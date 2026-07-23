import type { components } from "../generated/openapi-v1";

export type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];
export type ErrorDetail = components["schemas"]["ErrorDetail"];

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly retryable: boolean;
  readonly details: ErrorDetail[];

  constructor(
    message: string,
    options: {
      status: number;
      code: string;
      requestId?: string;
      retryable?: boolean;
      details?: ErrorDetail[];
      cause?: unknown;
    },
  ) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId;
    this.retryable = options.retryable ?? false;
    this.details = options.details ?? [];
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isErrorDetail(value: unknown): value is ErrorDetail {
  return (
    isRecord(value) &&
    typeof value.code === "string" &&
    typeof value.message === "string" &&
    (value.field === undefined ||
      value.field === null ||
      typeof value.field === "string")
  );
}

export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (!isRecord(value) || value.schema_version !== 1) {
    return false;
  }
  if (typeof value.request_id !== "string" || !isRecord(value.error)) {
    return false;
  }
  return (
    typeof value.error.code === "string" &&
    typeof value.error.message === "string" &&
    typeof value.error.retryable === "boolean" &&
    (value.error.details === undefined ||
      (Array.isArray(value.error.details) &&
        value.error.details.every(isErrorDetail)))
  );
}

export function normalizeApiError(
  error: unknown,
  response: Response,
): ApiError {
  if (isErrorEnvelope(error)) {
    return new ApiError(error.error.message, {
      status: response.status,
      code: error.error.code,
      requestId: error.request_id,
      retryable: error.error.retryable,
      details: error.error.details,
      cause: error,
    });
  }

  return new ApiError(
    response.statusText || `HTTP 请求失败（${response.status}）`,
    {
      status: response.status,
      code: "unexpected_response",
      cause: error,
    },
  );
}
