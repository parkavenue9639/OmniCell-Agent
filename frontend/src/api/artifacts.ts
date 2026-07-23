import type { components, operations } from "../generated/openapi-v1";
import {
  clientFor,
  type ApiCallOptions,
  unwrapApiResponse,
} from "./client";
import { normalizeApiError } from "./errors";

export type Artifact = components["schemas"]["ArtifactRead"];
export type ArtifactListResponse = components["schemas"]["ArtifactListResponse"];
export type ArtifactListQuery = NonNullable<
  operations["listArtifacts"]["parameters"]["query"]
>;
export type ArtifactUploadRequest =
  components["schemas"]["Body_uploadArtifact"];

export async function listArtifacts(
  conversationId: string,
  query: ArtifactListQuery = {},
  options?: ApiCallOptions,
): Promise<ArtifactListResponse> {
  return unwrapApiResponse(
    await clientFor(options).GET(
      "/api/v1/conversations/{conversation_id}/artifacts",
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

export async function listAllArtifacts(
  conversationId: string,
  options?: ApiCallOptions,
): Promise<ArtifactListResponse> {
  const items: ArtifactListResponse["items"] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null | undefined;
  let page: ArtifactListResponse | undefined;
  do {
    page = await listArtifacts(
      conversationId,
      { cursor, limit: 100 },
      options,
    );
    items.push(...page.items);
    cursor = page.page.next_cursor;
    if (page.page.has_more) {
      if (!cursor || seenCursors.has(cursor)) {
        throw new Error("artifact list 返回了无效分页游标");
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

export async function getArtifact(
  artifactId: string,
  options?: ApiCallOptions,
): Promise<Artifact> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/artifacts/{artifact_id}", {
      params: { path: { artifact_id: artifactId } },
      signal: options?.signal,
    }),
  );
}

export async function uploadArtifact(
  conversationId: string,
  body: ArtifactUploadRequest,
  options?: ApiCallOptions,
): Promise<Artifact> {
  return unwrapApiResponse(
    await clientFor(options).POST(
      "/api/v1/conversations/{conversation_id}/artifacts",
      {
        params: { path: { conversation_id: conversationId } },
        body,
        bodySerializer: (upload) => {
          const form = new FormData();
          const fileName =
            "name" in upload.file && typeof upload.file.name === "string"
              ? upload.file.name
              : "upload.bin";
          form.append("file", upload.file, fileName);
          form.append("kind", upload.kind);
          return form;
        },
        signal: options?.signal,
      },
    ),
  );
}

export async function downloadArtifact(
  artifactId: string,
  options?: ApiCallOptions,
): Promise<Blob> {
  const result = await clientFor(options).GET(
    "/api/v1/artifacts/{artifact_id}/content",
    {
      params: { path: { artifact_id: artifactId } },
      parseAs: "blob",
      signal: options?.signal,
    },
  );
  if (result.data !== undefined) {
    return result.data;
  }

  let error: unknown = result.error;
  if (
    error instanceof Blob &&
    result.response.headers.get("content-type")?.includes("application/json")
  ) {
    try {
      error = JSON.parse(await error.text());
    } catch {
      // 保留原始 Blob，由统一错误归一逻辑生成安全兜底错误。
    }
  }
  throw normalizeApiError(error, result.response);
}
