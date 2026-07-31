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
  it("uses a temporary new-conversation label until backend summary arrives", () => {
    const conversation = {
      schema_version: 1,
      conversation_id: conversationId,
      title: null,
      status: "active",
      dataset_artifact_id: null,
      created_at: "2026-07-25T04:00:00Z",
      updated_at: "2026-07-25T04:00:00Z",
    } as const;

    const model = buildConversationViewModel({
      loading: false,
      conversations: [conversation],
      selectedConversation: conversation,
      artifacts: [],
      reviews: [],
      pending: {
        createConversation: false,
        uploadDataset: false,
        submitRun: false,
        cancelRun: false,
      },
    });

    expect(model.title).toBe("新分析对话");
    expect(model.conversations[0]?.title).toBe("新分析对话");
  });

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
          capability_name: "annotate_cell_clusters",
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
    expect(failed.context).toBe("annotate_cell_clusters");
    expect(failed.tone).toBe("danger");
    expect(Object.fromEntries(failed.metadata.map((item) => [item.label, item.value]))).toMatchObject({
      event_id: "55555555-5555-4555-8555-555555555555",
      run_id: runId,
      capability_call_id: "66666666-6666-4666-8666-666666666666",
      capability_name: "annotate_cell_clusters",
      attempt: "2",
      error_code: "artifact_identity_mismatch",
      retryable: "false",
    });
    expect(failed.metadata.some((item) => item.label === "content")).toBe(false);
  });

  it("将 capability 生命周期呈现为包含过程和结果的 Tool 调用", () => {
    const capabilityCallId = "66666666-6666-4666-8666-666666666666";
    const started = {
      schema_version: 1,
      event_id: "55555555-5555-4555-8555-555555555551",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "1",
      occurred_at: "2026-07-24T07:12:09.084Z",
      type: "capability.started",
      payload: {
        capability_call_id: capabilityCallId,
        capability_name: "inspect_dataset",
        task_id: null,
        attempt: 1,
      },
    } as const satisfies PersistedEvent;
    const completed = {
      schema_version: 1,
      event_id: "55555555-5555-4555-8555-555555555552",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "2",
      occurred_at: "2026-07-24T07:12:13.982Z",
      type: "capability.completed",
      payload: {
        capability_call_id: capabilityCallId,
        capability_name: "inspect_dataset",
        task_id: null,
        attempt: 1,
        result_status: null,
        artifact_ids: [],
        summary: "能力调用已返回",
      },
    } as const satisfies PersistedEvent;
    const projection: RunProjection = {
      ...emptyRunProjection(runId, conversationId),
      appliedSequence: "2",
      events: [started, completed],
      capabilities: {
        [capabilityCallId]: {
          capabilityCallId,
          capabilityName: "inspect_dataset",
          taskId: null,
          status: "completed",
          attempt: 1,
          summary: "能力调用已返回",
          errorSummary: null,
          artifactIds: [],
          progressCurrent: null,
          progressTotal: null,
          progressMessage: null,
        },
      },
    };

    const model = buildConversationViewModel({
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

    expect(model.timeline).toHaveLength(1);
    expect(model.timeline[0]).toMatchObject({
      kind: "tool",
      toolName: "inspect_dataset",
      title: "检查当前数据集",
      stateLabel: "调用完成",
      durationLabel: "4.90 s",
      resultSummary: "检查当前数据集已完成",
      process: [
        { label: "发起 Tool 调用", state: "completed" },
        { label: "Tool 返回结果", state: "completed" },
      ],
    });
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
        capability_name: "cluster_cells",
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
          skillVersion: null,
          resourceSha256: null,
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
      toolName: "cluster_cells",
      stdout: "done\n",
      exitCode: 0,
    });
  });

  it("renders Memory Plane identity and outcome without exposing body", () => {
    const event = {
      schema_version: 1,
      event_id: "99999999-9999-4999-8999-999999999999",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "1",
      occurred_at: "2026-07-26T08:00:00Z",
      type: "memory.context_loaded",
      payload: {
        snapshot_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        scope_key: "local-default",
        mode: "selected",
        outcome: "loaded",
        content_bytes: 42,
        inputs: [
          {
            item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            version_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            version_number: 2,
            kind: "response_preference",
            source_kind: "explicit",
            selection_reason: "selected",
          },
        ],
      },
    } as const satisfies PersistedEvent;

    const model = modelFor([event]);
    expect(model.timeline[0]).toMatchObject({
      kind: "memory",
      operation: "snapshot",
      mode: "selected",
      outcome: "loaded",
      title: "检查相关记忆",
      actionSummary: "选择与当前问题相关且仍然有效的记忆",
      identities: [
        {
          itemId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          versionId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          version: 2,
        },
      ],
    });
    expect(model.events[0]).toMatchObject({
      summary: "Memory Plane 已冻结当前 Run 的记忆上下文",
      context: "Memory Plane · selected · loaded",
    });
    const metadata = Object.fromEntries(
      model.events[0]!.metadata.map((item) => [item.label, item.value]),
    );
    expect(metadata).toMatchObject({
      snapshot_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      mode: "selected",
      outcome: "loaded",
    });
    expect(JSON.stringify(metadata)).not.toContain("使用中文回复");
    expect(metadata).not.toHaveProperty("content");
  });

  it("projects search, proposal, and forget Memory events as distinct identity-only activities", () => {
    const searchEvent = {
      schema_version: 1,
      event_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "1",
      occurred_at: "2026-07-26T08:01:00Z",
      type: "memory.search_completed",
      payload: {
        tool_call_id: "search-memory-1",
        outcome: "loaded",
        inputs: [
          {
            item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
            version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
            version_number: 3,
            kind: "project_context",
            source_kind: "explicit",
            selection_reason: "tool_search",
          },
        ],
      },
    } as const satisfies PersistedEvent;
    const proposalEvent = {
      schema_version: 1,
      event_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "2",
      occurred_at: "2026-07-26T08:02:00Z",
      type: "memory.proposal_created",
      payload: {
        tool_call_id: "propose-memory-1",
        status: "proposed",
        memory: {
          item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
          version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc2",
          version_number: 1,
          kind: "scientific_observation",
          source_kind: "proposed",
          selection_reason: "tool_search",
        },
      },
    } as const satisfies PersistedEvent;
    const forgetEvent = {
      schema_version: 1,
      event_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
      conversation_id: conversationId,
      run_id: runId,
      sequence: "3",
      occurred_at: "2026-07-26T08:03:00Z",
      type: "memory.forget_requested",
      payload: {
        tool_call_id: "forget-memory-1",
        status: "confirmation_required",
        memory: {
          item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3",
          version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc3",
          version_number: 4,
          kind: "response_preference",
          source_kind: "corrected",
          selection_reason: "tool_search",
        },
      },
    } as const satisfies PersistedEvent;

    const model = modelFor([searchEvent, proposalEvent, forgetEvent]);
    const memoryItems = model.timeline.filter((item) => item.kind === "memory");

    expect(memoryItems).toHaveLength(3);
    expect(memoryItems[0]).toMatchObject({
      operation: "search",
      title: "继续查找历史背景",
      stateLabel: "已找到",
      identities: [
        {
          itemId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
          versionId: "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
          version: 3,
        },
      ],
    });
    expect(memoryItems[1]).toMatchObject({
      operation: "proposal",
      title: "有一条记忆待确认",
      stateLabel: "待确认",
      description:
        "Agent 将你刚才的整条用户消息选为候选，没有摘要、抽取或改写",
      actionSummary:
        "候选已按整条来源消息保存；确认后才会用于未来相关对话",
      process: [
        { label: "检查内容是否适合长期保存", state: "completed" },
        { label: "保存整条来源消息为候选", state: "completed" },
        { label: "等待你的确认", state: "pending" },
      ],
      resultSummary: "候选已保存但尚未采用；请核对完整原文",
    });
    expect(memoryItems[2]).toMatchObject({
      operation: "forget",
      title: "确认忘记这条内容",
      stateLabel: "等待确认",
      resultSummary: "尚未忘记；请确认是否停止在未来对话中使用",
    });

    expect(model.events.map((event) => event.summary)).toEqual([
      "Agent 按需扩展了当前 Run 的记忆上下文",
      "Agent 创建了待确认的记忆提议",
      "Agent 请求确认遗忘一条记忆",
    ]);
    expect(model.events.map((event) => event.tone)).toEqual([
      "success",
      "warning",
      "warning",
    ]);
    for (const event of model.events) {
      const metadata = Object.fromEntries(
        event.metadata.map((item) => [item.label, item.value]),
      );
      expect(metadata).not.toHaveProperty("body");
      expect(metadata).not.toHaveProperty("content");
    }
    expect(JSON.stringify(memoryItems)).not.toContain("记忆正文");
  });

  it("does not offer correction for an already revoked memory", () => {
    const model = buildConversationViewModel({
      loading: false,
      conversations: [],
      artifacts: [],
      reviews: [],
      memory: {
        loading: false,
        commandsPending: false,
        items: [
          {
            schema_version: 1,
            memory_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb4",
            scope_key: "local-default",
            stable_key: "response.language",
            kind: "response_preference",
            status: "revoked",
            current_version: 2,
            version_id: "cccccccc-cccc-4ccc-8ccc-ccccccccccc4",
            content_sha256: "a".repeat(64),
            content: "回答时优先使用中文。",
            dataset_scope: null,
            source: null,
            expires_at: null,
            created_at: "2026-07-26T08:00:00Z",
            updated_at: "2026-07-26T08:03:00Z",
          },
        ],
      },
      pending: {
        createConversation: false,
        uploadDataset: false,
        submitRun: false,
        cancelRun: false,
      },
    });

    expect(model.memory.items[0]?.canCorrect).toBe(false);
    expect(model.memory.items[0]?.canPurge).toBe(true);
  });
});
