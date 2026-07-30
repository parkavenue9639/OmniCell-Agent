import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";

import {
  approveMemory,
  cancelRun,
  correctMemory,
  createMemory,
  createConversation,
  decideReview,
  downloadArtifact,
  forgetMemory,
  getAllConversationHistory,
  getConversation,
  getMemorySettings,
  listAllArtifacts,
  listConversations,
  listAllMemories,
  listReviews,
  prepareRunSubmission,
  purgeMemory,
  replayAllRunEvents,
  submitRun,
  updateMemoryProviderConsent,
  updateMemorySettings,
  uploadArtifact,
  type Artifact,
  type Memory,
  type Run,
  type RunHistoryResponse,
} from "../api";
import {
  ConversationWorkspace,
  type ReviewDecision,
} from "../features/conversations";
import { useConnectionStore } from "../stores/connections";
import { useRunProjectionStore } from "../stores/run-projections";
import { parsePersistedEvent } from "../stream/event-validator";
import { buildConversationViewModel } from "./conversation-view-model";

const GLOBAL_SCOPE = "__global__";
const MEMORY_PROVIDER_CONSENT_VERSION = "memory-provider-v1";
export const MEMORY_COMMAND_MUTATION_KEY = ["memory", "command"] as const;

const queryKeys = {
  conversations: ["conversations"] as const,
  conversation: (id: string) => ["conversation", id] as const,
  history: (id: string) => ["conversation", id, "history"] as const,
  artifacts: (id: string) => ["conversation", id, "artifacts"] as const,
  artifactContent: (id: string) => ["artifact", id, "content"] as const,
  memorySettings: ["memory", "settings"] as const,
  memories: ["memory", "items"] as const,
  reviews: (id: string, runId: string) =>
    ["conversation", id, "run", runId, "reviews"] as const,
};

interface ScopedCommandError {
  readonly scope: string;
  readonly message: string;
}

type MemoryCommand =
  | {
      readonly kind: "setting";
      readonly setting: "generate_candidates" | "enable_agent_tools";
      readonly enabled: boolean;
    }
  | { readonly kind: "enable" }
  | { readonly kind: "disable" }
  | { readonly kind: "revoke_consent" }
  | {
      readonly kind: "create";
      readonly memoryKind:
        | "response_preference"
        | "profile_fact"
        | "project_context"
        | "scientific_observation";
      readonly stableKey?: string;
      readonly content: string;
      readonly datasetScope?: Readonly<Record<string, string>>;
    }
  | {
      readonly kind: "approve" | "forget" | "purge";
      readonly memoryId: string;
      readonly expectedVersion: number;
    }
  | {
      readonly kind: "correct";
      readonly memoryId: string;
      readonly expectedVersion: number;
      readonly content: string;
    };

interface MemoryCommandIdentity {
  readonly kind: MemoryCommand["kind"];
  readonly memoryId?: string;
}

interface MemoryCommandFailure extends MemoryCommandIdentity {
  readonly message: string;
}

