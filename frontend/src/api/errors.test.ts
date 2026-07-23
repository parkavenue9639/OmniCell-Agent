import { describe, expect, it } from "vitest";

import { ApiError, normalizeApiError } from "./errors";

describe("normalizeApiError", () => {
  it("将 ErrorEnvelope 归一为 ApiError", () => {
    const response = new Response(null, { status: 409, statusText: "Conflict" });
    const error = normalizeApiError(
      {
        schema_version: 1,
        request_id: "0a2184bf-2c16-4a2c-a8e7-a8925779bb87",
        error: {
          code: "run_conflict",
          message: "run 状态冲突",
          retryable: false,
          details: [{ code: "invalid_status", message: "无法提交" }],
        },
      },
      response,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "run_conflict",
      requestId: "0a2184bf-2c16-4a2c-a8e7-a8925779bb87",
      retryable: false,
      message: "run 状态冲突",
    });
    expect(error.details).toHaveLength(1);
  });

  it("对非契约错误返回稳定的兜底错误", () => {
    const response = new Response(null, {
      status: 502,
      statusText: "Bad Gateway",
    });
    const error = normalizeApiError({ message: "unknown" }, response);

    expect(error).toMatchObject({
      status: 502,
      code: "unexpected_response",
      retryable: false,
      message: "Bad Gateway",
    });
  });
});
