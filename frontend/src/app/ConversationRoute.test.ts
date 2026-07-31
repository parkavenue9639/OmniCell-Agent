import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import type { Memory, Run, RunHistoryResponse } from "../api";
import {
  clearMemoryCommandMutationCache,
  eventRefreshesMemories,
  MEMORY_COMMAND_MUTATION_KEY,
  removeMemoryFromCachedList,
  selectCurrentRun,
} from "./ConversationRoute";

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

describe("purged memory cache handling", () => {
  it("synchronously removes the cached object that owns the old plaintext", () => {
    const purgedId = "44444444-4444-4444-8444-444444444444";
    const cached = [
      {
        schema_version: 1,
        memory_id: purgedId,
        scope_key: "local-default",
        stable_key: "profile.private",
        kind: "profile_fact",
        status: "active",
        current_version: 1,
        version_id: "55555555-5555-4555-8555-555555555555",
        content: "必须立即卸载的旧正文",
        created_at: "2026-07-26T08:00:00Z",
        updated_at: "2026-07-26T08:00:00Z",
      },
    ] satisfies Memory[];

    const next = removeMemoryFromCachedList(cached, purgedId);

    expect(next).toEqual([]);
    expect(JSON.stringify(next)).not.toContain("必须立即卸载的旧正文");
    expect(cached).toHaveLength(1);
  });

  it("removes plaintext from both QueryCache and MutationCache at the purge boundary", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const purgedId = "44444444-4444-4444-8444-444444444444";
    const plaintext = "purge 后前端 JS 缓存中不得保留的敏感正文";
    queryClient.setQueryData<readonly Memory[]>(["memory", "items"], [
      {
        schema_version: 1,
        memory_id: purgedId,
        scope_key: "local-default",
        stable_key: "profile.private",
        kind: "profile_fact",
        status: "active",
        current_version: 2,
        version_id: "55555555-5555-4555-8555-555555555555",
        content: plaintext,
        created_at: "2026-07-26T08:00:00Z",
        updated_at: "2026-07-26T09:00:00Z",
      },
    ]);
    const mutation = queryClient.getMutationCache().build(queryClient, {
      mutationKey: MEMORY_COMMAND_MUTATION_KEY,
      gcTime: 0,
      mutationFn: async (variables: { readonly content: string }) => ({
        content: variables.content,
      }),
    });
    await mutation.execute({ content: plaintext });

    expect(
      JSON.stringify(queryClient.getMutationCache().getAll()),
    ).toContain(plaintext);

    queryClient.setQueryData<readonly Memory[]>(
      ["memory", "items"],
      (current) => removeMemoryFromCachedList(current, purgedId),
    );
    clearMemoryCommandMutationCache(queryClient);

    expect(queryClient.getMutationCache().getAll()).toEqual([]);
    expect(
      JSON.stringify({
        queryData: queryClient.getQueryData(["memory", "items"]),
        mutations: queryClient.getMutationCache().getAll(),
      }),
    ).not.toContain(plaintext);
  });
});

describe("memory event refresh policy", () => {
  it("uses the same proposal trigger for live delivery and replay hydration", () => {
    expect(eventRefreshesMemories({ type: "memory.proposal_created" })).toBe(
      true,
    );
    expect(eventRefreshesMemories({ type: "memory.context_loaded" })).toBe(
      false,
    );
  });
});
