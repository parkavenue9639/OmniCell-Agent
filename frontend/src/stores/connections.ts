import { create } from "zustand";

import type { ConnectionPhase } from "../stream/reconnect-policy";

export interface ConnectionState {
  readonly phase: ConnectionPhase;
  readonly error: string | null;
}

interface ConnectionStore {
  readonly byRunId: Readonly<Record<string, ConnectionState>>;
  setPhase: (runId: string, phase: ConnectionPhase, error?: unknown) => void;
  clearRun: (runId: string) => void;
}

function publicError(error: unknown): string | null {
  if (error === undefined) {
    return null;
  }
  return error instanceof Error ? error.message : "事件流连接异常";
}

export const useConnectionStore = create<ConnectionStore>((set) => ({
  byRunId: {},
  setPhase(runId, phase, error) {
    set((state) => ({
      byRunId: {
        ...state.byRunId,
        [runId]: { phase, error: publicError(error) },
      },
    }));
  },
  clearRun(runId) {
    set((state) => {
      const byRunId = { ...state.byRunId };
      delete byRunId[runId];
      return { byRunId };
    });
  },
}));
