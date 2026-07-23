import Ajv2020, { type ErrorObject } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import eventContracts from "../../../contracts/events/v1.schema.json";
import type { PersistedEvent, TransientEvent } from "../generated/events-v1";

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);

function jsonSchema(schema: unknown): object {
  const clone = structuredClone(schema) as Record<string, unknown>;
  // `discriminator` 是生成器提示，不属于 draft 2020-12 验证关键字；
  // oneOf 内的 const 仍由 Ajv 严格校验。
  delete clone.discriminator;
  return clone;
}

const validatePersisted = ajv.compile(jsonSchema(eventContracts.persisted));
const validateTransient = ajv.compile(jsonSchema(eventContracts.transient));

export class EventContractError extends Error {
  readonly errors: readonly ErrorObject[];

  constructor(kind: "persisted" | "transient", errors: readonly ErrorObject[]) {
    super(`${kind} event 不符合 v1 契约`);
    this.name = "EventContractError";
    this.errors = errors;
  }
}

function assertWireVersion(
  kind: "persisted" | "transient",
  value: unknown,
): void {
  if (
    typeof value !== "object" ||
    value === null ||
    !("schema_version" in value) ||
    value.schema_version !== 1
  ) {
    throw new EventContractError(kind, []);
  }
}

export function parsePersistedEvent(value: unknown): PersistedEvent {
  assertWireVersion("persisted", value);
  if (!validatePersisted(value)) {
    throw new EventContractError("persisted", validatePersisted.errors ?? []);
  }
  return value as PersistedEvent;
}

export function parseTransientEvent(value: unknown): TransientEvent {
  assertWireVersion("transient", value);
  if (!validateTransient(value)) {
    throw new EventContractError("transient", validateTransient.errors ?? []);
  }
  return value as TransientEvent;
}
