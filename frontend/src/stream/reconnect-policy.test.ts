import { describe, expect, it, vi } from "vitest";

import type { PersistedEvent } from "../generated/events-v1";
import { followRunEvents } from "./reconnect-policy";
import { EventApplicationError } from "./run-event-connection";
import { SseProtocolError } from "./sse-parser";

const RUN_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "22222222-2222-4222-8222-222222222222";

function persisted(
  sequence: string,
  type: "run.created" | "run.completed",
): PersistedEvent {
  return {
    schema_version: 1,
    event_id: `33333333-3333-4333-8333-${sequence.padStart(12, "0")}`,
    conversation_id: CONVERSATION_ID,
    run_id: RUN_ID,
    sequence,
    occurred_at: "2026-07-23T00:00:00Z",
    type,
    payload:
      type === "run.created"
        ? { status: "pending" }
        : { status: "completed", final_message_id: null, artifact_ids: [] },
  } as PersistedEvent;
}

function streamResponse(event: PersistedEvent): Response {
  const frame = `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
  return new Response(frame, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function streamEvents(events: readonly PersistedEvent[]): Response {
  return new Response(
    events
      .map(
        (event) =>
          `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
      )
      .join(""),
    {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    },
  );
}

describe("followRunEvents", () => {
  it("断线后从最后连续 sequence 重连且不会发送 cancel", async () => {
    let cursor = "0";
    let terminal = false;
    const requests: RequestInit[] = [];
    const fetcher = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      requests.push(init ?? {});
      return requests.length === 1
        ? streamResponse(persisted("1", "run.created"))
        : streamResponse(persisted("2", "run.completed"));
    });
    const wait = vi.fn(async () => undefined);
    const phases: string[] = [];

    await followRunEvents({
      baseUrl: "http://localhost:8000",
      runId: RUN_ID,
      conversationId: CONVERSATION_ID,
      signal: new AbortController().signal,
      getAppliedSequence: () => cursor,
      isTerminal: () => terminal,
      onEvent: (event) => {
        cursor = event.sequence;
        terminal = event.type === "run.completed";
      },
      onPhaseChange: (phase) => phases.push(phase),
      fetcher,
      wait,
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(new Headers(requests[0]?.headers).get("Last-Event-ID")).toBe("0");
    expect(new Headers(requests[1]?.headers).get("Last-Event-ID")).toBe("1");
    expect(requests.every((request) => request.method === "GET")).toBe(true);
    expect(wait).toHaveBeenCalledOnce();
    expect(phases).toContain("reconnecting");
    expect(phases.at(-1)).toBe("closed");
  });

  it("可恢复 sequence gap 会从未推进的 cursor 重连", async () => {
    let cursor = "0";
    let terminal = false;
    const requestCursors: Array<string | null> = [];
    const fetcher = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      requestCursors.push(new Headers(init?.headers).get("Last-Event-ID"));
      return requestCursors.length === 1
        ? streamResponse(persisted("2", "run.completed"))
        : streamEvents([
            persisted("1", "run.created"),
            persisted("2", "run.completed"),
          ]);
    });
    const wait = vi.fn(async () => undefined);

    await followRunEvents({
      baseUrl: "http://localhost:8000",
      runId: RUN_ID,
      conversationId: CONVERSATION_ID,
      signal: new AbortController().signal,
      getAppliedSequence: () => cursor,
      isTerminal: () => terminal,
      onEvent: (event) => {
        cursor = event.sequence;
        terminal = event.type === "run.completed";
      },
      onPhaseChange: () => undefined,
      fetcher,
      wait,
    });

    expect(requestCursors).toEqual(["0", "0"]);
    expect(wait).toHaveBeenCalledOnce();
    expect(cursor).toBe("2");
  });

  it("确定性 SSE 协议错误只尝试一次，并取消 reader 与 attempt signal", async () => {
    const cancel = vi.fn();
    const encoder = new TextEncoder();
    let attemptSignal: AbortSignal | undefined;
    const invalid = streamEvents([persisted("1", "run.created")]);
    const invalidText = (await invalid.text()).replace(
      "event: run.created",
      "event: run.completed",
    );
    const fetcher = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      attemptSignal = init?.signal ?? undefined;
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode(invalidText));
          },
          cancel,
        }),
        { status: 200, headers: { "content-type": "text/event-stream" } },
      );
    });
    const wait = vi.fn(async () => undefined);
    const errors: unknown[] = [];

    await expect(
      followRunEvents({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        conversationId: CONVERSATION_ID,
        signal: new AbortController().signal,
        getAppliedSequence: () => "0",
        isTerminal: () => false,
        onEvent: () => undefined,
        onPhaseChange: (_phase, error) => {
          if (error !== undefined) errors.push(error);
        },
        fetcher,
        wait,
      }),
    ).rejects.toBeInstanceOf(SseProtocolError);

    expect(fetcher).toHaveBeenCalledOnce();
    expect(wait).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalledOnce();
    expect(attemptSignal?.aborted).toBe(true);
    expect(errors).toHaveLength(1);
  });

  it("projector conflict 等未知 callback 错误 fail-closed，不重连", async () => {
    const conflict = new Error("projector conflict");
    const fetcher = vi.fn(async () => streamResponse(persisted("1", "run.created")));
    const wait = vi.fn(async () => undefined);

    await expect(
      followRunEvents({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        signal: new AbortController().signal,
        getAppliedSequence: () => "0",
        isTerminal: () => false,
        onEvent: () => {
          throw conflict;
        },
        onPhaseChange: () => undefined,
        fetcher,
        wait,
      }),
    ).rejects.toMatchObject({
      name: EventApplicationError.name,
      cause: conflict,
    });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(wait).not.toHaveBeenCalled();
  });

  it("EventContractError 与错误 Content-Type 均 fail-closed", async () => {
    const invalidContract = {
      ...persisted("1", "run.created"),
      payload: {},
      type: "message.completed",
    } as PersistedEvent;
    const cases: Array<() => Promise<Response>> = [
      async () => streamResponse(invalidContract),
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    ];

    for (const response of cases) {
      const fetcher = vi.fn(response);
      const wait = vi.fn(async () => undefined);
      await expect(
        followRunEvents({
          baseUrl: "http://localhost:8000",
          runId: RUN_ID,
          signal: new AbortController().signal,
          getAppliedSequence: () => "0",
          isTerminal: () => false,
          onEvent: () => undefined,
          onPhaseChange: () => undefined,
          fetcher,
          wait,
        }),
      ).rejects.toBeInstanceOf(Error);
      expect(fetcher).toHaveBeenCalledOnce();
      expect(wait).not.toHaveBeenCalled();
    }
  });

  it("只重试明确的 HTTP transient 状态", async () => {
    const retryableFetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("busy", { status: 503 }))
      .mockResolvedValueOnce(streamResponse(persisted("1", "run.completed")));
    await followRunEvents({
      baseUrl: "http://localhost:8000",
      runId: RUN_ID,
      signal: new AbortController().signal,
      getAppliedSequence: () => "0",
      isTerminal: () => false,
      onEvent: () => undefined,
      onPhaseChange: () => undefined,
      fetcher: retryableFetcher,
      wait: async () => undefined,
    });
    expect(retryableFetcher).toHaveBeenCalledTimes(2);

    const fatalFetcher = vi.fn(async () => new Response("bad", { status: 400 }));
    await expect(
      followRunEvents({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        signal: new AbortController().signal,
        getAppliedSequence: () => "0",
        isTerminal: () => false,
        onEvent: () => undefined,
        onPhaseChange: () => undefined,
        fetcher: fatalFetcher,
        wait: async () => undefined,
      }),
    ).rejects.toMatchObject({ status: 400 });
    expect(fatalFetcher).toHaveBeenCalledOnce();
  });
});
