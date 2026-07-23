import type { PersistedEvent } from "../generated/events-v1";
import {
  consumeRunEventStream,
  StreamEndedError,
  StreamGapError,
  StreamHttpError,
  type RunEventStreamOptions,
} from "./run-event-connection";

export type ConnectionPhase =
  | "connecting"
  | "live"
  | "reconnecting"
  | "closed";

export interface FollowRunEventsOptions
  extends Omit<RunEventStreamOptions, "afterSequence" | "onEvent"> {
  readonly getAppliedSequence: () => string;
  readonly isTerminal: () => boolean;
  readonly onEvent: (event: PersistedEvent) => void;
  readonly onPhaseChange: (phase: ConnectionPhase, error?: unknown) => void;
  readonly baseDelayMs?: number;
  readonly maxDelayMs?: number;
  readonly wait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
}

function defaultWait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function shouldRetry(error: unknown): boolean {
  if (error instanceof StreamGapError || error instanceof StreamEndedError) {
    return true;
  }
  if (error instanceof StreamHttpError) {
    return [408, 425, 429, 500, 502, 503, 504].includes(error.status);
  }
  return error instanceof TypeError;
}

export async function followRunEvents(
  options: FollowRunEventsOptions,
): Promise<void> {
  const baseDelay = options.baseDelayMs ?? 500;
  const maxDelay = options.maxDelayMs ?? 10_000;
  const wait = options.wait ?? defaultWait;
  let attempt = 0;

  while (!options.signal.aborted && !options.isTerminal()) {
    options.onPhaseChange(attempt === 0 ? "connecting" : "reconnecting");
    try {
      const result = await consumeRunEventStream({
        baseUrl: options.baseUrl,
        runId: options.runId,
        conversationId: options.conversationId,
        afterSequence: options.getAppliedSequence(),
        signal: options.signal,
        onEvent: (event) => {
          options.onPhaseChange("live");
          options.onEvent(event);
        },
        fetcher: options.fetcher,
      });
      if (result.terminal || options.isTerminal()) {
        options.onPhaseChange("closed");
        return;
      }
      throw new StreamEndedError();
    } catch (error) {
      if (options.signal.aborted) {
        options.onPhaseChange("closed");
        return;
      }
      if (!shouldRetry(error)) {
        options.onPhaseChange("closed", error);
        throw error;
      }
      options.onPhaseChange("reconnecting", error);
      const delay = Math.min(maxDelay, baseDelay * 2 ** attempt);
      attempt += 1;
      await wait(delay, options.signal);
    }
  }
  options.onPhaseChange("closed");
}
