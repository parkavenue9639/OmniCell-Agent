import { describe, expect, it, vi } from "vitest";

import type { PersistedEvent } from "../generated/events-v1";
import { EventContractError } from "./event-validator";
import { consumeRunEventStream } from "./run-event-connection";
import { SseProtocolError } from "./sse-parser";

const RUN_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "22222222-2222-4222-8222-222222222222";

function frame(overrides: Partial<PersistedEvent> = {}): string {
  const event = {
    schema_version: 1,
    event_id: "33333333-3333-4333-8333-333333333333",
    conversation_id: CONVERSATION_ID,
    run_id: RUN_ID,
    sequence: "1",
    occurred_at: "2026-07-23T00:00:00Z",
    type: "run.created",
    payload: { status: "pending" },
    ...overrides,
  } as PersistedEvent;
  return `: heartbeat\n\nid: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

function responseFromChunks(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );
}

describe("consumeRunEventStream", () => {
  it("解析跨 chunk frame、忽略 heartbeat 并只发送 Last-Event-ID", async () => {
    const payload = frame();
    const fetcher = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Last-Event-ID")).toBe(
        "9007199254740993",
      );
      expect(String(_input)).not.toContain("after_sequence");
      return responseFromChunks([payload.slice(0, 17), payload.slice(17)]);
    });
    const events: PersistedEvent[] = [];

    const result = await consumeRunEventStream({
      baseUrl: "http://localhost:8000",
      runId: RUN_ID,
      conversationId: CONVERSATION_ID,
      afterSequence: "9007199254740993",
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
      fetcher,
    });

    expect(result.terminal).toBe(false);
    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe("run.created");
  });

  it("拒绝 SSE event 与 payload type 不一致", async () => {
    const invalid = frame().replace("event: run.created", "event: run.started");
    await expect(
      consumeRunEventStream({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        afterSequence: "0",
        signal: new AbortController().signal,
        onEvent: () => undefined,
        fetcher: async () => responseFromChunks([invalid]),
      }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("拒绝错误 run identity", async () => {
    const invalid = frame({
      run_id: "44444444-4444-4444-8444-444444444444",
    });
    await expect(
      consumeRunEventStream({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        afterSequence: "0",
        signal: new AbortController().signal,
        onEvent: () => undefined,
        fetcher: async () => responseFromChunks([invalid]),
      }),
    ).rejects.toBeInstanceOf(SseProtocolError);
  });

  it("拒绝未通过 persisted schema 的 payload", async () => {
    const invalid = frame({
      type: "message.completed",
      payload: {},
    } as Partial<PersistedEvent>);
    await expect(
      consumeRunEventStream({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        afterSequence: "0",
        signal: new AbortController().signal,
        onEvent: () => undefined,
        fetcher: async () => responseFromChunks([invalid]),
      }),
    ).rejects.toBeInstanceOf(EventContractError);
  });

  it("terminal event 后返回且不把 heartbeat 当事件", async () => {
    const events: PersistedEvent[] = [];
    const result = await consumeRunEventStream({
      baseUrl: "http://localhost:8000",
      runId: RUN_ID,
      afterSequence: "0",
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
      fetcher: async () =>
        responseFromChunks([
          frame({
            type: "run.completed",
            payload: {
              status: "completed",
              final_message_id: null,
              artifact_ids: [],
            },
          }),
        ]),
    });

    expect(result.terminal).toBe(true);
    expect(events).toHaveLength(1);
  });

  it("terminal 到达后忽略同 chunk trailing frame 并立即取消 reader", async () => {
    const cancel = vi.fn();
    const encoder = new TextEncoder();
    const trailing =
      "id: 2\nevent: message.completed\ndata: {terminal 后的非法 JSON 不应再解析}\n\n";
    const response = new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            encoder.encode(
              frame({
                type: "run.completed",
                payload: {
                  status: "completed",
                  final_message_id: null,
                  artifact_ids: [],
                },
              }) + trailing,
            ),
          );
        },
        cancel,
      }),
      { status: 200, headers: { "content-type": "text/event-stream; charset=utf-8" } },
    );
    const events: PersistedEvent[] = [];

    const result = await consumeRunEventStream({
      baseUrl: "http://localhost:8000",
      runId: RUN_ID,
      afterSequence: "0",
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
      fetcher: async () => response,
    });

    expect(result.terminal).toBe(true);
    expect(events.map((event) => event.type)).toEqual(["run.completed"]);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("拒绝错误 Content-Type 并取消 response body", async () => {
    const cancel = vi.fn();
    const response = new Response(
      new ReadableStream({
        start() {
          // 保持 body 打开，用于确认协议错误会主动取消。
        },
        cancel,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );

    await expect(
      consumeRunEventStream({
        baseUrl: "http://localhost:8000",
        runId: RUN_ID,
        afterSequence: "0",
        signal: new AbortController().signal,
        onEvent: () => undefined,
        fetcher: async () => response,
      }),
    ).rejects.toBeInstanceOf(SseProtocolError);
    expect(cancel).toHaveBeenCalledOnce();
  });
});
