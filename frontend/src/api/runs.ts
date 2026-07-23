import type { components, operations } from "../generated/openapi-v1";
import {
  clientFor,
  type ApiCallOptions,
  unwrapApiResponse,
} from "./client";

export type Run = components["schemas"]["RunRead"];
export type RunCreateRequest = components["schemas"]["RunCreateRequest"];
export type RunCreateResponse = components["schemas"]["RunCreateResponse"];
export type RunCancelRequest = components["schemas"]["RunCancelRequest"];
export type RunCancelResponse = components["schemas"]["RunCancelResponse"];
export type RunResumeRequest = components["schemas"]["RunResumeRequest"];
export type RunResumeResponse = components["schemas"]["RunResumeResponse"];
export type EventReplayResponse = components["schemas"]["EventReplayResponse"];
export type EventReplayQuery = NonNullable<
  operations["replayRunEvents"]["parameters"]["query"]
>;

export interface PreparedRunSubmission {
  readonly conversationId: string;
  readonly body: Readonly<RunCreateRequest>;
  readonly idempotencyKey: string;
}

export function prepareRunSubmission(
  conversationId: string,
  request: RunCreateRequest,
  idempotencyKey = request.request_key ?? globalThis.crypto.randomUUID(),
): PreparedRunSubmission {
  if (!idempotencyKey.trim()) {
    throw new TypeError("Idempotency-Key 不能为空");
  }
  if (request.request_key && request.request_key !== idempotencyKey) {
    throw new TypeError("request_key 必须与 Idempotency-Key 一致");
  }

  return Object.freeze({
    conversationId,
    idempotencyKey,
    body: Object.freeze({
      ...request,
      request_key: idempotencyKey,
    }),
  });
}

export async function submitRun(
  submission: PreparedRunSubmission,
  options?: ApiCallOptions,
): Promise<RunCreateResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST(
      "/api/v1/conversations/{conversation_id}/runs",
      {
        params: {
          path: { conversation_id: submission.conversationId },
          header: { "Idempotency-Key": submission.idempotencyKey },
        },
        body: submission.body,
        signal: options?.signal,
      },
    ),
  );
}

export async function getRun(
  runId: string,
  options?: ApiCallOptions,
): Promise<Run> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/runs/{run_id}", {
      params: { path: { run_id: runId } },
      signal: options?.signal,
    }),
  );
}

export async function cancelRun(
  runId: string,
  body: RunCancelRequest = {},
  options?: ApiCallOptions,
): Promise<RunCancelResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/runs/{run_id}/cancel", {
      params: { path: { run_id: runId } },
      body,
      signal: options?.signal,
    }),
  );
}

export async function resumeRun(
  runId: string,
  body: RunResumeRequest = {},
  options?: ApiCallOptions,
): Promise<RunResumeResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/runs/{run_id}/resume", {
      params: { path: { run_id: runId } },
      body,
      signal: options?.signal,
    }),
  );
}

export async function replayRunEvents(
  runId: string,
  query: EventReplayQuery = {},
  options?: ApiCallOptions,
): Promise<EventReplayResponse> {
  return unwrapApiResponse(
    await clientFor(options).GET("/api/v1/runs/{run_id}/events", {
      params: { path: { run_id: runId }, query },
      signal: options?.signal,
    }),
  );
}

export async function replayAllRunEvents(
  runId: string,
  options?: ApiCallOptions,
): Promise<EventReplayResponse> {
  const events: EventReplayResponse["events"] = [];
  let afterSequence = "0";
  while (true) {
    const page = await replayRunEvents(
      runId,
      { after_sequence: afterSequence, limit: 500 },
      options,
    );
    events.push(...page.events);
    if (!page.has_more) {
      return { ...page, events };
    }
    if (page.next_sequence === afterSequence) {
      throw new Error("run event replay 未推进 sequence");
    }
    afterSequence = page.next_sequence;
  }
}
