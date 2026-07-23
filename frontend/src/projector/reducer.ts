import type { PersistedEvent } from "../generated/events-v1";
import type {
  ArtifactProjection,
  CapabilityProjection,
  ReviewProjection,
  RuntimeCommandProjection,
  RunProjection,
  SkillLoadProjection,
  TaskProjection,
} from "./model";
import { compareSequence, nextSequence } from "./sequence";

export type ProjectionResult =
  | { readonly kind: "applied"; readonly state: RunProjection }
  | { readonly kind: "duplicate"; readonly state: RunProjection }
  | {
      readonly kind: "gap";
      readonly state: RunProjection;
      readonly expectedSequence: string;
      readonly receivedSequence: string;
    }
  | {
      readonly kind: "conflict";
      readonly state: RunProjection;
      readonly message: string;
    };

function sameJsonValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => sameJsonValue(value, right[index]))
    );
  }
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object"
  ) {
    return false;
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        sameJsonValue(leftRecord[key], rightRecord[key]),
    )
  );
}

function isExactDuplicate(
  state: RunProjection,
  event: PersistedEvent,
): boolean {
  const existing = state.events.find(
    (candidate) => candidate.event_id === event.event_id,
  );
  return existing !== undefined && sameJsonValue(existing, event);
}

function eventIdentityConflict(
  state: RunProjection,
  event: PersistedEvent,
): ProjectionResult | null {
  const sequenceForIdentity = state.appliedEventIds[event.event_id];
  if (sequenceForIdentity !== undefined) {
    if (
      sequenceForIdentity === event.sequence &&
      isExactDuplicate(state, event)
    ) {
      return { kind: "duplicate", state };
    }
    return {
      kind: "conflict",
      state,
      message: `事件 ${event.event_id} 对应多个 sequence`,
    };
  }

  const comparison = compareSequence(event.sequence, state.appliedSequence);
  if (comparison <= 0) {
    return {
      kind: "conflict",
      state,
      message: `sequence ${event.sequence} 已被其他事件占用`,
    };
  }
  const expected = nextSequence(state.appliedSequence);
  if (event.sequence !== expected) {
    return {
      kind: "gap",
      state,
      expectedSequence: expected,
      receivedSequence: event.sequence,
    };
  }
  return null;
}

function closeOpenActivities(
  state: RunProjection,
  status: "failed" | "cancelled",
): Pick<
  RunProjection,
  "runtimeCommands" | "skillLoads" | "capabilities" | "tasks"
> {
  return {
    runtimeCommands: Object.fromEntries(
      Object.entries(state.runtimeCommands).map(([id, runtime]) => [
        id,
        runtime.status === "running" ? { ...runtime, status } : runtime,
      ]),
    ),
    skillLoads: Object.fromEntries(
      Object.entries(state.skillLoads).map(([id, skillLoad]) => [
        id,
        skillLoad.status === "running" ? { ...skillLoad, status } : skillLoad,
      ]),
    ),
    capabilities: Object.fromEntries(
      Object.entries(state.capabilities).map(([id, capability]) => [
        id,
        capability.status === "running" || capability.status === "retrying"
          ? { ...capability, status }
          : capability,
      ]),
    ),
    tasks: Object.fromEntries(
      Object.entries(state.tasks).map(([id, task]) => [
        id,
        task.status === "completed" ||
        task.status === "failed" ||
        task.status === "cancelled"
          ? task
          : { ...task, status },
      ]),
    ),
  };
}

