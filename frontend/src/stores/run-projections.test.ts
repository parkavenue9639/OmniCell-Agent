import { beforeEach, describe, expect, it } from "vitest";

import type { PersistedEvent } from "../generated/events-v1";
import { useRunProjectionStore } from "./run-projections";

const conversationId = "11111111-1111-4111-8111-111111111111";
const firstRunId = "22222222-2222-4222-8222-222222222222";
const secondRunId = "33333333-3333-4333-8333-333333333333";

function event(
  runId: string,
  sequence: string,
  type: "run.created" | "run.started",
): PersistedEvent {
  return {
    schema_version: 1,
    event_id: crypto.randomUUID(),
    conversation_id: conversationId,
    run_id: runId,
    sequence,
    occurred_at: "2026-07-23T10:00:00Z",
    type,
    payload: { status: type === "run.created" ? "pending" : "running" },
  } as PersistedEvent;
}

describe("run projection history hydration", () => {
  beforeEach(() => {
    useRunProjectionStore.setState({ byRunId: {}, issueByRunId: {} });
  });

  it("hydrates multiple historical runs independently", () => {
    const store = useRunProjectionStore.getState();
    const first = store.hydrateRun(
      firstRunId,
      conversationId,
      [event(firstRunId, "1", "run.created")],
      "1",
    );
    const second = store.hydrateRun(
      secondRunId,
      conversationId,
      [event(secondRunId, "1", "run.started")],
      "1",
    );

    expect(first.kind).toBe("hydrated");
    expect(second.kind).toBe("hydrated");
    expect(Object.keys(useRunProjectionStore.getState().byRunId)).toEqual([
      firstRunId,
      secondRunId,
    ]);
  });

  it("does not overwrite a projection that SSE already advanced", () => {
    const first = event(firstRunId, "1", "run.created");
    useRunProjectionStore
      .getState()
      .hydrateRun(firstRunId, conversationId, [first], "1");
    useRunProjectionStore
      .getState()
      .applyEvent(event(firstRunId, "2", "run.started"));

    const replay = useRunProjectionStore
      .getState()
      .hydrateRun(firstRunId, conversationId, [first], "1");

    expect(replay.kind).toBe("preserved");
    expect(
      useRunProjectionStore.getState().byRunId[firstRunId].appliedSequence,
    ).toBe("2");
  });

  it("fails closed when replay does not reach the run last_sequence", () => {
    const result = useRunProjectionStore.getState().hydrateRun(
      firstRunId,
      conversationId,
      [event(firstRunId, "1", "run.created")],
      "2",
    );

    expect(result.kind).toBe("gap");
    expect(useRunProjectionStore.getState().byRunId[firstRunId]).toBeUndefined();
  });

  it("accepts replay that advanced beyond a stale history snapshot", () => {
    const result = useRunProjectionStore.getState().hydrateRun(
      firstRunId,
      conversationId,
      [
        event(firstRunId, "1", "run.created"),
        event(firstRunId, "2", "run.started"),
      ],
      "1",
    );

    expect(result.kind).toBe("hydrated");
    if (result.kind !== "hydrated") {
      throw new Error(`expected hydrated, received ${result.kind}`);
    }
    expect(result.state.appliedSequence).toBe("2");
  });
});
