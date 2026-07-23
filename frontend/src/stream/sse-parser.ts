import { createParser, type EventSourceMessage } from "eventsource-parser";

import type { PersistedEvent } from "../generated/events-v1";
import { parseSequence } from "../projector/sequence";
import { parsePersistedEvent } from "./event-validator";

export class SseProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SseProtocolError";
  }
}

export interface SseIdentity {
  readonly runId: string;
  readonly conversationId?: string;
}

function decodeMessage(
  message: EventSourceMessage,
  identity: SseIdentity,
): PersistedEvent {
  let value: unknown;
  try {
    value = JSON.parse(message.data) as unknown;
  } catch {
    throw new SseProtocolError("SSE data 不是合法 JSON");
  }
  const event = parsePersistedEvent(value);
  parseSequence(event.sequence);
  if (message.event !== undefined && message.event !== event.type) {
    throw new SseProtocolError("SSE event 名称与 payload type 不一致");
  }
  if (message.id !== undefined && message.id !== event.sequence) {
    throw new SseProtocolError("SSE id 与 payload sequence 不一致");
  }
  if (event.run_id !== identity.runId) {
    throw new SseProtocolError("SSE event 属于其他 run");
  }
  if (
    identity.conversationId !== undefined &&
    event.conversation_id !== identity.conversationId
  ) {
    throw new SseProtocolError("SSE event 属于其他 conversation");
  }
  return event;
}

export function createRunSseParser(
  identity: SseIdentity,
  onEvent: (event: PersistedEvent) => void,
  shouldStop: () => boolean = () => false,
) {
  return createParser({
    onEvent(message) {
      if (shouldStop()) {
        return;
      }
      onEvent(decodeMessage(message, identity));
    },
    onError(error) {
      if (shouldStop()) {
        return;
      }
      throw new SseProtocolError(`SSE frame 解析失败：${error.message}`);
    },
  });
}