function memoryCommandIdentity(command: MemoryCommand): MemoryCommandIdentity {
  return "memoryId" in command
    ? { kind: command.kind, memoryId: command.memoryId }
    : { kind: command.kind };
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

function scopeFor(conversationId: string | undefined): string {
  return conversationId ?? GLOBAL_SCOPE;
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

export function removeMemoryFromCachedList(
  memories: readonly Memory[] | undefined,
  memoryId: string,
): readonly Memory[] {
  return (memories ?? []).filter((memory) => memory.memory_id !== memoryId);
}

export function clearMemoryCommandMutationCache(queryClient: QueryClient): void {
  const mutationCache = queryClient.getMutationCache();
  for (const mutation of mutationCache.findAll({
    exact: true,
    mutationKey: MEMORY_COMMAND_MUTATION_KEY,
  })) {
    mutationCache.remove(mutation);
  }
}

export function eventRefreshesMemories(event: {
  readonly type: string;
}): boolean {
  return event.type === "memory.proposal_created";
}

export function selectCurrentRun(
  history: RunHistoryResponse | undefined,
): Run | undefined {
  if (history === undefined || history.order !== "newest_first") return undefined;
  return history.items.reduce<Run | undefined>((newest, candidate) => {
    if (newest === undefined) return candidate;
    const candidateTime = timestamp(candidate.created_at);
    const newestTime = timestamp(newest.created_at);
    if (candidateTime !== newestTime) {
      return candidateTime > newestTime ? candidate : newest;
    }
    return candidate.run_id.localeCompare(newest.run_id) > 0 ? candidate : newest;
  }, undefined);
}

function shouldPollForNewRun(history: RunHistoryResponse | undefined): boolean {
  const current = selectCurrentRun(history);
  return (
    current === undefined ||
    current.status === "completed" ||
    current.status === "failed" ||
    current.status === "cancelled"
  );
}

function extensionFor(mediaType: string | null | undefined): string {
  const extensions: Record<string, string> = {
    "application/json": ".json",
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/plain": ".txt",
  };
  return mediaType ? (extensions[mediaType] ?? "") : "";
}

function artifactFileName(artifact: Artifact): string {
  const metadataName = artifact.metadata?.filename;
  const requested =
    typeof metadataName === "string" && metadataName.trim()
      ? metadataName.trim()
      : `${artifact.kind}-${artifact.artifact_id.slice(0, 8)}${extensionFor(artifact.media_type)}`;
  const leaf = requested.split(/[\\/]/).at(-1) ?? "artifact";
  const safe = leaf.replace(/[\u0000-\u001f<>:"|?*]/g, "_").trim();
  return safe && safe !== "." && safe !== ".." ? safe : "artifact";
}

function saveBlob(blob: Blob, fileName: string): void {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = fileName;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}

export function ConversationRoute() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const createInFlight = useRef(false);
  const runInFlight = useRef(false);
  const cancelInFlight = useRef(false);
  const reviewInFlight = useRef(false);
  const uploadInFlight = useRef(false);
  const downloadInFlight = useRef(false);
  const memoryCommandInFlight = useRef(false);
  const [selectedDatasets, setSelectedDatasets] = useState<
    Readonly<Record<string, string>>
  >({});
  const [commandError, setCommandError] = useState<ScopedCommandError>();
  const [memoryCommandError, setMemoryCommandError] =
    useState<MemoryCommandFailure>();
  const [projectionError, setProjectionError] = useState<string>();

  const conversationsQuery = useQuery({
    queryKey: queryKeys.conversations,
    queryFn: () => listConversations({ limit: 100 }),
  });
  const memorySettingsQuery = useQuery({
    queryKey: queryKeys.memorySettings,
    queryFn: () => getMemorySettings(),
    retry: false,
  });
  const memoriesQuery = useQuery({
    queryKey: queryKeys.memories,
    queryFn: () => listAllMemories(),
    retry: false,
  });
  const conversationQuery = useQuery({
    queryKey: queryKeys.conversation(conversationId ?? "none"),
    queryFn: () => getConversation(conversationId!),
    enabled: conversationId !== undefined,
  });
  const historyQuery = useQuery({
    queryKey: queryKeys.history(conversationId ?? "none"),
    queryFn: () => getAllConversationHistory(conversationId!),
    enabled: conversationId !== undefined,
    // A run can also be started by the API or a local experiment process. Keep
    // the selected conversation in sync so the event stream can attach to that
    // new run without requiring a manual page refresh.
    refetchInterval: (query) =>
      shouldPollForNewRun(query.state.data) ? 2_000 : false,
    refetchIntervalInBackground: true,
  });
  const artifactsQuery = useQuery({
    queryKey: queryKeys.artifacts(conversationId ?? "none"),
    queryFn: () => listAllArtifacts(conversationId!),
    enabled: conversationId !== undefined,
  });
  const currentRun = selectCurrentRun(historyQuery.data);
  const reviewsQuery = useQuery({
    queryKey: queryKeys.reviews(
      conversationId ?? "none",
      currentRun?.run_id ?? "none",
    ),
    queryFn: () =>
      listReviews(conversationId!, {
        limit: 100,
        run_id: currentRun!.run_id,
      }),
    enabled: conversationId !== undefined && currentRun !== undefined,
  });

  const datasetIds = useMemo(
    () =>
      new Set(
        (artifactsQuery.data?.items ?? [])
          .filter((artifact) => artifact.kind === "dataset")
          .map((artifact) => artifact.artifact_id),
      ),
    [artifactsQuery.data],
  );
  const explicitDatasetId = conversationId
    ? selectedDatasets[conversationId]
    : undefined;
  const boundDatasetId = conversationQuery.data?.dataset_artifact_id ?? undefined;
  const selectedDatasetId =
    explicitDatasetId && datasetIds.has(explicitDatasetId)
      ? explicitDatasetId
      : boundDatasetId;
  const visibleCommandError =
    commandError?.scope === scopeFor(conversationId)
      ? commandError.message
      : undefined;

  const clearCommandError = (scope: string) => {
    setCommandError((current) =>
      current?.scope === scope ? undefined : current,
    );
  };
  const recordCommandError = (scope: string, error: unknown) => {
    setCommandError({ scope, message: message(error) });
  };

  const projectionsByRunId = useRunProjectionStore((state) => state.byRunId);
  const orderedRuns = useMemo(
    () =>
      [...(historyQuery.data?.items ?? [])].sort((left, right) => {
        const byTime = timestamp(left.created_at) - timestamp(right.created_at);
        return byTime || left.run_id.localeCompare(right.run_id);
      }),
    [historyQuery.data],
  );
  const projections = useMemo(
    () =>
      orderedRuns
        .map((run) => projectionsByRunId[run.run_id])
        .filter((value): value is NonNullable<typeof value> => value !== undefined),
    [orderedRuns, projectionsByRunId],
  );
  const projection = currentRun
    ? projectionsByRunId[currentRun.run_id]
    : undefined;
  const connection = useConnectionStore((state) =>
    currentRun ? state.byRunId[currentRun.run_id] : undefined,
  );
  const projectionHydrating =
    projectionError === undefined &&
    historyQuery.data !== undefined &&
    orderedRuns.some((run) => projectionsByRunId[run.run_id] === undefined);

  useEffect(() => {
    if (conversationId === undefined) return;
    const controller = new AbortController();
    const connectionStore = useConnectionStore.getState();
    setProjectionError(undefined);

    void (async () => {
      let replayRequiresMemoryRefresh = false;
      for (const run of orderedRuns) {
        const replay = await replayAllRunEvents(run.run_id, {
          signal: controller.signal,
        });
        if (
          replay.run_id !== run.run_id ||
          replay.conversation_id !== conversationId
        ) {
          throw new Error("历史事件身份与 conversation 不一致");
        }
        const replayEvents = replay.events.map((event) =>
          parsePersistedEvent(event),
        );
        replayRequiresMemoryRefresh ||= replayEvents.some(
          eventRefreshesMemories,
        );
        const result = useRunProjectionStore.getState().hydrateRun(
          run.run_id,
          conversationId,
          replayEvents,
          run.last_sequence,
        );
        if (result.kind === "gap" || result.kind === "conflict") {
          throw new Error(result.message);
        }
      }
      if (replayRequiresMemoryRefresh) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.memories,
        });
      }
      if (currentRun === undefined) return;
      const { followRunEvents } = await import("../stream/reconnect-policy");
      await followRunEvents({
          baseUrl: window.location.origin,
          runId: currentRun.run_id,
          conversationId,
          signal: controller.signal,
          getAppliedSequence: () =>
            useRunProjectionStore.getState().byRunId[currentRun.run_id]
              ?.appliedSequence ?? "0",
          isTerminal: () =>
            useRunProjectionStore.getState().byRunId[currentRun.run_id]
              ?.terminalStatus !== null,
          onEvent: (event) => {
            const result = useRunProjectionStore.getState().applyEvent(event);
            if (result.kind === "gap" || result.kind === "conflict") {
              throw new Error(
                result.kind === "gap"
                  ? `事件序列存在缺口：${result.expectedSequence}`
                  : result.message,
              );
            }
            if (event.type === "artifact.created") {
              void queryClient.invalidateQueries({
                queryKey: queryKeys.artifacts(conversationId),
              });
            }
            if (eventRefreshesMemories(event)) {
              void queryClient.invalidateQueries({
                queryKey: queryKeys.memories,
              });
            }
            if (event.type === "run.created" || event.type === "run.started") {
              void Promise.all([
                queryClient.invalidateQueries({
                  queryKey: queryKeys.conversations,
                }),
                queryClient.invalidateQueries({
                  queryKey: queryKeys.conversation(conversationId),
                }),
              ]);
            }
            if (
              event.type === "review.requested" ||
              event.type === "review.resolved"
            ) {
              void queryClient.invalidateQueries({
                queryKey: queryKeys.reviews(conversationId, currentRun.run_id),
              });
            }
            if (
              event.type === "run.completed" ||
              event.type === "run.failed" ||
              event.type === "run.cancelled"
            ) {
              void queryClient.invalidateQueries({
                queryKey: queryKeys.history(conversationId),
              });
            }
          },
          onPhaseChange: (phase, error) =>
            connectionStore.setPhase(currentRun.run_id, phase, error),
        });
    })().catch((error: unknown) => {
        if (!controller.signal.aborted) {
          const rendered = message(error);
          setProjectionError(rendered);
          if (currentRun !== undefined) {
            connectionStore.setPhase(currentRun.run_id, "closed", error);
          }
        }
      });
    return () => controller.abort();
  }, [
    conversationId,
    currentRun?.run_id,
    orderedRuns
      .map((run) => `${run.run_id}:${run.last_sequence}`)
      .join(","),
    queryClient,
  ]);

  const createConversationMutation = useMutation({
    mutationFn: (_: { errorScope: string }) =>
      createConversation({}),
    onMutate: ({ errorScope }) => clearCommandError(errorScope),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.conversations });
      navigate(`/conversation/${created.conversation_id}`);
    },
    onError: (error, { errorScope }) => recordCommandError(errorScope, error),
    onSettled: () => {
      createInFlight.current = false;
    },
  });
  const runMutation = useMutation({
    mutationFn: ({
      targetConversationId,
      goal,
      inputArtifactIds,
      memoryMode,
      selectedMemories,
    }: {
      targetConversationId: string;
      goal: string;
      inputArtifactIds: readonly string[];
      memoryMode: "off" | "default" | "selected";
      selectedMemories: readonly {
        readonly itemId: string;
        readonly versionId: string;
      }[];
    }) =>
      submitRun(
        prepareRunSubmission(targetConversationId, {
          goal,
          input_artifact_ids: [...inputArtifactIds],
          memory_mode: memoryMode,
          selected_memories: selectedMemories.map((memory) => ({
            item_id: memory.itemId,
            version_id: memory.versionId,
          })),
        }),
      ),
    onMutate: ({ targetConversationId }) =>
      clearCommandError(targetConversationId),
    onSuccess: async (_, { targetConversationId }) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.conversations,
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.conversation(targetConversationId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.history(targetConversationId),
        }),
      ]);
    },
    onError: (error, { targetConversationId }) =>
      recordCommandError(targetConversationId, error),
  });
  const cancelMutation = useMutation({
    mutationFn: ({ runId }: { targetConversationId: string; runId: string }) =>
      cancelRun(runId, { reason: "用户从 Web 界面取消" }),
    onMutate: ({ targetConversationId }) =>
      clearCommandError(targetConversationId),
    onSuccess: async (_, { targetConversationId }) => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.history(targetConversationId),
      });
    },
    onError: (error, { targetConversationId }) =>
      recordCommandError(targetConversationId, error),
    onSettled: () => {
      cancelInFlight.current = false;
    },
  });
  const reviewMutation = useMutation({
    mutationFn: ({
      reviewId,
      decision,
      comment,
    }: {
      targetConversationId: string;
      runId: string;
      reviewId: string;
      decision: ReviewDecision;
      comment?: string;
    }) => decideReview(reviewId, { decision, comment }),
    onMutate: ({ targetConversationId }) =>
      clearCommandError(targetConversationId),
    onSuccess: async (_, { targetConversationId, runId }) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.reviews(targetConversationId, runId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.history(targetConversationId),
        }),
      ]);
    },
    onError: (error, { targetConversationId }) =>
      recordCommandError(targetConversationId, error),
    onSettled: () => {
      reviewInFlight.current = false;
    },
  });
  const uploadMutation = useMutation({
    mutationFn: ({
      targetConversationId,
      file,
    }: {
      targetConversationId: string;
      file: File;
    }) => uploadArtifact(targetConversationId, { file, kind: "dataset" }),
    onMutate: ({ targetConversationId }) =>
      clearCommandError(targetConversationId),
    onSuccess: async (artifact, { targetConversationId }) => {
      setSelectedDatasets((current) => ({
        ...current,
        [targetConversationId]: artifact.artifact_id,
      }));
      await queryClient.invalidateQueries({
        queryKey: queryKeys.artifacts(targetConversationId),
      });
    },
    onError: (error, { targetConversationId }) =>
      recordCommandError(targetConversationId, error),
    onSettled: () => {
      uploadInFlight.current = false;
    },
  });
  const downloadMutation = useMutation({
    mutationFn: async ({ artifact }: { scope: string; artifact: Artifact }) => ({
      artifact,
      blob: await downloadArtifact(artifact.artifact_id),
    }),
    onMutate: ({ scope }) => clearCommandError(scope),
    onSuccess: ({ artifact, blob }) => saveBlob(blob, artifactFileName(artifact)),
    onError: (error, { scope }) => recordCommandError(scope, error),
    onSettled: () => {
      downloadInFlight.current = false;
    },
  });
  const memoryMutation = useMutation({
    mutationKey: MEMORY_COMMAND_MUTATION_KEY,
    // Memory command variables and responses can contain plaintext. Retain
    // them for no longer than the active mutation itself.
    gcTime: 0,
    mutationFn: async (command: MemoryCommand) => {
      switch (command.kind) {
        case "setting":
          return updateMemorySettings({
            [command.setting]: command.enabled,
          });
        case "enable":
          if (!memorySettingsQuery.data?.provider_consent_granted) {
            await updateMemoryProviderConsent({
              decision: "grant",
              statement_version: MEMORY_PROVIDER_CONSENT_VERSION,
              confirmed: true,
            });
          }
          return updateMemorySettings({
            use_memory: true,
            generate_candidates: true,
            enable_agent_tools: true,
          });
        case "disable":
          return updateMemorySettings({
            use_memory: false,
            generate_candidates: false,
            enable_agent_tools: false,
          });
        case "revoke_consent":
          if (
            memorySettingsQuery.data?.use_memory ||
            memorySettingsQuery.data?.generate_candidates ||
            memorySettingsQuery.data?.enable_agent_tools
          ) {
            await updateMemorySettings({
              use_memory: false,
              generate_candidates: false,
              enable_agent_tools: false,
            });
          }
          return updateMemoryProviderConsent({
            decision: "revoke",
            statement_version: MEMORY_PROVIDER_CONSENT_VERSION,
            confirmed: true,
          });
        case "create":
          return createMemory({
            kind: command.memoryKind,
            stable_key: command.stableKey,
            content: command.content,
            dataset_scope: command.datasetScope
              ? { ...command.datasetScope }
              : undefined,
            source_message_ids: [],
          });
        case "approve":
          return approveMemory(command.memoryId, {
            expected_version: command.expectedVersion,
          });
        case "correct":
          return correctMemory(command.memoryId, {
            expected_version: command.expectedVersion,
            content: command.content,
            source_message_ids: [],
          });
        case "forget":
          return forgetMemory(command.memoryId, {
            expected_version: command.expectedVersion,
            confirmed: true,
          });
        case "purge":
          return purgeMemory(command.memoryId, {
            expected_version: command.expectedVersion,
            confirmed: true,
          });
      }
    },
    onMutate: () => setMemoryCommandError(undefined),
    onSuccess: async (_, command) => {
      if (command.kind === "purge") {
        // Unmount the purged item before refetching. Filtering, rather than
        // invalidation alone, guarantees its old body and the card's local
        // correction draft disappear synchronously from the mounted UI.
        queryClient.setQueryData<readonly Memory[]>(
          queryKeys.memories,
          (current) => removeMemoryFromCachedList(current, command.memoryId),
        );
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.memorySettings }),
        queryClient.invalidateQueries({ queryKey: queryKeys.memories }),
      ]);
    },
    onError: async (error, command) => {
      setMemoryCommandError({
        ...memoryCommandIdentity(command),
        message: message(error),
      });
      // A compound command can fail after an earlier step succeeded (for
      // example consent grant followed by setting update). Re-read both
      // authorities before another Run or memory command is allowed.
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.memorySettings,
          refetchType: "active",
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.memories,
          refetchType: "active",
        }),
      ]);
    },
  });

  const runMemoryCommand = async (
    command: MemoryCommand,
  ): Promise<boolean> => {
    if (memoryCommandInFlight.current || runInFlight.current) {
      return false;
    }
    memoryCommandInFlight.current = true;
    try {
      await memoryMutation.mutateAsync(command);
      return true;
    } catch {
      return false;
    } finally {
      // Reset first so the hook observer no longer owns the completed
      // mutation, then remove every same-key entry synchronously. This keeps
      // create/correct plaintext out of MutationCache and makes purge's
      // completion boundary cover all frontend JS caches under our control.
      memoryMutation.reset();
      clearMemoryCommandMutationCache(queryClient);
      memoryCommandInFlight.current = false;
    }
  };

  const loading =
    conversationsQuery.isPending ||
    (conversationId !== undefined &&
      (conversationQuery.isPending ||
        historyQuery.isPending ||
        artifactsQuery.isPending ||
        projectionHydrating));
  const queryError =
    conversationsQuery.error ??
    conversationQuery.error ??
    historyQuery.error ??
    artifactsQuery.error ??
    reviewsQuery.error ??
    (projectionError ? new Error(projectionError) : undefined);
  const pendingMemoryCommand =
    memoryMutation.isPending && memoryMutation.variables
      ? {
          ...memoryCommandIdentity(memoryMutation.variables),
          pending: true,
        }
      : undefined;
  const visibleMemoryCommand = pendingMemoryCommand
    ? pendingMemoryCommand
    : memoryCommandError
      ? {
          kind: memoryCommandError.kind,
          memoryId: memoryCommandError.memoryId,
          pending: false,
          errorMessage: memoryCommandError.message,
        }
      : undefined;
  const model = useMemo(
    () =>
      buildConversationViewModel({
        loading,
        errorMessage: queryError ? message(queryError) : undefined,
        commandErrorMessage: visibleCommandError,
        conversations: conversationsQuery.data?.items ?? [],
        selectedConversation: conversationQuery.data,
        selectedDatasetId,
        artifacts: artifactsQuery.data?.items ?? [],
        reviews: reviewsQuery.data?.items ?? [],
        runs: orderedRuns,
        run: currentRun,
        projections,
        projection,
        connection,
        pending: {
          createConversation: createConversationMutation.isPending,
          uploadDataset: uploadMutation.isPending,
          submitRun: runMutation.isPending,
          cancelRun: cancelMutation.isPending,
          reviewId: reviewMutation.isPending
            ? reviewMutation.variables?.reviewId
            : undefined,
          artifactId: downloadMutation.isPending
            ? downloadMutation.variables?.artifact.artifact_id
            : undefined,
        },
        memory: {
          settings: memorySettingsQuery.data,
          items: memoriesQuery.data ?? [],
          loading:
            memorySettingsQuery.isPending || memoriesQuery.isPending,
          errorMessage: memorySettingsQuery.error
            ? message(memorySettingsQuery.error)
            : memoriesQuery.error
              ? message(memoriesQuery.error)
              : undefined,
          commandErrorMessage: memoryCommandError?.message,
          commandsPending: memoryMutation.isPending,
          command: visibleMemoryCommand,
        },
      }),
    [
      loading,
      queryError,
      visibleCommandError,
      conversationsQuery.data,
      conversationQuery.data,
      selectedDatasetId,
      artifactsQuery.data,
      reviewsQuery.data,
      orderedRuns,
      currentRun,
      projections,
      projection,
      connection,
      createConversationMutation.isPending,
      uploadMutation.isPending,
      runMutation.isPending,
      cancelMutation.isPending,
      reviewMutation.isPending,
      reviewMutation.variables,
      downloadMutation.isPending,
      downloadMutation.variables,
      memorySettingsQuery.data,
      memorySettingsQuery.isPending,
      memorySettingsQuery.error,
      memoriesQuery.data,
      memoriesQuery.isPending,
      memoriesQuery.error,
      memoryCommandError,
      memoryMutation.isPending,
      visibleMemoryCommand,
    ],
  );

  return (
    <>
      <input
        ref={fileInput}
        hidden
        type="file"
        accept=".h5ad,.csv,.tsv,application/octet-stream,text/csv"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          const targetConversationId = conversationId;
          if (file && targetConversationId && !uploadInFlight.current) {
            uploadInFlight.current = true;
            uploadMutation.mutate({ targetConversationId, file });
          }
          event.currentTarget.value = "";
        }}
      />
      <ConversationWorkspace
        model={model}
        actions={{
          onCreateConversation: () => {
            if (!createInFlight.current) {
              createInFlight.current = true;
              createConversationMutation.mutate({
                errorScope: scopeFor(conversationId),
              });
            }
          },
          onSelectConversation: (id) => navigate(`/conversation/${id}`),
          onSelectDataset: (artifactId) => {
            if (conversationId && datasetIds.has(artifactId)) {
              setSelectedDatasets((current) => ({
                ...current,
                [conversationId]: artifactId,
              }));
            }
          },
          onImportDataset: () => {
            if (conversationId && !uploadInFlight.current) {
              fileInput.current?.click();
            }
          },
          onRetry: () => {
            clearCommandError(scopeFor(conversationId));
            void queryClient.invalidateQueries();
          },
          onSubmit: async (goal, memory) => {
            if (
              conversationId === undefined ||
              runInFlight.current ||
              memoryCommandInFlight.current ||
              memoryMutation.isPending
            ) {
              return false;
            }
            runInFlight.current = true;
            const inputArtifactIds = selectedDatasetId ? [selectedDatasetId] : [];
            try {
              await runMutation.mutateAsync({
                targetConversationId: conversationId,
                goal,
                inputArtifactIds,
                memoryMode: memory.mode,
                selectedMemories: memory.refs,
              });
              return true;
            } catch {
              return false;
            } finally {
              runInFlight.current = false;
            }
          },
          onCancelRun: (runId) => {
            if (conversationId && !cancelInFlight.current) {
              cancelInFlight.current = true;
              cancelMutation.mutate({
                targetConversationId: conversationId,
                runId,
              });
            }
          },
          onReviewDecision: (reviewId, decision, comment) => {
            if (conversationId && currentRun && !reviewInFlight.current) {
              reviewInFlight.current = true;
              reviewMutation.mutate({
                targetConversationId: conversationId,
                runId: currentRun.run_id,
                reviewId,
                decision,
                comment,
              });
            }
          },
          onDownloadArtifact: (artifactId) => {
            if (downloadInFlight.current) return;
            const artifact = artifactsQuery.data?.items.find(
              (candidate) => candidate.artifact_id === artifactId,
            );
            if (artifact) {
              downloadInFlight.current = true;
              downloadMutation.mutate({
                scope: scopeFor(conversationId),
                artifact,
              });
            }
          },
          onLoadArtifactContent: (artifactId) =>
            queryClient.fetchQuery({
              queryKey: queryKeys.artifactContent(artifactId),
              queryFn: () => downloadArtifact(artifactId),
              staleTime: Number.POSITIVE_INFINITY,
            }),
          onUpdateMemorySetting: (setting, enabled) =>
            runMemoryCommand({
              kind: "setting",
              setting:
                setting === "generateCandidates"
                  ? "generate_candidates"
                  : "enable_agent_tools",
              enabled,
            }),
          onGrantMemoryConsentAndEnable: () =>
            runMemoryCommand({ kind: "enable" }),
          onDisableMemory: () => runMemoryCommand({ kind: "disable" }),
          onRevokeMemoryConsent: () =>
            runMemoryCommand({ kind: "revoke_consent" }),
          onCreateMemory: (input) =>
            runMemoryCommand({
              kind: "create",
              memoryKind: input.kind,
              stableKey: input.stableKey,
              content: input.content,
              datasetScope: input.datasetScope,
            }),
          onApproveMemory: (memoryId, expectedVersion) =>
            runMemoryCommand({
              kind: "approve",
              memoryId,
              expectedVersion,
            }),
          onCorrectMemory: (memoryId, expectedVersion, content) =>
            runMemoryCommand({
              kind: "correct",
              memoryId,
              expectedVersion,
              content,
            }),
          onForgetMemory: (memoryId, expectedVersion) =>
            runMemoryCommand({
              kind: "forget",
              memoryId,
              expectedVersion,
            }),
          onPurgeMemory: (memoryId, expectedVersion) =>
            runMemoryCommand({
              kind: "purge",
              memoryId,
              expectedVersion,
            }),
        }}
      />
    </>
  );
}