function reduceKnownEvent(
  state: RunProjection,
  event: PersistedEvent,
): RunProjection {
  switch (event.type) {
    case "run.created":
      return { ...state, status: event.payload.status ?? "pending" };
    case "run.started":
      return {
        ...state,
        status: event.payload.status ?? "running",
        stopReason: null,
      };
    case "run.cancel_requested":
      return { ...state, status: event.payload.status ?? "cancelling" };
    case "run.interrupted":
      return {
        ...state,
        status: event.payload.status ?? "review_required",
        stopReason: event.payload.reason,
      };
    case "run.completed":
      return {
        ...state,
        status: event.payload.status ?? "completed",
        terminalStatus: "completed",
        stopReason: null,
      };
    case "run.failed":
      return {
        ...state,
        ...closeOpenActivities(state, "failed"),
        status: event.payload.status ?? "failed",
        terminalStatus: "failed",
        stopReason: event.payload.error_summary,
      };
    case "run.cancelled":
      return {
        ...state,
        ...closeOpenActivities(state, "cancelled"),
        status: event.payload.status ?? "cancelled",
        terminalStatus: "cancelled",
        stopReason: event.payload.reason ?? null,
      };
    case "message.completed":
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            eventId: event.event_id,
            messageId: event.payload.message_id,
            role: event.payload.role,
            content: event.payload.content,
            contentArtifactId: event.payload.content_artifact_id ?? null,
            occurredAt: event.occurred_at,
          },
        ],
      };
    case "task.created": {
      const task: TaskProjection = {
        taskId: event.payload.task_id,
        title: event.payload.title,
        description: event.payload.description ?? null,
        capabilityName: event.payload.capability_name ?? null,
        status: event.payload.status ?? "pending",
        summary: null,
      };
      return { ...state, tasks: { ...state.tasks, [task.taskId]: task } };
    }
    case "task.updated": {
      const existing = state.tasks[event.payload.task_id];
      if (existing === undefined) {
        return state;
      }
      const task: TaskProjection = {
        ...existing,
        status: event.payload.status,
        summary: event.payload.summary ?? null,
      };
      return { ...state, tasks: { ...state.tasks, [task.taskId]: task } };
    }
    case "skill.load_started": {
      const skillLoad: SkillLoadProjection = {
        skillLoadId: event.payload.skill_load_id,
        skillName: event.payload.skill_name,
        resourceKind: event.payload.resource_kind,
        resourceName: event.payload.resource_name ?? null,
        purpose: event.payload.purpose,
        status: "running",
        outcome: null,
        contentBytes: null,
        errorCode: null,
        errorSummary: null,
      };
      return {
        ...state,
        skillLoads: {
          ...state.skillLoads,
          [skillLoad.skillLoadId]: skillLoad,
        },
      };
    }
    case "skill.load_completed": {
      const existing = state.skillLoads[event.payload.skill_load_id];
      if (existing === undefined) return state;
      const skillLoad: SkillLoadProjection = {
        ...existing,
        status: "completed",
        outcome: event.payload.outcome,
        contentBytes: event.payload.content_bytes,
        errorCode: null,
        errorSummary: null,
      };
      return {
        ...state,
        skillLoads: {
          ...state.skillLoads,
          [skillLoad.skillLoadId]: skillLoad,
        },
      };
    }
    case "skill.load_failed": {
      const existing = state.skillLoads[event.payload.skill_load_id];
      if (existing === undefined) return state;
      const skillLoad: SkillLoadProjection = {
        ...existing,
        status: "failed",
        outcome: null,
        contentBytes: null,
        errorCode: event.payload.error_code,
        errorSummary: event.payload.error_summary,
      };
      return {
        ...state,
        skillLoads: {
          ...state.skillLoads,
          [skillLoad.skillLoadId]: skillLoad,
        },
      };
    }
    case "capability.started": {
      const capability: CapabilityProjection = {
        capabilityCallId: event.payload.capability_call_id,
        capabilityName: event.payload.capability_name,
        taskId: event.payload.task_id ?? null,
        status: "running",
        attempt: event.payload.attempt ?? 1,
        summary: null,
        errorSummary: null,
        artifactIds: [],
        progressCurrent: null,
        progressTotal: null,
        progressMessage: null,
      };
      return {
        ...state,
        capabilities: {
          ...state.capabilities,
          [capability.capabilityCallId]: capability,
        },
      };
    }
    case "capability.retrying": {
      const existing = state.capabilities[event.payload.capability_call_id];
      const capability: CapabilityProjection = {
        capabilityCallId: event.payload.capability_call_id,
        capabilityName: event.payload.capability_name,
        taskId: existing?.taskId ?? null,
        status: "retrying",
        attempt: event.payload.next_attempt,
        summary: event.payload.reason,
        errorSummary: null,
        artifactIds: existing?.artifactIds ?? [],
        progressCurrent: existing?.progressCurrent ?? null,
        progressTotal: existing?.progressTotal ?? null,
        progressMessage: existing?.progressMessage ?? null,
      };
      return {
        ...state,
        capabilities: {
          ...state.capabilities,
          [capability.capabilityCallId]: capability,
        },
      };
    }
    case "capability.completed": {
      const existing = state.capabilities[event.payload.capability_call_id];
      const capability: CapabilityProjection = {
        capabilityCallId: event.payload.capability_call_id,
        capabilityName: event.payload.capability_name,
        taskId: event.payload.task_id ?? null,
        status: event.payload.result_status === "aborted" ? "failed" : "completed",
        attempt: existing?.attempt ?? 1,
        summary: event.payload.summary ?? null,
        errorSummary: null,
        artifactIds: event.payload.artifact_ids ?? [],
        progressCurrent: existing?.progressCurrent ?? null,
        progressTotal: existing?.progressTotal ?? null,
        progressMessage: existing?.progressMessage ?? null,
      };
      return {
        ...state,
        capabilities: {
          ...state.capabilities,
          [capability.capabilityCallId]: capability,
        },
      };
    }
    case "capability.failed": {
      const existing = state.capabilities[event.payload.capability_call_id];
      const capability: CapabilityProjection = {
        capabilityCallId: event.payload.capability_call_id,
        capabilityName: event.payload.capability_name,
        taskId: event.payload.task_id ?? null,
        status: "failed",
        attempt: existing?.attempt ?? 1,
        summary: null,
        errorSummary: event.payload.error_summary,
        artifactIds: existing?.artifactIds ?? [],
        progressCurrent: existing?.progressCurrent ?? null,
        progressTotal: existing?.progressTotal ?? null,
        progressMessage: existing?.progressMessage ?? null,
      };
      return {
        ...state,
        capabilities: {
          ...state.capabilities,
          [capability.capabilityCallId]: capability,
        },
      };
    }
    case "capability.progress": {
      const existing = state.capabilities[event.payload.capability_call_id];
      if (existing === undefined) return state;
      const capability: CapabilityProjection = {
        ...existing,
        status: "running",
        attempt: event.payload.attempt ?? 1,
        progressCurrent: event.payload.current,
        progressTotal: event.payload.total ?? null,
        progressMessage: event.payload.message,
      };
      return {
        ...state,
        capabilities: {
          ...state.capabilities,
          [capability.capabilityCallId]: capability,
        },
      };
    }
    case "runtime.command_started": {
      const runtime: RuntimeCommandProjection = {
        runtimeCommandId: event.payload.runtime_command_id,
        capabilityCallId: event.payload.capability_call_id,
        capabilityName: event.payload.capability_name,
        attempt: event.payload.attempt ?? 1,
        backend: event.payload.backend,
        command: event.payload.command,
        code: event.payload.code ?? null,
        workdir: event.payload.workdir,
        status: "running",
        stdout: "",
        stderr: "",
        exitCode: null,
        durationMs: null,
        commandTruncated: event.payload.command_truncated ?? false,
        stdoutTruncated: false,
        stderrTruncated: false,
        redacted: event.payload.redacted ?? false,
      };
      return {
        ...state,
        runtimeCommands: {
          ...state.runtimeCommands,
          [runtime.runtimeCommandId]: runtime,
        },
      };
    }
    case "runtime.output": {
      const existing = state.runtimeCommands[event.payload.runtime_command_id];
      if (existing === undefined) return state;
      const runtime: RuntimeCommandProjection = {
        ...existing,
        [event.payload.stream]:
          existing[event.payload.stream] + event.payload.chunk,
        redacted: existing.redacted || (event.payload.redacted ?? false),
        stdoutTruncated:
          existing.stdoutTruncated ||
          (event.payload.stream === "stdout" &&
            (event.payload.truncated ?? false)),
        stderrTruncated:
          existing.stderrTruncated ||
          (event.payload.stream === "stderr" &&
            (event.payload.truncated ?? false)),
      };
      return {
        ...state,
        runtimeCommands: {
          ...state.runtimeCommands,
          [runtime.runtimeCommandId]: runtime,
        },
      };
    }
    case "runtime.command_completed": {
      const existing = state.runtimeCommands[event.payload.runtime_command_id];
      if (existing === undefined) return state;
      const runtime: RuntimeCommandProjection = {
        ...existing,
        status: event.payload.outcome,
        exitCode: event.payload.exit_code ?? null,
        durationMs: event.payload.duration_ms,
        stdoutTruncated: event.payload.stdout_truncated ?? false,
        stderrTruncated: event.payload.stderr_truncated ?? false,
        redacted: existing.redacted || (event.payload.redacted ?? false),
      };
      return {
        ...state,
        runtimeCommands: {
          ...state.runtimeCommands,
          [runtime.runtimeCommandId]: runtime,
        },
      };
    }
    case "review.requested": {
      const review: ReviewProjection = {
        reviewId: event.payload.review_id,
        taskId: event.payload.task_id ?? null,
        prompt: event.payload.prompt,
        status: event.payload.status ?? "pending",
        decision: null,
        comment: null,
      };
      return { ...state, reviews: { ...state.reviews, [review.reviewId]: review } };
    }
    case "review.resolved": {
      const existing = state.reviews[event.payload.review_id];
      const review: ReviewProjection = {
        reviewId: event.payload.review_id,
        taskId: existing?.taskId ?? null,
        prompt: existing?.prompt ?? "",
        status: event.payload.status,
        decision: event.payload.decision ?? null,
        comment: event.payload.comment ?? null,
      };
      return { ...state, reviews: { ...state.reviews, [review.reviewId]: review } };
    }
    case "artifact.created": {
      const artifact: ArtifactProjection = {
        artifactId: event.payload.artifact_id,
        kind: event.payload.kind,
        mediaType: event.payload.media_type ?? null,
        sizeBytes: event.payload.size_bytes,
        sha256: event.payload.sha256,
      };
      return {
        ...state,
        artifacts: { ...state.artifacts, [artifact.artifactId]: artifact },
      };
    }
    case "agent.turn_started":
    case "budget.exhausted":
      return state;
  }
  return state;
}

export function projectPersistedEvent(
  state: RunProjection,
  event: PersistedEvent,
): ProjectionResult {
  if (event.type === undefined) {
    return {
      kind: "conflict",
      state,
      message: "持久化事件缺少 type discriminator",
    };
  }
  if (event.run_id !== state.runId || event.conversation_id !== state.conversationId) {
    return {
      kind: "conflict",
      state,
      message: "事件身份与当前 run 投影不一致",
    };
  }

  if (isExactDuplicate(state, event)) {
    return { kind: "duplicate", state };
  }
  if (state.terminalStatus !== null) {
    return {
      kind: "conflict",
      state,
      message: `run 已进入终态 ${state.terminalStatus}，不能继续应用事件 ${event.event_id}`,
    };
  }

  const identityResult = eventIdentityConflict(state, event);
  if (identityResult !== null) {
    return identityResult;
  }

  const reduced = reduceKnownEvent(state, event);
  return {
    kind: "applied",
    state: {
      ...reduced,
      appliedSequence: event.sequence,
      appliedEventIds: {
        ...reduced.appliedEventIds,
        [event.event_id]: event.sequence,
      },
      events: [...reduced.events, event],
    },
  };
}
