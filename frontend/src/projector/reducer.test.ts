import { describe, expect, it } from "vitest";

import type { PersistedEvent } from "../generated/events-v1";
import { emptyRunProjection } from "./model";
import { projectPersistedEvent } from "./reducer";
import { MAX_SEQUENCE, nextSequence, parseSequence, SequenceError } from "./sequence";

const RUN_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "22222222-2222-4222-8222-222222222222";

function event(
  sequence: string,
  type: string,
  payload: Record<string, unknown>,
  eventId = `33333333-3333-4333-8333-${sequence.padStart(12, "0")}`,
): PersistedEvent {
  return {
    schema_version: 1,
    event_id: eventId,
    conversation_id: CONVERSATION_ID,
    run_id: RUN_ID,
    sequence,
    occurred_at: "2026-07-23T00:00:00Z",
    type,
    payload,
  } as PersistedEvent;
}

describe("projectPersistedEvent", () => {
  it("保持超过 Number 安全范围的 sequence 精度", () => {
    const initial = {
      ...emptyRunProjection(RUN_ID, CONVERSATION_ID),
      appliedSequence: "9007199254740992",
    };
    const result = projectPersistedEvent(
      initial,
      event("9007199254740993", "run.started", { status: "running" }),
    );

    expect(result.kind).toBe("applied");
    expect(result.state.appliedSequence).toBe("9007199254740993");
  });

  it("接受 signed PostgreSQL BIGINT 上限并拒绝更大 sequence", () => {
    const maximum = MAX_SEQUENCE.toString();
    const initial = {
      ...emptyRunProjection(RUN_ID, CONVERSATION_ID),
      appliedSequence: (MAX_SEQUENCE - 1n).toString(),
    };
    const applied = projectPersistedEvent(
      initial,
      event(maximum, "run.started", { status: "running" }),
    );

    expect(applied.kind).toBe("applied");
    expect(applied.state.appliedSequence).toBe(maximum);
    expect(() => parseSequence("9223372036854775808")).toThrow(SequenceError);
    expect(() => nextSequence(maximum)).toThrow(SequenceError);
  });

  it("相同事件重放幂等且不修改旧 state", () => {
    const initial = emptyRunProjection(RUN_ID, CONVERSATION_ID);
    const first = event("1", "run.created", { status: "pending" });
    const applied = projectPersistedEvent(initial, first);
    const replayed = projectPersistedEvent(applied.state, first);

    expect(applied.kind).toBe("applied");
    expect(initial.events).toHaveLength(0);
    expect(replayed.kind).toBe("duplicate");
    expect(replayed.state).toBe(applied.state);
  });

  it("sequence gap 暂停投影", () => {
    const initial = emptyRunProjection(RUN_ID, CONVERSATION_ID);
    const result = projectPersistedEvent(
      initial,
      event("2", "run.started", { status: "running" }),
    );

    expect(result).toMatchObject({
      kind: "gap",
      expectedSequence: "1",
      receivedSequence: "2",
    });
    expect(result.state).toBe(initial);
  });

  it("相同 sequence 的不同事件报告冲突", () => {
    const initial = emptyRunProjection(RUN_ID, CONVERSATION_ID);
    const applied = projectPersistedEvent(
      initial,
      event("1", "run.created", { status: "pending" }),
    );
    const conflict = projectPersistedEvent(
      applied.state,
      event(
        "1",
        "run.started",
        { status: "running" },
        "44444444-4444-4444-8444-444444444444",
      ),
    );

    expect(conflict.kind).toBe("conflict");
  });

  it("budget.exhausted 不自行设置终态", () => {
    const initial = emptyRunProjection(RUN_ID, CONVERSATION_ID);
    const result = projectPersistedEvent(
      initial,
      event("1", "budget.exhausted", {
        budget: "turns",
        used: 8,
        limit: 8,
        unit: "count",
      }),
    );

    expect(result.kind).toBe("applied");
    expect(result.state.terminalStatus).toBeNull();
  });

  it("投影 Skill 加载结果并由取消终态收敛未完成活动", () => {
    const skillLoadId = "77777777-7777-4777-8777-777777777771";
    const runtimeCommandId = "77777777-7777-4777-8777-777777777772";
    const skillStarted = projectPersistedEvent(
      emptyRunProjection(RUN_ID, CONVERSATION_ID),
      event("1", "skill.load_started", {
        skill_load_id: skillLoadId,
        skill_name: "pca-clustering",
        resource_kind: "body",
        resource_name: null,
        purpose: "domain_method",
      }),
    );
    const taskCreated = projectPersistedEvent(
      skillStarted.state,
      event("2", "task.created", {
        task_id: "77777777-7777-4777-8777-777777777775",
        title: "运行 PCA",
        description: null,
        capability_name: "run_pca_clustering",
        status: "pending",
      }),
    );
    const capabilityStarted = projectPersistedEvent(
      taskCreated.state,
      event("3", "capability.started", {
        capability_call_id: "77777777-7777-4777-8777-777777777773",
        capability_name: "run_pca_clustering",
        task_id: "77777777-7777-4777-8777-777777777775",
        attempt: 1,
      }),
    );
    const commandStarted = projectPersistedEvent(
      capabilityStarted.state,
      event("4", "runtime.command_started", {
        runtime_command_id: runtimeCommandId,
        capability_call_id: "77777777-7777-4777-8777-777777777773",
        capability_name: "run_pca_clustering",
        task_id: null,
        attempt: 1,
        backend: "local-docker-cli",
        command: ["python"],
        code: null,
        workdir: "/app/data",
        command_truncated: false,
        redacted: false,
      }),
    );
    const cancelled = projectPersistedEvent(
      commandStarted.state,
      event("5", "run.cancelled", {
        status: "cancelled",
        reason: "用户取消",
      }),
    );

    expect(skillStarted.state.skillLoads[skillLoadId]).toMatchObject({
      status: "running",
      skillName: "pca-clustering",
      purpose: "domain_method",
    });
    expect(cancelled.state.skillLoads[skillLoadId].status).toBe("cancelled");
    expect(cancelled.state.runtimeCommands[runtimeCommandId].status).toBe(
      "cancelled",
    );
    expect(
      cancelled.state.capabilities[
        "77777777-7777-4777-8777-777777777773"
      ].status,
    ).toBe("cancelled");
    expect(
      cancelled.state.tasks["77777777-7777-4777-8777-777777777775"].status,
    ).toBe("cancelled");
  });

  it("Skill 完成事件只保存公开加载结果而不保存正文", () => {
    const skillLoadId = "77777777-7777-4777-8777-777777777774";
    const started = projectPersistedEvent(
      emptyRunProjection(RUN_ID, CONVERSATION_ID),
      event("1", "skill.load_started", {
        skill_load_id: skillLoadId,
        skill_name: "pca-clustering",
        resource_kind: "reference",
        resource_name: "quality-control.md",
        purpose: "validation_rules",
      }),
    );
    const completed = projectPersistedEvent(
      started.state,
      event("2", "skill.load_completed", {
        skill_load_id: skillLoadId,
        skill_name: "pca-clustering",
        resource_kind: "reference",
        resource_name: "quality-control.md",
        purpose: "validation_rules",
        outcome: "loaded",
        content_bytes: 2048,
      }),
    );

    expect(completed.state.skillLoads[skillLoadId]).toEqual({
      skillLoadId,
      skillName: "pca-clustering",
      resourceKind: "reference",
      resourceName: "quality-control.md",
      purpose: "validation_rules",
      status: "completed",
      outcome: "loaded",
      contentBytes: 2048,
      errorCode: null,
      errorSummary: null,
    });
    expect(
      "content" in completed.state.skillLoads[skillLoadId],
    ).toBe(false);
  });

  it("失败终态将仍在执行的 capability 与 task 收敛为失败", () => {
    const taskId = "77777777-7777-4777-8777-777777777776";
    const capabilityCallId = "77777777-7777-4777-8777-777777777777";
    const taskCreated = projectPersistedEvent(
      emptyRunProjection(RUN_ID, CONVERSATION_ID),
      event("1", "task.created", {
        task_id: taskId,
        title: "执行领域能力",
        description: null,
        capability_name: "run_pca_clustering",
        status: "pending",
      }),
    );
    const capabilityStarted = projectPersistedEvent(
      taskCreated.state,
      event("2", "capability.started", {
        capability_call_id: capabilityCallId,
        capability_name: "run_pca_clustering",
        task_id: taskId,
        attempt: 1,
      }),
    );
    const failed = projectPersistedEvent(
      capabilityStarted.state,
      event("3", "run.failed", {
        status: "failed",
        error_code: "run_execution_failed",
        error_summary: "运行未能完成",
        retryable: false,
      }),
    );

    expect(failed.state.capabilities[capabilityCallId].status).toBe("failed");
    expect(failed.state.tasks[taskId].status).toBe("failed");
  });

  it("terminal 后只允许同一事件幂等重放，其他事件均冲突且不推进 cursor", () => {
    const completed = event("1", "run.completed", {
      status: "completed",
      final_message_id: null,
      artifact_ids: [],
    });
    const terminal = projectPersistedEvent(
      emptyRunProjection(RUN_ID, CONVERSATION_ID),
      completed,
    );
    const duplicate = projectPersistedEvent(terminal.state, completed);
    const trailing = projectPersistedEvent(
      terminal.state,
      event("2", "message.completed", {
        message_id: "55555555-5555-4555-8555-555555555555",
        role: "assistant",
        content: "不应应用",
      }),
    );

    expect(duplicate.kind).toBe("duplicate");
    expect(trailing.kind).toBe("conflict");
    expect(trailing.state).toBe(terminal.state);
    expect(trailing.state.appliedSequence).toBe("1");
    expect(trailing.state.events).toHaveLength(1);

    const mutatedReplay = projectPersistedEvent(terminal.state, {
      ...completed,
      payload: {
        status: "completed",
        final_message_id: null,
        artifact_ids: ["66666666-6666-4666-8666-666666666666"],
      },
    } as PersistedEvent);
    expect(mutatedReplay.kind).toBe("conflict");
    expect(mutatedReplay.state).toBe(terminal.state);
  });

  it("review interruption 恢复启动与最终完成都会清理旧 stop reason", () => {
    const reviewId = "77777777-7777-4777-8777-777777777777";
    const interrupted = projectPersistedEvent(
      emptyRunProjection(RUN_ID, CONVERSATION_ID),
      event("1", "run.interrupted", {
        status: "review_required",
        reason: "等待人工确认",
        resumable: true,
        review_id: reviewId,
      }),
    );
    expect(interrupted.state.stopReason).toBe("等待人工确认");

    const resolved = projectPersistedEvent(
      interrupted.state,
      event("2", "review.resolved", {
        review_id: reviewId,
        status: "resolved",
        decision: "approve",
        comment: "继续执行",
      }),
    );
    expect(resolved.state.stopReason).toBe("等待人工确认");

    const resumed = projectPersistedEvent(
      resolved.state,
      event("3", "run.started", { status: "running" }),
    );
    expect(resumed.state.stopReason).toBeNull();
    expect(resumed.state.status).toBe("running");

    const completed = projectPersistedEvent(
      resolved.state,
      event("3", "run.completed", {
        status: "completed",
        final_message_id: null,
        artifact_ids: [],
      }),
    );
    expect(completed.state.stopReason).toBeNull();
    expect(completed.state.terminalStatus).toBe("completed");
  });
});
