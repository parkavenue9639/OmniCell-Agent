import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "./client";
import {
  createMemory,
  listAllMemories,
  listMemories,
  purgeMemory,
  updateMemoryProviderConsent,
  type Memory,
} from "./memories";

const memory: Memory = {
  schema_version: 1,
  memory_id: "11111111-1111-4111-8111-111111111111",
  scope_key: "local-default",
  stable_key: "response.language",
  kind: "response_preference",
  status: "active",
  current_version: 1,
  version_id: "22222222-2222-4222-8222-222222222222",
  content_sha256:
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  content: "使用中文回复",
  source: {
    source_kind: "explicit",
    message_ids: [],
  },
  created_at: "2026-07-26T08:00:00Z",
  updated_at: "2026-07-26T08:00:00Z",
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("memory API", () => {
  it("lists memory and sends explicit create content only to the Memory API", async () => {
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (request: Request) => {
      requests.push(request.clone());
      if (request.method === "GET") {
        return json({
          schema_version: 1,
          items: [memory],
          page: { has_more: false, next_cursor: null },
        });
      }
      return json(memory, 201);
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });

    const listed = await listMemories({ limit: 100 }, { client });
    const created = await createMemory(
      {
        kind: "response_preference",
        stable_key: "response.language",
        content: "使用中文回复",
        source_message_ids: [],
      },
      { client },
    );

    expect(listed.items).toEqual([memory]);
    expect(created).toEqual(memory);
    expect(new URL(requests[0]!.url).pathname).toBe("/api/v1/memories");
    expect(new URL(requests[0]!.url).searchParams.get("limit")).toBe("100");
    expect(await requests[1]!.json()).toEqual({
      kind: "response_preference",
      stable_key: "response.language",
      content: "使用中文回复",
      source_message_ids: [],
    });
  });

  it("follows every memory cursor instead of truncating the global manager", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      return json({
        schema_version: 1,
        items: [
          {
            ...memory,
            memory_id:
              cursor === null
                ? "11111111-1111-4111-8111-111111111111"
                : "33333333-3333-4333-8333-333333333333",
          },
        ],
        page:
          cursor === null
            ? { has_more: true, next_cursor: "next-page" }
            : { has_more: false, next_cursor: null },
      });
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });

    const items = await listAllMemories({ client });

    expect(items.map((item) => item.memory_id)).toEqual([
      "11111111-1111-4111-8111-111111111111",
      "33333333-3333-4333-8333-333333333333",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("requires an explicit consent statement and purge confirmation payload", async () => {
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (request: Request) => {
      requests.push(request.clone());
      if (request.url.endsWith("/memory/provider-consent")) {
        return json({
          schema_version: 1,
          scope_key: "local-default",
          version: 2,
          use_memory: false,
          generate_candidates: false,
          enable_agent_tools: false,
          provider_consent_granted: true,
          provider_consent_version: "memory-provider-v1",
          provider_consented_at: "2026-07-26T08:00:00Z",
          updated_at: "2026-07-26T08:00:00Z",
        });
      }
      return json({ schema_version: 1, memory: { ...memory, status: "purged" } });
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });

    await updateMemoryProviderConsent(
      {
        decision: "grant",
        statement_version: "memory-provider-v1",
        confirmed: true,
        expected_version: 1,
      },
      { client },
    );
    await purgeMemory(
      memory.memory_id,
      { expected_version: 1, confirmed: true },
      { client },
    );

    expect(await requests[0]!.json()).toEqual({
      decision: "grant",
      statement_version: "memory-provider-v1",
      confirmed: true,
      expected_version: 1,
    });
    expect(new URL(requests[1]!.url).pathname).toBe(
      `/api/v1/memories/${memory.memory_id}/purge`,
    );
    expect(await requests[1]!.json()).toEqual({
      expected_version: 1,
      confirmed: true,
    });
  });
});
