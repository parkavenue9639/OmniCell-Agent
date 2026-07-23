import { create } from "zustand";

import type { PersistedEvent } from "../generated/events-v1";
import { emptyRunProjection, type RunProjection } from "../projector/model";
import {
  projectPersistedEvent,
  type ProjectionResult,
} from "../projector/reducer";
import { compareSequence } from "../projector/sequence";

export interface ProjectionIssue {
  readonly kind: "gap" | "conflict";
  readonly message: string;
}

export type HydrationResult =
  | { readonly kind: "hydrated"; readonly state: RunProjection }
  | { readonly kind: "preserved"; readonly state: RunProjection }
  | { readonly kind: "gap" | "conflict"; readonly message: string };

interface RunProjectionStore {
  readonly byRunId: Readonly<Record<string, RunProjection>>;
  readonly issueByRunId: Readonly<Record<string, ProjectionIssue | undefined>>;
  ensureRun: (runId: string, conversationId: string) => RunProjection;
  applyEvent: (event: PersistedEvent) => ProjectionResult;
  hydrateRun: (
    runId: string,
    conversationId: string,
    events: readonly PersistedEvent[],
    expectedLastSequence: string,
  ) => HydrationResult;
  clearRun: (runId: string) => void;
}

export const useRunProjectionStore = create<RunProjectionStore>((set, get) => ({
  byRunId: {},
  issueByRunId: {},
  ensureRun(runId, conversationId) {
    const existing = get().byRunId[runId];
    if (existing !== undefined) {
      return existing;
    }
    const created = emptyRunProjection(runId, conversationId);
    set((state) => ({ byRunId: { ...state.byRunId, [runId]: created } }));
    return created;
  },
  applyEvent(event) {
    const state = get();
    const current =
      state.byRunId[event.run_id] ??
      emptyRunProjection(event.run_id, event.conversation_id);
    const result = projectPersistedEvent(current, event);
    if (result.kind === "applied") {
      set((previous) => ({
        byRunId: { ...previous.byRunId, [event.run_id]: result.state },
        issueByRunId: { ...previous.issueByRunId, [event.run_id]: undefined },
      }));
    } else if (result.kind === "gap" || result.kind === "conflict") {
      const message =
        result.kind === "gap"
          ? `等待 sequence ${result.expectedSequence}，收到 ${result.receivedSequence}`
          : result.message;
      set((previous) => ({
        issueByRunId: {
          ...previous.issueByRunId,
          [event.run_id]: { kind: result.kind, message },
        },
      }));
    }
    return result;
  },
  hydrateRun(runId, conversationId, events, expectedLastSequence) {
    let candidate = emptyRunProjection(runId, conversationId);
    for (const event of events) {
      const result = projectPersistedEvent(candidate, event);
      if (result.kind === "gap" || result.kind === "conflict") {
        const message =
          result.kind === "gap"
            ? `历史重放缺少 sequence ${result.expectedSequence}`
            : result.message;
        set((previous) => ({
          issueByRunId: {
            ...previous.issueByRunId,
            [runId]: { kind: result.kind, message },
          },
        }));
        return { kind: result.kind, message };
      }
      candidate = result.state;
    }
    if (
      compareSequence(candidate.appliedSequence, expectedLastSequence) < 0
    ) {
      const message = (
        `历史重放只到 ${candidate.appliedSequence}，` +
        `低于服务端 run 游标 ${expectedLastSequence}`
      );
      set((previous) => ({
        issueByRunId: {
          ...previous.issueByRunId,
          [runId]: { kind: "gap", message },
        },
      }));
      return { kind: "gap", message };
    }
    let selected = candidate;
    let preserved = false;
    set((previous) => {
      const current = previous.byRunId[runId];
      if (
        current !== undefined &&
        compareSequence(current.appliedSequence, candidate.appliedSequence) > 0
      ) {
        selected = current;
        preserved = true;
        return previous;
      }
      return {
        byRunId: { ...previous.byRunId, [runId]: candidate },
        issueByRunId: { ...previous.issueByRunId, [runId]: undefined },
      };
    });
    return {
      kind: preserved ? "preserved" : "hydrated",
      state: selected,
    };
  },
  clearRun(runId) {
    set((state) => {
      const byRunId = { ...state.byRunId };
      const issueByRunId = { ...state.issueByRunId };
      delete byRunId[runId];
      delete issueByRunId[runId];
      return { byRunId, issueByRunId };
    });
  },
}));
