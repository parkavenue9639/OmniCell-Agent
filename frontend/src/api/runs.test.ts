import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "./client";
import {
  prepareRunSubmission,
  replayAllRunEvents,
  submitRun,
  type RunCreateResponse,
} from "./runs";

const runResponse: RunCreateResponse = {
  schema_version: 1,
  run: {
    schema_version: 1,
    run_id: "90158079-c788-4eef-b953-98516996a158",
    conversation_id: "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
    status: "pending",
    last_sequence: "1",
    created_at: "2026-07-23T08:00:00Z",
    updated_at: "2026-07-23T08:00:00Z",
  },
};

describe("run submission", () => {
  it("复用准备阶段生成的 Idempotency-Key", async () => {
    const requests: Request[] = [];
    const fetchMock = vi.fn(async (request: Request) => {
      requests.push(request.clone());
      return new Response(JSON.stringify(runResponse), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });
    const submission = prepareRunSubmission(
      "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
      { goal: "分析单细胞数据" },
      "stable-run-request-key",
    );

    await submitRun(submission, { client });
    await submitRun(submission, { client });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const request of requests) {
      expect(request.headers.get("Idempotency-Key")).toBe(
        "stable-run-request-key",
      );
      expect(await request.json()).toMatchObject({
        goal: "分析单细胞数据",
        request_key: "stable-run-request-key",
      });
    }
  });

  it("拒绝 header 与 body 中相互冲突的幂等键", () => {
    expect(() =>
      prepareRunSubmission(
        "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
        { goal: "分析", request_key: "body-key" },
        "header-key",
      ),
    ).toThrow("request_key 必须与 Idempotency-Key 一致");
  });

  it("replays every event page until has_more is false", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      const after = new URL(request.url).searchParams.get("after_sequence");
      const sequence = after === "1" ? "2" : "1";
      const type = sequence === "1" ? "run.created" : "run.started";
      return new Response(
        JSON.stringify({
          schema_version: 1,
          conversation_id:
            "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
          run_id: runResponse.run.run_id,
          events: [
            {
              schema_version: 1,
              event_id:
                sequence === "1"
                  ? "11111111-1111-4111-8111-111111111111"
                  : "22222222-2222-4222-8222-222222222222",
              conversation_id:
                "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
              run_id: runResponse.run.run_id,
              sequence,
              occurred_at: "2026-07-23T08:00:00Z",
              type,
              payload: {
                status: type === "run.created" ? "pending" : "running",
              },
            },
          ],
          has_more: sequence === "1",
          next_sequence: sequence,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });

    const replay = await replayAllRunEvents(runResponse.run.run_id, { client });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(replay.events.map((event) => event.sequence)).toEqual(["1", "2"]);
  });
});
