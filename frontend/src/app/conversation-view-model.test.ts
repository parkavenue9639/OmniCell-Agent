import { describe, expect, it } from "vitest";

import type { PersistedEvent } from "../generated/events-v1";
import { emptyRunProjection, type RunProjection } from "../projector/model";
import { buildConversationViewModel } from "./conversation-view-model";

const runId = "11111111-1111-4111-8111-111111111111";
const conversationId = "22222222-2222-4222-8222-222222222222";

function modelFor(events: readonly PersistedEvent[]) {
  const base = emptyRunProjection(runId, conversationId);
  const projection: RunProjection = {
    ...base,
    appliedSequence: String(events.length),
    status: "running",
    events,
  };
  return buildConversationViewModel({
    loading: false,
    conversations: [],
    artifacts: [],
    reviews: [],
    projection,
    pending: {
      createConversation: false,
      uploadDataset: false,
      submitRun: false,
      cancelRun: false,
    },
  });
}

describe("conversation event diagnostics", () => {
  it("filters empty tool-call bubbles and keeps bounded diagnostic metadata", () => {
    const events = [
      {
        schema_version: 1,
        event_id: "33333333-3333-4333-8333-333333333333",
        conversation_id: conversationId,
        run_id: runId,
        sequence: "1",
        occurred_at: "2026-07-23T10:04:05Z",
        type: "message.completed",
        payload: {
          message_id: "44444444-4444-4444-8444-444444444444",
          role: "assistant",
          content: "",
          turn_index: 1,
          has_tool_calls: true,
          stop_reason: null,
          content_artifact_id: null,
        },
      },
      {
        schema_version: 1,
        event_id: "55555555-5555-4555-8555-555555555555",
        conversation_id: conversationId,
        run_id: runId,
        sequence: "2",
        occurred_at: "2026-07-23T10:04:06Z",
        type: "capability.failed",
        payload: {
          capability_call_id: "66666666-6666-4666-8666-666666666666",
          capability_name: "deep_cell_annotation",
          task_id: "77777777-7777-4777-8777-777777777777",
          attempt: 2,
          error_code: "artifact_identity_mismatch",
          error_summary: "artifact 引用不完整",
          retryable: false,
        },
      },
    ] as const satisfies readonly PersistedEvent[];

    const model = modelFor(events);
    const failed = model.events[1];

    expect(model.timeline).toEqual([]);
    expect(failed.context).toBe("deep_cell_annotation");
    expect(failed.tone).toBe("danger");
    expect(Object.fromEntries(failed.metadata.map((item) => [item.label, item.value]))).toMatchObject({
      event_id: "55555555-5555-4555-8555-555555555555",
      run_id: runId,
      capability_call_id: "66666666-6666-4666-8666-666666666666",
      capability_name: "deep_cell_annotation",
      attempt: "2",
      error_code: "artifact_identity_mismatch",
      retryable: "false",
    });
    expect(failed.metadata.some((item) => item.label === "content")).toBe(false);
  });

  it("merges historical run timelines and renders the latest runtime state", () => {
    const firstMessage = {
      schema_version: 1,
      event_id: "88888888-8888-4888-8888-888888888881",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "1",
      occurred_at: "2026-07-23T10:00:00Z",
      type: "message.completed",
      payload: {
        message_id: "99999999-9999-4999-8999-999999999991",
        role: "user",
        content: "第一轮问题",
        turn_index: null,
        has_tool_calls: false,
        stop_reason: null,
        content_artifact_id: null,
      },
    } as const satisfies PersistedEvent;
    const secondRunId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const skillLoadId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb0";
    const runtimeId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const skillStarted = {
      schema_version: 1,
      event_id: "88888888-8888-4888-8888-888888888880",
      conversation_id: conversationId,
      run_id: secondRunId,
      sequence: "1",
      occurred_at: "2026-07-23T10:04:59Z",
      type: "skill.load_started",
      payload: {
        skill_load_id: skillLoadId,
        skill_name: "pca-clustering",
        resource_kind: "body",
        resource_name: null,
        purpose: "domain_method",
      },
    } as const satisfies PersistedEvent;
    const runtimeStarted = {
      schema_version: 1,
      event_id: "88888888-8888-4888-8888-888888888882",
      conversation_id: conversationId,
      run_id: secondRunId,
      sequence: "2",
      occurred_at: "2026-07-23T10:05:00Z",
      type: "runtime.command_started",
      payload: {
        runtime_command_id: runtimeId,
        capability_call_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        capability_name: "run_pca_clustering",
        task_id: null,
        attempt: 1,
        backend: "local-docker-cli",
        command: ["python"],
        code: "print('pca')",
        workdir: "/app/data",
        command_truncated: false,
        redacted: false,
      },
    } as const satisfies PersistedEvent;
    const firstProjection: RunProjection = {
      ...emptyRunProjection(runId, conversationId),
      appliedSequence: "1",
      events: [firstMessage],
    };
    const secondProjection: RunProjection = {
      ...emptyRunProjection(secondRunId, conversationId),
      appliedSequence: "2",
      events: [skillStarted, runtimeStarted],
      skillLoads: {
        [skillLoadId]: {
          skillLoadId,
          skillName: "pca-clustering",
          resourceKind: "body",
          resourceName: null,
          purpose: "domain_method",
          status: "completed",
          outcome: "loaded",
          contentBytes: 2048,
          errorCode: null,
          errorSummary: null,
        },
      },
      runtimeCommands: {
        [runtimeId]: {
          runtimeCommandId: runtimeId,
          capabilityCallId: runtimeStarted.payload.capability_call_id,
          capabilityName: runtimeStarted.payload.capability_name,
          attempt: 1,
          backend: "local-docker-cli",
          command: ["python"],
          code: "print('pca')",
          workdir: "/app/data",
          status: "completed",
          stdout: "done\n",
          stderr: "",
          exitCode: 0,
          durationMs: 120,
          commandTruncated: false,
          stdoutTruncated: false,
          stderrTruncated: false,
          redacted: false,
        },
      },
    };

    const model = buildConversationViewModel({
      loading: false,
      conversations: [],
      artifacts: [],
      reviews: [],
      projections: [firstProjection, secondProjection],
      pending: {
        createConversation: false,
        uploadDataset: false,
        submitRun: false,
        cancelRun: false,
      },
    });

    expect(model.timeline.map((item) => item.kind)).toEqual([
      "message",
      "skill",
      "runtime",
    ]);
    expect(model.timeline[0]).toMatchObject({ content: "第一轮问题" });
    expect(model.timeline[1]).toMatchObject({
      skillName: "pca-clustering",
      purposeLabel: "加载领域方法",
      resultSummary: "已加载 2.0 KiB 方法上下文",
    });
    expect(model.timeline[2]).toMatchObject({
      capability: "run_pca_clustering",
      stdout: "done\n",
      exitCode: 0,
    });
  });
});
