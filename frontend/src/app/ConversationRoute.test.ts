import { describe, expect, it } from "vitest";

import type { Run, RunHistoryResponse } from "../api";
import { selectCurrentRun } from "./ConversationRoute";

const conversationId = "11111111-1111-4111-8111-111111111111";

function run(runId: string, createdAt: string): Run {
  return {
    schema_version: 1,
    run_id: runId,
    conversation_id: conversationId,
    status: "completed",
    last_sequence: "1",
    created_at: createdAt,
    started_at: createdAt,
    updated_at: createdAt,
    completed_at: createdAt,
    error_summary: null,
  };
}

describe("selectCurrentRun", () => {
  it("selects the newest run deterministically instead of trusting array position", () => {
    const older = run(
      "22222222-2222-4222-8222-222222222222",
      "2026-07-23T08:00:00Z",
    );
    const newer = run(
      "33333333-3333-4333-8333-333333333333",
      "2026-07-23T09:00:00Z",
    );
    const history: RunHistoryResponse = {
      schema_version: 1,
      conversation_id: conversationId,
      order: "newest_first",
      items: [older, newer],
      page: { next_cursor: null, has_more: false },
    };

    expect(selectCurrentRun(history)?.run_id).toBe(newer.run_id);
  });

  it("fails closed when the runtime response omits the declared order", () => {
    const malformed = {
      schema_version: 1,
      conversation_id: conversationId,
      items: [
        run(
          "22222222-2222-4222-8222-222222222222",
          "2026-07-23T08:00:00Z",
        ),
      ],
      page: { next_cursor: null, has_more: false },
    } as unknown as RunHistoryResponse;

    expect(selectCurrentRun(malformed)).toBeUndefined();
  });
});
