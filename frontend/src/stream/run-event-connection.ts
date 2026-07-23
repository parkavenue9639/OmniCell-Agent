import type { PersistedEvent } from "../generated/events-v1";
import { compareSequence, nextSequence } from "../projector/sequence";
import { createRunSseParser, SseProtocolError } from "./sse-parser";

const TERMINAL_EVENTS = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
]);

export class StreamHttpError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    super(`事件流请求失败：HTTP ${status}`);
    this.name = "StreamHttpError";
    this.status = status;
    this.body = body;
  }
}

export class StreamEndedError extends Error {
  constructor() {
    super("事件流在 run 进入终态前结束");
    this.name = "StreamEndedError";
  }
}

export class StreamGapError extends Error {
  readonly expectedSequence: string;
  readonly receivedSequence: string;

  constructor(expectedSequence: string, receivedSequence: string) {
    super(`事件序列存在缺口：等待 ${expectedSequence}，收到 ${receivedSequence}`);
    this.name = "StreamGapError";
    this.expectedSequence = expectedSequence;
    this.receivedSequence = receivedSequence;
  }
}

export class EventApplicationError extends Error {
  override readonly cause: unknown;

  constructor(cause: unknown) {
    super("事件投影回调失败", { cause });
    this.name = "EventApplicationError";
    this.cause = cause;
  }
}

export interface RunEventStreamOptions {
  readonly baseUrl: string;
  readonly runId: string;
  readonly conversationId?: string;
  readonly afterSequence: string;
  readonly signal: AbortSignal;
  readonly onEvent: (event: PersistedEvent) => void;
  readonly fetcher?: typeof fetch;
}

async function errorBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  return await response.text();
}

export async function consumeRunEventStream(
  options: RunEventStreamOptions,
): Promise<{ readonly terminal: boolean }> {
  const fetcher = options.fetcher ?? fetch;
  const url = new URL(
    `/api/v1/runs/${encodeURIComponent(options.runId)}/events/stream`,
    options.baseUrl,
  );
  const attemptController = new AbortController();
  const abortAttempt = () => attemptController.abort(options.signal.reason);
  if (options.signal.aborted) {
    abortAttempt();
  } else {
    options.signal.addEventListener("abort", abortAttempt, { once: true });
  }

  let response: Response | undefined;
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let terminal = false;
  let expectedSequence = "";
  const parser = createRunSseParser(
    { runId: options.runId, conversationId: options.conversationId },
    (event) => {
      if (terminal) {
        return;
      }
      const comparison = compareSequence(event.sequence, expectedSequence);
      if (comparison > 0) {
        throw new StreamGapError(expectedSequence, event.sequence);
      }
      try {
        options.onEvent(event);
      } catch (error) {
        throw new EventApplicationError(error);
      }
      terminal = event.type !== undefined && TERMINAL_EVENTS.has(event.type);
      if (!terminal && comparison === 0) {
        expectedSequence = nextSequence(event.sequence);
      }
    },
    () => terminal,
  );
  const decoder = new TextDecoder();
  try {
    expectedSequence = nextSequence(options.afterSequence);
    response = await fetcher(url, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        "Last-Event-ID": options.afterSequence,
      },
      signal: attemptController.signal,
    });
    if (!response.ok) {
      throw new StreamHttpError(response.status, await errorBody(response));
    }
    const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
    const mediaType = contentType.split(";", 1)[0]?.trim();
    if (mediaType !== "text/event-stream") {
      throw new SseProtocolError(
        `事件流 Content-Type 非法：${contentType || "<missing>"}`,
      );
    }
    if (response.body === null) {
      throw new StreamEndedError();
    }

    reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      parser.feed(decoder.decode(value, { stream: true }));
      if (terminal) {
        await reader.cancel("run 已进入终态");
        break;
      }
    }
    if (!terminal) {
      const tail = decoder.decode();
      if (tail.length > 0) {
        parser.feed(tail);
      }
      if (terminal) {
        await reader.cancel("run 已进入终态");
      }
    }
  } catch (error) {
    if (reader !== undefined) {
      await reader.cancel(error).catch(() => undefined);
    } else if (response?.body !== null && response?.body !== undefined) {
      await response.body.cancel(error).catch(() => undefined);
    }
    throw error;
  } finally {
    reader?.releaseLock();
    options.signal.removeEventListener("abort", abortAttempt);
    if (!attemptController.signal.aborted) {
      attemptController.abort("事件流 attempt 已结束");
    }
  }
  return { terminal };
}
