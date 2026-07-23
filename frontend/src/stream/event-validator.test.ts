import { describe, expect, it } from "vitest";

import {
  EventContractError,
  parsePersistedEvent,
  parseTransientEvent,
} from "./event-validator";

const base = {
  schema_version: 1,
  conversation_id: "11111111-1111-4111-8111-111111111111",
  run_id: "22222222-2222-4222-8222-222222222222",
  occurred_at: "2026-07-23T08:00:00Z",
};

describe("event wire validator", () => {
  it("requires an explicit v1 version and discriminator on persisted events", () => {
    const event = {
      ...base,
      event_id: "33333333-3333-4333-8333-333333333333",
      sequence: "1",
      type: "run.created",
      payload: { status: "pending" },
    };

    expect(parsePersistedEvent(event).schema_version).toBe(1);
    const { schema_version: _, ...versionless } = event;
    expect(() => parsePersistedEvent(versionless)).toThrow(EventContractError);
    expect(() =>
      parsePersistedEvent({ ...event, schema_version: 2 }),
    ).toThrow(EventContractError);
    const { type: __, ...withoutType } = event;
    expect(() => parsePersistedEvent(withoutType)).toThrow(EventContractError);
  });

  it("requires an explicit v1 version on transient events", () => {
    const event = {
      ...base,
      type: "assistant.delta",
      payload: {
        message_id: "44444444-4444-4444-8444-444444444444",
        index: 0,
        delta: "hello",
      },
    };

    expect(parseTransientEvent(event).schema_version).toBe(1);
    const { schema_version: _, ...versionless } = event;
    expect(() => parseTransientEvent(versionless)).toThrow(EventContractError);
  });
});
