import type { components, operations } from "../generated/openapi-v1";
import {
  clientFor,
  type ApiCallOptions,
  unwrapApiResponse,
} from "./client";

export type MemorySettings = components["schemas"]["MemorySettingsRead"];
export type MemorySettingsUpdate =
  components["schemas"]["MemorySettingsUpdateRequest"];
export type MemoryProviderConsent =
  components["schemas"]["MemoryProviderConsentRequest"];
export type Memory = components["schemas"]["MemoryRead"];
export type MemoryListResponse =
  components["schemas"]["MemoryListResponse"];
export type MemoryCreateRequest =
  components["schemas"]["MemoryCreateRequest"];
export type MemoryApproveRequest =
  components["schemas"]["MemoryApproveRequest"];
export type MemoryCorrectRequest =
  components["schemas"]["MemoryCorrectRequest"];
export type MemoryForgetRequest =
  components["schemas"]["MemoryForgetRequest"];
export type MemoryPurgeRequest =
  components["schemas"]["MemoryPurgeRequest"];
export type MemoryCommandResponse =
  components["schemas"]["MemoryCommandResponse"];
export type RunMemoryContext =
  components["schemas"]["RunMemoryContextRead"];
export type MemoryListQuery = NonNullable<
  operations["listMemories"]["parameters"]["query"]
>;

export async function getMemorySettings(
  options?: ApiCallOptions,
): Promise<MemorySettings> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/memory/settings", {
      signal: options?.signal,
    }),
  );
}

export async function updateMemorySettings(
  body: MemorySettingsUpdate,
  options?: ApiCallOptions,
): Promise<MemorySettings> {
  return unwrapApiResponse(
    await clientFor(options).PATCH("/api/v1/memory/settings", {
      body,
      signal: options?.signal,
    }),
  );
}

export async function updateMemoryProviderConsent(
  body: MemoryProviderConsent,
  options?: ApiCallOptions,
): Promise<MemorySettings> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/memory/provider-consent", {
      body,
      signal: options?.signal,
    }),
  );
}

export async function listMemories(
  query: MemoryListQuery = {},
  options?: ApiCallOptions,
): Promise<MemoryListResponse> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/memories", {
      params: { query },
      signal: options?.signal,
    }),
  );
}

export async function listAllMemories(
  options?: ApiCallOptions,
): Promise<readonly Memory[]> {
  const items: Memory[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null | undefined;
  let page: MemoryListResponse;
  do {
    page = await listMemories({ cursor, limit: 100 }, options);
    items.push(...page.items);
    cursor = page.page.next_cursor;
    if (page.page.has_more) {
      if (!cursor || seenCursors.has(cursor)) {
        throw new Error("memory list 返回了无效分页游标");
      }
      seenCursors.add(cursor);
    }
  } while (page.page.has_more);
  return items;
}

export async function createMemory(
  body: MemoryCreateRequest,
  options?: ApiCallOptions,
): Promise<Memory> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/memories", {
      body,
      signal: options?.signal,
    }),
  );
}

export async function getMemory(
  memoryId: string,
  options?: ApiCallOptions,
): Promise<Memory> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/memories/{memory_id}", {
      params: { path: { memory_id: memoryId } },
      signal: options?.signal,
    }),
  );
}

export async function approveMemory(
  memoryId: string,
  body: MemoryApproveRequest,
  options?: ApiCallOptions,
): Promise<MemoryCommandResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/memories/{memory_id}/approve", {
      params: { path: { memory_id: memoryId } },
      body,
      signal: options?.signal,
    }),
  );
}

export async function correctMemory(
  memoryId: string,
  body: MemoryCorrectRequest,
  options?: ApiCallOptions,
): Promise<MemoryCommandResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/memories/{memory_id}/correct", {
      params: { path: { memory_id: memoryId } },
      body,
      signal: options?.signal,
    }),
  );
}

export async function forgetMemory(
  memoryId: string,
  body: MemoryForgetRequest,
  options?: ApiCallOptions,
): Promise<MemoryCommandResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/memories/{memory_id}/forget", {
      params: { path: { memory_id: memoryId } },
      body,
      signal: options?.signal,
    }),
  );
}

export async function purgeMemory(
  memoryId: string,
  body: MemoryPurgeRequest,
  options?: ApiCallOptions,
): Promise<MemoryCommandResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/memories/{memory_id}/purge", {
      params: { path: { memory_id: memoryId } },
      body,
      signal: options?.signal,
    }),
  );
}

export async function getRunMemoryContext(
  runId: string,
  options?: ApiCallOptions,
): Promise<RunMemoryContext> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/runs/{run_id}/memory-context", {
      params: { path: { run_id: runId } },
      signal: options?.signal,
    }),
  );
}
