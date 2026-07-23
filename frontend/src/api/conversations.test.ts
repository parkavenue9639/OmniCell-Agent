import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "./client";
import { getAllConversationHistory, type RunHistoryResponse } from "./conversations";

const conversationId = "13269a73-8a64-47f6-ad95-6d6063a3e5cc";

function page(
  runId: string,
  nextCursor: string | null,
): RunHistoryResponse {
  return {
    schema_version: 1,
    conversation_id: conversationId,
    order: "newest_first",
    items: [
      {
        schema_version: 1,
        run_id: runId,
        conversation_id: conversationId,
        status: "completed",
        last_sequence: "1",
        created_at: "2026-07-23T08:00:00Z",
        updated_at: "2026-07-23T08:00:00Z",
      },
    ],
    page: {
      has_more: nextCursor !== null,
      next_cursor: nextCursor,
    },
  };
}

describe("getAllConversationHistory", () => {
  it("follows server cursors instead of truncating history at one page", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      const body =
        cursor === "next-page"
          ? page("22222222-2222-4222-8222-222222222222", null)
          : page(
              "11111111-1111-4111-8111-111111111111",
              "next-page",
            );
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });

    const history = await getAllConversationHistory(conversationId, { client });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(history.items.map((run) => run.run_id)).toEqual([
      "11111111-1111-4111-8111-111111111111",
      "22222222-2222-4222-8222-222222222222",
    ]);
    expect(history.page).toEqual({ has_more: false, next_cursor: null });
  });
});
