import type {
  Artifact,
  Conversation,
  Review,
  Run,
} from "../api";
import type {
  ArtifactViewModel,
  CapabilityViewModel,
  ConnectionState,
  ConversationWorkspaceViewModel,
  EventViewModel,
  ReviewViewModel,
  RunState,
  TaskViewModel,
  TimelineItem,
  WorkItemState,
} from "../features/conversations";
import type { RunProjection } from "../projector/model";
import type { PersistedEvent } from "../generated/events-v1";
import type { ConnectionState as StreamConnectionState } from "../stores/connections";

const RUN_LABELS: Record<RunState, string> = {
  idle: "尚未运行",
  pending: "等待执行",
  running: "运行中",
  review_required: "等待审核",
  cancelling: "正在取消",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const WORK_LABELS: Record<WorkItemState, string> = {
  pending: "等待中",
  running: "执行中",
  review_required: "等待审核",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const TERMINAL_RUNS = new Set<RunState>(["completed", "failed", "cancelled"]);

function runState(value: string | undefined): RunState {
  switch (value) {
    case "pending":
    case "running":
    case "review_required":
    case "cancelling":
    case "completed":
    case "failed":
    case "cancelled":
      return value;
    default:
      return "idle";
  }
}

function workState(value: string): WorkItemState {
  if (value === "in_progress" || value === "retrying") {
    return "running";
  }
  switch (value) {
    case "pending":
    case "running":
    case "review_required":
    case "completed":
    case "failed":
    case "cancelled":
      return value;
    default:
      return "pending";
  }
}

function dateLabel(value: string | undefined): string {
  if (value === undefined) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function artifactName(artifact: Artifact): string {
  const filename = artifact.metadata?.filename;
  return typeof filename === "string" && filename.length > 0
    ? filename
    : `${artifact.kind} · ${artifact.artifact_id.slice(0, 8)}`;
}

function capabilityFamily(
  name: string,
): "graph_a" | "graph_b" | "tool" {
  if (name === "single_cell_analysis") return "graph_a";
  if (name === "deep_cell_annotation") return "graph_b";
  return "tool";
}

function capabilityTitle(name: string): string {
  const labels: Record<string, string> = {
    single_cell_analysis: "单细胞分析工作流",
    deep_cell_annotation: "深度细胞类型注释",
    inspect_single_cell_context: "检查单细胞上下文",
    inspect_marker_contract: "检查 Marker Contract",
  };
  return labels[name] ?? name;
}

function skillResourceLabel(
  kind: "body" | "reference" | "example",
  name: string | null,
): string {
  if (kind === "body") return "Skill 正文";
  const label = kind === "reference" ? "Reference" : "Example";
  return name ? `${label} · ${name}` : label;
}

function skillPurposeLabel(
  purpose:
    | "domain_method"
    | "validation_rules"
    | "workflow_guidance"
    | "reference_lookup"
    | "example_lookup",
): string {
  const labels = {
    domain_method: "加载领域方法",
    validation_rules: "加载验证规则",
    workflow_guidance: "加载工作流指引",
    reference_lookup: "查阅参考资料",
    example_lookup: "查阅使用示例",
  } as const;
  return labels[purpose];
}

function eventSummary(type: string | undefined): string {
  const labels: Record<string, string> = {
    "run.created": "运行已创建",
    "run.started": "运行开始执行",
    "agent.turn_started": "Agent 开始新一轮推理",
    "message.completed": "消息已持久化",
    "task.created": "任务已创建",
    "task.updated": "任务状态已更新",
    "skill.load_started": "Skill 资源开始加载",
    "skill.load_completed": "Skill 资源加载完成",
    "skill.load_failed": "Skill 资源加载失败",
    "capability.started": "能力调用开始",
    "capability.retrying": "能力调用准备重试",
    "capability.progress": "能力仍在执行",
    "capability.completed": "能力调用已返回",
    "capability.failed": "能力调用失败",
    "runtime.command_started": "容器命令开始执行",
    "runtime.output": "容器产生执行输出",
    "runtime.command_completed": "容器命令执行结束",
    "artifact.created": "产物已登记",
    "review.requested": "需要人工审核",
    "review.resolved": "审核已处理",
    "budget.exhausted": "预算已耗尽",
    "run.cancel_requested": "已提交取消命令",
    "run.interrupted": "运行已中断",
    "run.completed": "运行已完成",
    "run.failed": "运行失败",
    "run.cancelled": "运行已取消",
  };
  return labels[type ?? ""] ?? "已收到类型化事件";
}

function eventTone(
  type: string | undefined,
): "neutral" | "active" | "success" | "warning" | "danger" {
  if (
    type === "skill.load_failed" ||
    type === "capability.failed" ||
    type === "run.failed"
  ) {
    return "danger";
  }
  if (
    type === "run.cancel_requested" ||
    type === "run.cancelled" ||
    type === "run.interrupted" ||
    type === "budget.exhausted"
  ) {
    return "warning";
  }
  if (
    type === "run.completed" ||
    type === "skill.load_completed" ||
    type === "capability.completed" ||
    type === "artifact.created" ||
    type === "review.resolved"
  ) {
    return "success";
  }
  if (
    type === "run.started" ||
    type === "agent.turn_started" ||
    type === "skill.load_started" ||
    type === "capability.started" ||
    type === "capability.retrying" ||
    type === "capability.progress" ||
    type === "runtime.command_started" ||
    type === "runtime.output"
  ) {
    return "active";
  }
  return "neutral";
}

function diagnosticValue(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(", ") || "[]";
  }
  return undefined;
}

function eventMetadata(event: PersistedEvent): EventViewModel["metadata"] {
  const rows: Array<{ label: string; value: string }> = [];
  const append = (label: string, value: unknown) => {
    const rendered = diagnosticValue(value);
    if (rendered !== undefined) rows.push({ label, value: rendered });
  };

  append("event_id", event.event_id);
  append("run_id", event.run_id);
  append("conversation_id", event.conversation_id);
  append("schema_version", event.schema_version);
  append("occurred_at", event.occurred_at);

  const payload = event.payload as unknown as Record<string, unknown>;
  switch (event.type) {
    case "agent.turn_started":
      append("turn_index", payload.turn_index);
      append("remaining_turns", payload.remaining_turns);
      break;
    case "message.completed":
      append("message_id", payload.message_id);
      append("role", payload.role);
      append("turn_index", payload.turn_index);
      append("has_tool_calls", payload.has_tool_calls);
      append("stop_reason", payload.stop_reason);
      append("content_artifact_id", payload.content_artifact_id);
      break;
    case "task.created":
    case "task.updated":
      append("task_id", payload.task_id);
      append("capability_name", payload.capability_name);
      append("status", payload.status);
      append("summary", payload.summary);
      break;
    case "skill.load_started":
    case "skill.load_completed":
    case "skill.load_failed":
      append("skill_load_id", payload.skill_load_id);
      append("skill_name", payload.skill_name);
      append("resource_kind", payload.resource_kind);
      append("resource_name", payload.resource_name);
      append("purpose", payload.purpose);
      append("outcome", payload.outcome);
      append("content_bytes", payload.content_bytes);
      append("error_code", payload.error_code);
      append("error_summary", payload.error_summary);
      break;
    case "capability.started":
    case "capability.retrying":
    case "capability.progress":
    case "capability.completed":
    case "capability.failed":
      append("capability_call_id", payload.capability_call_id);
      append("capability_name", payload.capability_name);
      append("task_id", payload.task_id);
      append("attempt", payload.attempt);
      append("next_attempt", payload.next_attempt);
      append("result_status", payload.result_status);
      append("artifact_ids", payload.artifact_ids);
      append("error_code", payload.error_code);
      append("error_summary", payload.error_summary);
      append("retryable", payload.retryable);
      append("reason", payload.reason);
      append("stage", payload.stage);
      append("current", payload.current);
      append("total", payload.total);
      append("message", payload.message);
      break;
    case "runtime.command_started":
    case "runtime.output":
    case "runtime.command_completed":
      append("runtime_command_id", payload.runtime_command_id);
      append("capability_call_id", payload.capability_call_id);
      append("capability_name", payload.capability_name);
      append("task_id", payload.task_id);
      append("attempt", payload.attempt);
      append("backend", payload.backend);
      append("command", payload.command);
      append("workdir", payload.workdir);
      append("command_truncated", payload.command_truncated);
      append("stream", payload.stream);
      append("index", payload.index);
      append("encoding", payload.encoding);
      append("truncated", payload.truncated);
      append("outcome", payload.outcome);
      append("exit_code", payload.exit_code);
      append("duration_ms", payload.duration_ms);
      append("stdout_observed_bytes", payload.stdout_observed_bytes);
      append("stdout_published_bytes", payload.stdout_published_bytes);
      append("stderr_observed_bytes", payload.stderr_observed_bytes);
      append("stderr_published_bytes", payload.stderr_published_bytes);
      append("stdout_truncated", payload.stdout_truncated);
      append("stderr_truncated", payload.stderr_truncated);
      append("redacted", payload.redacted);
      break;
    case "artifact.created":
      append("artifact_id", payload.artifact_id);
      append("kind", payload.kind);
      append("media_type", payload.media_type);
      append("size_bytes", payload.size_bytes);
      append("sha256", payload.sha256);
      break;
    case "review.requested":
    case "review.resolved":
      append("review_id", payload.review_id);
      append("task_id", payload.task_id);
      append("status", payload.status);
      append("decision", payload.decision);
      append("comment", payload.comment);
      break;
    case "budget.exhausted":
      append("budget", payload.budget);
      append("limit", payload.limit);
      append("used", payload.used);
      append("unit", payload.unit);
      break;
    case "run.created":
    case "run.started":
    case "run.cancel_requested":
    case "run.interrupted":
    case "run.completed":
    case "run.failed":
    case "run.cancelled":
      append("status", payload.status);
      append("review_id", payload.review_id);
      append("resumable", payload.resumable);
      append("artifact_ids", payload.artifact_ids);
      append("final_message_id", payload.final_message_id);
      append("error_code", payload.error_code);
      append("error_summary", payload.error_summary);
      append("retryable", payload.retryable);
      append("reason", payload.reason);
      break;
  }
  return rows;
}

function eventContext(event: PersistedEvent): string | undefined {
  const payload = event.payload as unknown as Record<string, unknown>;
  const value =
    payload.capability_name ??
    payload.skill_name ??
    payload.error_code ??
    payload.kind ??
    payload.role ??
    payload.status;
  return diagnosticValue(value);
}

function previewForArtifact(
  artifact: Artifact | undefined,
  fallback: { readonly media_type?: string | null; readonly size_bytes: number },
): { mode: "image" | "json" | "text" | "table" | "none"; reason?: string } {
  const mediaType = artifact?.media_type ?? fallback.media_type ?? undefined;
  const size = artifact?.size_bytes ?? fallback.size_bytes;
  if (
    (mediaType === "image/png" || mediaType === "image/jpeg") &&
    size <= 4 * 1024 * 1024
  ) {
    return { mode: "image" };
  }
  if (size > 256 * 1024) {
    return { mode: "none", reason: "内容较大，仅提供 metadata 与下载" };
  }
  if (mediaType === "application/json") return { mode: "json" };
  if (mediaType === "text/csv" || mediaType === "text/tab-separated-values") {
    return { mode: "table" };
  }
  if (mediaType === "text/plain" || mediaType === "text/markdown") {
    return { mode: "text" };
  }
  return { mode: "none", reason: "该类型不在安全内联预览列表中" };
}

function runTimeline(
  projection: RunProjection | undefined,
  pendingReviewId: string | undefined,
  artifactsById: ReadonlyMap<string, Artifact>,
): TimelineItem[] {
  if (projection === undefined) return [];
  const items: TimelineItem[] = [];
  for (const event of projection.events) {
    const when = dateLabel(event.occurred_at);
    if (event.type === "run.started") {
      items.push({
        id: event.event_id,
        kind: "notice",
        tone: "neutral",
        title: `Run ${projection.runId.slice(0, 8)} 开始`,
        description: "以下活动来自同一轮可恢复执行。",
        occurredAtLabel: when,
      });
    } else if (
      event.type === "message.completed" &&
      (event.payload.role === "user" || event.payload.role === "assistant")
    ) {
      const content = event.payload.content.trim();
      if (!content) continue;
      items.push({
        id: event.event_id,
        kind: "message",
        role: event.payload.role,
        authorLabel: event.payload.role === "user" ? "你" : "OmniCell Agent",
        content,
        occurredAtLabel: when,
      });
    } else if (event.type === "capability.started") {
      const latest = projection.capabilities[event.payload.capability_call_id];
      const name = event.payload.capability_name;
      const state = workState(latest?.status ?? "running");
      items.push({
        id: event.event_id,
        kind: "capability",
        capability: name,
        family: capabilityFamily(name),
        title: capabilityTitle(name),
        description: "Agent 按当前任务上下文调用结构化领域能力。",
        state,
        stateLabel: WORK_LABELS[state],
        occurredAtLabel: when,
        resultSummary: latest?.summary ?? latest?.errorSummary ?? undefined,
        progressLabel: latest?.progressMessage
          ? `${latest.progressMessage} · #${latest.progressCurrent ?? 0}`
          : undefined,
      });
    } else if (event.type === "task.created") {
      const latest = projection.tasks[event.payload.task_id];
      const state = workState(latest?.status ?? event.payload.status);
      items.push({
        id: event.event_id,
        kind: "task",
        title: event.payload.title,
        description:
          latest?.summary ??
          latest?.description ??
          event.payload.description ??
          undefined,
        capability:
          latest?.capabilityName ??
          event.payload.capability_name ??
          undefined,
        state,
        stateLabel: WORK_LABELS[state],
        occurredAtLabel: when,
      });
    } else if (event.type === "skill.load_started") {
      const latest = projection.skillLoads[event.payload.skill_load_id];
      if (latest === undefined) continue;
      const stateLabel = {
        running: "加载中",
        completed: "已加载",
        failed: "加载失败",
        cancelled: "已取消",
      }[latest.status];
      const resultSummary =
        latest.status === "completed"
          ? latest.outcome === "already_loaded"
            ? "该资源已在当前上下文中，直接复用"
            : `已加载 ${sizeLabel(latest.contentBytes ?? 0)} 方法上下文`
          : latest.errorSummary ?? undefined;
      items.push({
        id: event.event_id,
        kind: "skill",
        skillName: latest.skillName,
        resourceLabel: skillResourceLabel(
          latest.resourceKind,
          latest.resourceName,
        ),
        purposeLabel: skillPurposeLabel(latest.purpose),
        state: latest.status,
        stateLabel,
        resultSummary,
        occurredAtLabel: when,
      });
    } else if (event.type === "runtime.command_started") {
      const latest =
        projection.runtimeCommands[event.payload.runtime_command_id];
      if (latest === undefined) continue;
      items.push({
        id: event.event_id,
        kind: "runtime",
        runtimeCommandId: latest.runtimeCommandId,
        capability: latest.capabilityName,
        backend: latest.backend,
        command: latest.command,
        code: latest.code ?? undefined,
        workdir: latest.workdir,
        state: latest.status,
        stdout: latest.stdout,
        stderr: latest.stderr,
        exitCode: latest.exitCode ?? undefined,
        durationLabel:
          latest.durationMs === null
            ? undefined
            : `${(latest.durationMs / 1_000).toFixed(2)} s`,
        commandTruncated: latest.commandTruncated,
        stdoutTruncated: latest.stdoutTruncated,
        stderrTruncated: latest.stderrTruncated,
        redacted: latest.redacted,
        occurredAtLabel: when,
      });
    } else if (event.type === "artifact.created") {
      const artifact = artifactsById.get(event.payload.artifact_id);
      const preview = previewForArtifact(artifact, event.payload);
      items.push({
        id: event.event_id,
        kind: "artifact",
        artifactId: event.payload.artifact_id,
        name: artifact
          ? artifactName(artifact)
          : `${event.payload.kind} · ${event.payload.artifact_id.slice(0, 8)}`,
        artifactKind: event.payload.kind,
        mediaType: event.payload.media_type ?? undefined,
        sizeLabel: sizeLabel(event.payload.size_bytes),
        previewMode: preview.mode,
        previewReason: preview.reason,
        occurredAtLabel: when,
      });
    } else if (event.type === "review.requested") {
      const latest = projection.reviews[event.payload.review_id];
      items.push({
        id: event.event_id,
        kind: "review",
        reviewId: event.payload.review_id,
        title: "需要你的确认",
        description: event.payload.prompt,
        state:
          latest?.status === "approved"
            ? "approved"
            : latest?.status === "rejected"
              ? "rejected"
              : "pending",
        decisionPending: pendingReviewId === event.payload.review_id,
        occurredAtLabel: when,
      });
    } else if (
      event.type === "run.completed" ||
      event.type === "run.interrupted" ||
      event.type === "run.failed" ||
      event.type === "run.cancelled"
    ) {
      const payload = event.payload as {
        readonly reason?: string | null;
        readonly error_summary?: string;
      };
      items.push({
        id: event.event_id,
        kind: "notice",
        tone:
          event.type === "run.failed"
            ? "error"
            : event.type === "run.completed"
              ? "neutral"
              : "warning",
        title: eventSummary(event.type),
        description: payload.error_summary ?? payload.reason ?? undefined,
        occurredAtLabel: when,
      });
    }
  }
  return items;
}

function conversationTimeline(
  projections: readonly RunProjection[],
  pendingReviewId: string | undefined,
  artifacts: readonly Artifact[],
): TimelineItem[] {
  const artifactsById = new Map(
    artifacts.map((artifact) => [artifact.artifact_id, artifact]),
  );
  return projections.flatMap((projection) =>
    runTimeline(projection, pendingReviewId, artifactsById),
  );
}

function taskModels(projection: RunProjection | undefined): TaskViewModel[] {
  if (projection === undefined) return [];
  return Object.values(projection.tasks).map((task) => {
    const state = workState(task.status);
    return {
      id: task.taskId,
      title: task.title,
      description: task.summary ?? task.description ?? undefined,
      state,
      stateLabel: WORK_LABELS[state],
    };
  });
}

function capabilityModels(
  projection: RunProjection | undefined,
): CapabilityViewModel[] {
  if (projection === undefined) return [];
  return Object.values(projection.capabilities).map((capability) => {
    const state = workState(capability.status);
    return {
      id: capability.capabilityCallId,
      name: capability.capabilityName,
      family: capabilityFamily(capability.capabilityName),
      title: capabilityTitle(capability.capabilityName),
      description:
        capability.summary ?? capability.errorSummary ?? "等待结构化结果",
      state,
      stateLabel: WORK_LABELS[state],
      invocationCount: capability.attempt,
    };
  });
}

function reviewModels(
  reviews: readonly Review[],
  projection: RunProjection | undefined,
  pendingReviewId: string | undefined,
): ReviewViewModel[] {
  return reviews.map((review) => {
    const projected = projection?.reviews[review.review_id];
    const status = projected?.status ?? review.status;
    return {
      id: review.review_id,
      title: "人工审核",
      description: review.prompt,
      capabilityLabel: "Agent Tool Policy",
      state:
        status === "approved"
          ? "approved"
          : status === "rejected"
            ? "rejected"
            : "pending",
      decisionPending: pendingReviewId === review.review_id,
      decisionLabel: review.comment ?? projected?.comment ?? undefined,
    };
  });
}

function artifactModels(
  artifacts: readonly Artifact[],
  pendingArtifactId: string | undefined,
): ArtifactViewModel[] {
  return artifacts.map((artifact) => ({
    id: artifact.artifact_id,
    name: artifactName(artifact),
    kindLabel: artifact.kind,
    sizeLabel: sizeLabel(artifact.size_bytes),
    createdAtLabel: dateLabel(artifact.created_at),
    canDownload: true,
    downloadPending: pendingArtifactId === artifact.artifact_id,
  }));
}

function eventModels(projection: RunProjection | undefined): EventViewModel[] {
  if (projection === undefined) return [];
  return projection.events.map((event) => ({
    id: event.event_id,
    sequence: event.sequence,
    type: event.type ?? "unknown",
    occurredAtLabel: dateLabel(event.occurred_at),
    occurredAtIso: event.occurred_at,
    summary: eventSummary(event.type),
    context: eventContext(event),
    tone: eventTone(event.type),
    metadata: eventMetadata(event),
  }));
}

function connectionModel(
  state: StreamConnectionState | undefined,
): { connection: ConnectionState; label: string } {
  if (state?.phase === "live") {
    return { connection: "connected", label: "事件已同步" };
  }
  if (state?.phase === "connecting" || state?.phase === "reconnecting") {
    return { connection: "reconnecting", label: "正在从最后游标恢复" };
  }
  if (state?.error) {
    return { connection: "offline", label: state.error };
  }
  return { connection: "connected", label: "尚无活跃事件流" };
}

export interface BuildConversationViewModelOptions {
  readonly loading: boolean;
  readonly errorMessage?: string;
  readonly commandErrorMessage?: string;
  readonly conversations: readonly Conversation[];
  readonly selectedConversation?: Conversation;
  readonly selectedDatasetId?: string;
  readonly artifacts: readonly Artifact[];
  readonly reviews: readonly Review[];
  readonly runs?: readonly Run[];
  readonly run?: Run;
  readonly projections?: readonly RunProjection[];
  readonly projection?: RunProjection;
  readonly connection?: StreamConnectionState;
  readonly pending: {
    readonly createConversation: boolean;
    readonly uploadDataset: boolean;
    readonly submitRun: boolean;
    readonly cancelRun: boolean;
    readonly reviewId?: string;
    readonly artifactId?: string;
  };
}

export function buildConversationViewModel(
  options: BuildConversationViewModelOptions,
): ConversationWorkspaceViewModel {
  const currentRunState = runState(options.projection?.status ?? options.run?.status);
  const connection = connectionModel(options.connection);
  const datasets = options.artifacts.filter((artifact) => artifact.kind === "dataset");
  const selectedId = options.selectedDatasetId;
  const projections =
    options.projections ??
    (options.projection === undefined ? [] : [options.projection]);

  return {
    viewState: options.loading
      ? "loading"
      : options.errorMessage
        ? "error"
        : options.selectedConversation === undefined
          ? "empty"
          : "ready",
    errorMessage: options.errorMessage,
    commandErrorMessage: options.commandErrorMessage,
    connection: connection.connection,
    connectionLabel: connection.label,
    conversations: options.conversations.map((conversation) => ({
      id: conversation.conversation_id,
      title: conversation.title ?? "未命名对话",
      updatedAtLabel: dateLabel(conversation.updated_at),
    })),
    selectedConversationId: options.selectedConversation?.conversation_id,
    datasets: datasets.map((artifact) => ({
      artifactId: artifact.artifact_id,
      name: artifactName(artifact),
      detail: artifact.media_type ?? artifact.kind,
      sizeLabel: sizeLabel(artifact.size_bytes),
    })),
    selectedDatasetId: selectedId ?? undefined,
    title: options.selectedConversation?.title ?? "开始新的单细胞分析",
    subtitle: options.run
      ? `Run ${options.run.run_id.slice(0, 8)} · sequence ${options.projection?.appliedSequence ?? options.run.last_sequence}`
      : "选择数据集并描述目标，Agent 会按需调用 Graph A、Graph B 或领域 Tool。",
    run: options.run
      ? {
          id: options.run.run_id,
          state: currentRunState,
          stateLabel: RUN_LABELS[currentRunState],
          startedAtLabel: dateLabel(options.run.started_at ?? options.run.created_at),
          terminalSummary:
            options.projection?.stopReason ?? options.run.error_summary ?? undefined,
          canCancel:
            !options.pending.cancelRun &&
            !TERMINAL_RUNS.has(currentRunState) &&
            currentRunState !== "cancelling",
        }
      : undefined,
    timeline: conversationTimeline(
      projections,
      options.pending.reviewId,
      options.artifacts,
    ),
    tasks: projections.flatMap((projection) => taskModels(projection)),
    capabilities: projections.flatMap((projection) =>
      capabilityModels(projection),
    ),
    reviews: reviewModels(
      options.reviews,
      options.projection,
      options.pending.reviewId,
    ),
    artifacts: artifactModels(options.artifacts, options.pending.artifactId),
    events: projections.flatMap((projection) => eventModels(projection)),
    commands: {
      createConversationPending: options.pending.createConversation,
      importDatasetPending: options.pending.uploadDataset,
      cancelRunPending: options.pending.cancelRun,
    },
    composer: {
      placeholder: "描述你的分析目标或下一步问题…",
      disabled:
        options.selectedConversation === undefined ||
        options.pending.submitRun ||
        (options.run !== undefined && !TERMINAL_RUNS.has(currentRunState)),
      disabledReason: options.selectedConversation === undefined
        ? "请先创建或选择对话"
        : options.pending.submitRun
        ? "正在提交命令"
        : options.run !== undefined && !TERMINAL_RUNS.has(currentRunState)
          ? "当前 run 结束或进入审核后才能提交下一轮"
          : undefined,
    },
  };
}
