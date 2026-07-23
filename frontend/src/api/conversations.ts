import type { components, operations } from "../generated/openapi-v1";
import {
  clientFor,
  type ApiCallOptions,
  unwrapApiResponse,
} from "./client";

export type Conversation = components["schemas"]["ConversationRead"];
export type ConversationCreateRequest =
  components["schemas"]["ConversationCreateRequest"];
export type ConversationListResponse =
  components["schemas"]["ConversationListResponse"];
export type ConversationListQuery = NonNullable<
  operations["listConversations"]["parameters"]["query"]
>;
export type RunHistoryResponse = components["schemas"]["RunHistoryResponse"];
export type ConversationHistoryQuery = NonNullable<
  operations["getConversationHistory"]["parameters"]["query"]
>;

export async function listConversations(
  query: ConversationListQuery = {},
  options?: ApiCallOptions,
): Promise<ConversationListResponse> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/conversations", {
      params: { query },
      signal: options?.signal,
    }),
  );
}

export async function createConversation(
  body: ConversationCreateRequest,
  options?: ApiCallOptions,
): Promise<Conversation> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/conversations", {
      body,
      signal: options?.signal,
    }),
  );
}

export async function getConversation(
  conversationId: string,
  options?: ApiCallOptions,
): Promise<Conversation> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/conversations/{conversation_id}", {
      params: { path: { conversation_id: conversationId } },
      signal: options?.signal,
    }),
  );
}

export async function getConversationHistory(
  conversationId: string,
  query: ConversationHistoryQuery = {},
  options?: ApiCallOptions,
): Promise<RunHistoryResponse> {
  return unwrapApiResponse(
    await clientFor(options).GET(
      "/api/v1/conversations/{conversation_id}/history",
      {
        params: {
          path: { conversation_id: conversationId },
          query,
        },
        signal: options?.signal,
      },
    ),
  );
}

export async function getAllConversationHistory(
  conversationId: string,
  options?: ApiCallOptions,
): Promise<RunHistoryResponse> {
  const items: RunHistoryResponse["items"] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null | undefined;
  let page: RunHistoryResponse | undefined;
  do {
    page = await getConversationHistory(
      conversationId,
      { cursor, limit: 100 },
      options,
    );
    items.push(...page.items);
    cursor = page.page.next_cursor;
    if (page.page.has_more) {
      if (!cursor || seenCursors.has(cursor)) {
        throw new Error("conversation history 返回了无效分页游标");
      }
      seenCursors.add(cursor);
    }
  } while (page.page.has_more);
  return {
    ...page,
    items,
    page: { has_more: false, next_cursor: null },
  };
}
