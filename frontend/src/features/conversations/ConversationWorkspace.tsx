import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  ActivityProcessState,
  ArtifactViewModel,
  ConnectionState,
  ConversationWorkspaceActions,
  ConversationWorkspaceViewModel,
  EventViewModel,
  ReviewViewModel,
  RunState,
  TaskViewModel,
  TimelineArtifactItem,
  TimelineItem,
  TimelineMessageItem,
  TimelineNoticeItem,
  TimelineReviewItem,
  TimelineRuntimeItem,
  TimelineSkillItem,
  TimelineTaskItem,
  TimelineToolItem,
  ToolExecutionViewModel,
  WorkItemState,
} from "./view-model";

type InspectorTab =
  "tasks" | "toolExecutions" | "reviews" | "artifacts" | "events";

export interface ConversationWorkspaceProps {
  model: ConversationWorkspaceViewModel;
  actions?: ConversationWorkspaceActions;
}

const runTone: Record<RunState, string> = {
  idle: "neutral",
  pending: "info",
  running: "active",
  review_required: "warning",
  cancelling: "warning",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

const workTone: Record<WorkItemState, string> = {
  pending: "neutral",
  running: "active",
  review_required: "warning",
  completed: "success",
  failed: "danger",
  interrupted: "warning",
  cancelled: "neutral",
};

const tabLabels: Record<InspectorTab, string> = {
  tasks: "任务",
  toolExecutions: "Tool",
  reviews: "审核",
  artifacts: "产物",
  events: "事件",
};

function Icon({
  name,
}: {
  name: "plus" | "database" | "message" | "panel" | "download" | "send" | "x";
}) {
  const paths: Record<typeof name, ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />,
    database: (
      <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6m-16 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    ),
    message: <path d="M5 5h14v11H9l-4 4V5Z" />,
    panel: <path d="M4 4h16v16H4V4Zm10 0v16" />,
    download: <path d="M12 3v12m0 0 4-4m-4 4-4-4M5 20h14" />,
    send: <path d="m4 4 17 8-17 8 3-8-3-8Zm3 8h14" />,
    x: <path d="m6 6 12 12M18 6 6 18" />,
  };
  return (
    <svg
      aria-hidden="true"
      className="oc-icon"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

function StatusPill({
  label,
  tone,
  pulse = false,
}: {
  label: string;
  tone: string;
  pulse?: boolean;
}) {
  return (
    <span className="oc-status-pill" data-tone={tone}>
      <span className={pulse ? "oc-status-dot is-pulsing" : "oc-status-dot"} />
      {label}
    </span>
  );
}

function ConnectionBanner({
  state,
  label,
}: {
  state: ConnectionState;
  label: string;
}) {
  if (state === "connected") return null;
  return (
    <div className="oc-connection-banner" data-state={state} role="status">
      <span
        className={state === "reconnecting" ? "oc-spinner" : "oc-offline-mark"}
      />
      <div>
        <strong>
          {state === "reconnecting" ? "正在恢复事件连接" : "事件连接已离线"}
        </strong>
        <span>{label}。已提交的运行不会因页面断线自动取消。</span>
      </div>
    </div>
  );
}

function NavigationPanel({
  model,
  actions,
  onClose,
}: ConversationWorkspaceProps & { onClose?: () => void }) {
  return (
    <div className="oc-navigation-panel">
      <div className="oc-brand-row">
        <div className="oc-brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div>
          <strong>OmniCell</strong>
          <span>Agent Workspace</span>
        </div>
        {onClose && (
          <button
            className="oc-icon-button oc-drawer-close"
            type="button"
            aria-label="关闭导航"
            onClick={onClose}
          >
            <Icon name="x" />
          </button>
        )}
      </div>

      <button
        className="oc-primary-action"
        type="button"
        disabled={model.commands.createConversationPending}
        onClick={actions?.onCreateConversation}
      >
        <Icon name="plus" /> 新建分析对话
      </button>

      <section
        className="oc-nav-section"
        aria-labelledby="conversation-navigation-title"
      >
        <div className="oc-section-heading">
          <h2 id="conversation-navigation-title">对话</h2>
          <span>{model.conversations.length}</span>
        </div>
        <div className="oc-nav-list">
          {model.conversations.length === 0 ? (
            <p className="oc-nav-empty">
              还没有对话。创建后，运行与数据会在这里持续保存。
            </p>
          ) : (
            model.conversations.map((conversation) => (
              <button
                className="oc-conversation-row"
                data-selected={conversation.id === model.selectedConversationId}
                key={conversation.id}
                type="button"
                onClick={() => {
                  actions?.onSelectConversation?.(conversation.id);
                  onClose?.();
                }}
              >
                <span className="oc-conversation-icon">
                  <Icon name="message" />
                </span>
                <span className="oc-conversation-copy">
                  <strong>{conversation.title}</strong>
                  <small>{conversation.updatedAtLabel}</small>
                </span>
                {conversation.runState && (
                  <span
                    className="oc-mini-state"
                    data-tone={runTone[conversation.runState]}
                    aria-label={conversation.runState}
                  />
                )}
              </button>
            ))
          )}
        </div>
      </section>

      <section
        className="oc-nav-section oc-dataset-section"
        aria-labelledby="dataset-navigation-title"
      >
        <div className="oc-section-heading">
          <h2 id="dataset-navigation-title">数据集</h2>
          <button
            type="button"
            disabled={
              model.selectedConversationId === undefined ||
              model.commands.importDatasetPending
            }
            onClick={actions?.onImportDataset}
          >
            导入
          </button>
        </div>
        <div className="oc-dataset-list">
          {model.datasets.length === 0 ? (
            <button
              className="oc-dataset-empty"
              type="button"
              disabled={
                model.selectedConversationId === undefined ||
                model.commands.importDatasetPending
              }
              onClick={actions?.onImportDataset}
            >
              <Icon name="database" />
              <span>
                <strong>添加单细胞数据</strong>
                <small>导入后可在对话中选择</small>
              </span>
            </button>
          ) : (
            model.datasets.map((dataset) => (
              <button
                className="oc-dataset-row"
                data-selected={dataset.artifactId === model.selectedDatasetId}
                key={dataset.artifactId}
                type="button"
                onClick={() => {
                  actions?.onSelectDataset?.(dataset.artifactId);
                  onClose?.();
                }}
              >
                <span className="oc-dataset-icon">
                  <Icon name="database" />
                </span>
                <span>
                  <strong>{dataset.name}</strong>
                  <small>
                    {dataset.detail}
                    {dataset.sizeLabel ? ` · ${dataset.sizeLabel}` : ""}
                  </small>
                </span>
              </button>
            ))
          )}
        </div>
      </section>

      <div className="oc-local-note">
        <span className="oc-local-dot" />
        本地工作区 · 数据保持在 conversation 边界内
      </div>
    </div>
  );
}

function renderInlineMessage(value: string): ReactNode[] {
  return value
    .split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g)
    .filter(Boolean)
    .map((part, index) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={index}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`")) {
        return <code key={index}>{part.slice(1, -1)}</code>;
      }
      return part;
    });
}

function MessageContent({ content }: { content: string }) {
  return (
    <div className="oc-message-content">
      {content.split("\n").map((line, index) => {
        const bullet = line.match(/^\s*[-*]\s+(.+)$/);
        const numbered = line.match(/^\s*(\d+)\.\s+(.+)$/);
        if (!line.trim()) {
          return <div className="oc-message-line is-blank" key={index} />;
        }
        if (bullet) {
          return (
            <div className="oc-message-line is-list" key={index}>
              <span aria-hidden="true">•</span>
              <span>{renderInlineMessage(bullet[1])}</span>
            </div>
          );
        }
        if (numbered) {
          return (
            <div className="oc-message-line is-list" key={index}>
              <span aria-hidden="true">{numbered[1]}.</span>
              <span>{renderInlineMessage(numbered[2])}</span>
            </div>
          );
        }
        return (
          <div className="oc-message-line" key={index}>
            {renderInlineMessage(line)}
          </div>
        );
      })}
    </div>
  );
}

function MessageTimelineItem({ item }: { item: TimelineMessageItem }) {
  return (
    <article className="oc-timeline-message" data-role={item.role}>
      <div className="oc-avatar" aria-hidden="true">
        {item.role === "user" ? "你" : "O"}
      </div>
      <div className="oc-message-body">
        <header>
          <strong>{item.authorLabel}</strong>
          <time>{item.occurredAtLabel}</time>
        </header>
        <MessageContent content={item.content} />
      </div>
    </article>
  );
}

function ActivityProcess({
  steps,
}: {
  steps: readonly {
    label: string;
    detail?: string;
    state: ActivityProcessState;
  }[];
}) {
  return (
    <div className="oc-activity-process">
      <span className="oc-activity-section-label">过程</span>
      <ol>
        {steps.map((step, index) => (
          <li data-state={step.state} key={`${step.label}:${index}`}>
            <span className="oc-process-node" />
            <div>
              <strong>{step.label}</strong>
              {step.detail && <small>{step.detail}</small>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function TaskTimelineItem({ item }: { item: TimelineTaskItem }) {
  return (
    <article className="oc-task-card">
      <span className="oc-task-check" data-tone={workTone[item.state]}>
        {item.state === "completed" ? "✓" : item.state === "running" ? "…" : ""}
      </span>
      <div>
        <small>{item.capability ? `计划任务 · Tool ${item.capability}` : "计划任务"}</small>
        <strong>{item.title}</strong>
        {item.description && <p>{item.description}</p>}
        <time>{item.occurredAtLabel}</time>
      </div>
      <StatusPill
        label={item.stateLabel}
        tone={workTone[item.state]}
        pulse={item.state === "running"}
      />
    </article>
  );
}

function ToolTimelineItem({ item }: { item: TimelineToolItem }) {
  return (
    <article
      className="oc-activity-card oc-tool-activity"
      data-family={item.family}
      data-state={item.state}
    >
      <header>
        <div className="oc-activity-heading">
          <span className="oc-activity-kind">TOOL</span>
          <div>
            <h3>{item.toolName}</h3>
            <p>{item.title}</p>
          </div>
        </div>
        <StatusPill
          label={item.stateLabel}
          tone={workTone[item.state]}
          pulse={item.state === "running"}
        />
      </header>
      <div className="oc-activity-purpose">
        <span className="oc-activity-section-label">动作</span>
        <strong>{item.purpose}</strong>
      </div>
      <ActivityProcess steps={item.process} />
      {item.resultSummary && (
        <div className="oc-activity-result" data-state={item.state}>
          <span className="oc-activity-section-label">结果</span>
          <strong>{item.resultSummary}</strong>
          {item.errorCode && <code>{item.errorCode}</code>}
          {item.recoveryHint && <small>建议：{item.recoveryHint}</small>}
        </div>
      )}
      <footer>
        <time>{item.occurredAtLabel}</time>
        <span>
          第 {item.attempt} 次执行
          {item.durationLabel ? ` · ${item.durationLabel}` : ""}
          {item.artifactCount > 0
            ? ` · ${item.artifactCount} 个 Artifact`
            : ""}
        </span>
      </footer>
    </article>
  );
}

function SkillTimelineItem({ item }: { item: TimelineSkillItem }) {
  const tone =
    item.state === "completed"
      ? "success"
      : item.state === "running"
        ? "active"
        : item.state === "cancelled"
          ? "neutral"
          : "danger";
  return (
    <article
      className="oc-activity-card oc-skill-activity"
      data-state={item.state}
    >
      <header>
        <div className="oc-activity-heading">
          <span className="oc-activity-kind">SKILL</span>
          <div>
            <h3>{item.skillName}</h3>
            <p>加载 {item.resourceLabel}</p>
          </div>
        </div>
        <StatusPill
          label={item.stateLabel}
          tone={tone}
          pulse={item.state === "running"}
        />
      </header>
      <div className="oc-activity-purpose">
        <span className="oc-activity-section-label">动作</span>
        <strong>{item.purposeLabel}</strong>
      </div>
      <ActivityProcess steps={item.process} />
      {item.resultSummary && (
        <div className="oc-activity-result">
          <span className="oc-activity-section-label">结果</span>
          <strong>{item.resultSummary}</strong>
        </div>
      )}
      <footer>
        <time>{item.occurredAtLabel}</time>
        <span>{item.resourceLabel}</span>
      </footer>
    </article>
  );
}

function backendCommandPreview(command: readonly string[]): string {
  if (command.length === 0) return "等待命令…";
  const [executable, mode, ...args] = command;
  if (
    mode === "-c" &&
    (executable.endsWith("python") || executable.endsWith("python3"))
  ) {
    return `${JSON.stringify(executable)} "-c" "<backend-runner>"${
      args.length > 1 ? ` … (+${args.length - 1} args)` : ""
    }`;
  }
  const visible = command.slice(0, 5).map((token) =>
    JSON.stringify(
      token.length > 120 ? `<argument · ${token.length} characters>` : token,
    ),
  );
  return `${visible.join(" ")}${command.length > visible.length ? " …" : ""}`;
}

function RuntimeTimelineItem({ item }: { item: TimelineRuntimeItem }) {
  const tone =
    item.state === "completed"
      ? "success"
      : item.state === "running"
        ? "active"
        : item.state === "cancelled"
          ? "neutral"
          : "danger";
  return (
    <article
      className="oc-activity-card oc-backend-activity"
      data-state={item.state}
    >
      <header>
        <div className="oc-activity-heading">
          <span className="oc-activity-kind">BACKEND</span>
          <div>
            <h3>{item.backend}</h3>
            <p>执行 {item.toolName}</p>
          </div>
        </div>
        <StatusPill
          label={
            item.state === "running"
              ? "执行中"
              : item.state === "completed"
                ? "已结束"
                : item.state === "timeout"
                  ? "超时"
                  : item.state === "cancelled"
                    ? "已取消"
                  : "失败"
          }
          tone={tone}
          pulse={item.state === "running"}
        />
      </header>
      <div className="oc-activity-purpose">
        <span className="oc-activity-section-label">动作</span>
        <strong>
          在 {item.backend} 中执行 {item.toolName}
        </strong>
      </div>
      <div className="oc-backend-command">
        <span>$</span>
        <code>{backendCommandPreview(item.command)}</code>
      </div>
      <dl className="oc-runtime-meta">
        <div>
          <dt>工作目录</dt>
          <dd>{item.workdir}</dd>
        </div>
        <div>
          <dt>退出码</dt>
          <dd>{item.exitCode ?? (item.state === "running" ? "running" : "—")}</dd>
        </div>
        {item.durationLabel && (
          <div>
            <dt>耗时</dt>
            <dd>{item.durationLabel}</dd>
          </div>
        )}
      </dl>
      <ActivityProcess steps={item.process} />
      <div className="oc-activity-result" data-state={item.state}>
        <span className="oc-activity-section-label">结果</span>
        <strong>
          {item.state === "running"
            ? "Backend 正在执行"
            : item.state === "completed"
              ? `命令执行成功${item.exitCode === undefined ? "" : `，退出码 ${item.exitCode}`}`
              : item.state === "timeout"
                ? "命令执行超时"
                : item.state === "cancelled"
                  ? "Backend 操作已取消"
                  : `命令执行失败${item.exitCode === undefined ? "" : `，退出码 ${item.exitCode}`}`}
        </strong>
      </div>
      <details>
        <summary>查看原始 argv · {item.command.length}</summary>
        <pre>{JSON.stringify(item.command, null, 2)}</pre>
      </details>
      {item.code && (
        <details>
          <summary>执行代码{item.commandTruncated ? " · 已截断" : ""}</summary>
          <pre>{item.code}</pre>
        </details>
      )}
      {(item.stdout || item.state === "running") && (
        <div className="oc-backend-output">
          <span>stdout{item.stdoutTruncated ? " · 已截断" : ""}</span>
          <pre>{item.stdout || "等待输出…"}</pre>
        </div>
      )}
      {item.stderr && (
        <div className="oc-backend-output is-stderr">
          <span>stderr{item.stderrTruncated ? " · 已截断" : ""}</span>
          <pre className="is-stderr">{item.stderr}</pre>
        </div>
      )}
      {(item.redacted ||
        item.commandTruncated ||
        item.stdoutTruncated ||
        item.stderrTruncated) && (
        <p className="oc-runtime-disclosure">
          {item.redacted ? "redacted · 已隐藏敏感信息" : ""}
          {item.redacted &&
          (item.commandTruncated || item.stdoutTruncated || item.stderrTruncated)
            ? " · "
            : ""}
          {item.commandTruncated || item.stdoutTruncated || item.stderrTruncated
            ? "truncated · 内容达到公开上限"
            : ""}
        </p>
      )}
      <footer>
        <time>{item.occurredAtLabel}</time>
        <span>{item.durationLabel ?? "执行中"}</span>
      </footer>
    </article>
  );
}

function parseTable(text: string, separator: "," | "\t"): string[][] {
  return text
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(0, 20)
    .map((line) => line.split(separator).slice(0, 12));
}

interface AnnotationPreviewRow {
  readonly clusterId: string;
  readonly generalType: string;
  readonly subType: string;
  readonly score: string;
  readonly flags: string;
  readonly requiresReview: boolean;
}

function annotationPreviewRows(value: unknown): AnnotationPreviewRow[] | undefined {
  if (value === null || typeof value !== "object") return undefined;
  const annotations = (value as Record<string, unknown>).cluster_annotations;
  if (annotations === null || typeof annotations !== "object") return undefined;
  return Object.entries(annotations as Record<string, unknown>)
    .slice(0, 100)
    .map(([clusterId, raw]) => {
      const annotation =
        raw !== null && typeof raw === "object"
          ? (raw as Record<string, unknown>)
          : {};
      const numericScore = Number(annotation.cs_score ?? 0);
      const flags = Array.isArray(annotation.flags)
        ? annotation.flags.slice(0, 20).map(String)
        : [];
      const subType = String(annotation.sub_type ?? "Unknown");
      return {
        clusterId,
        generalType: String(annotation.general_type ?? "Unknown"),
        subType,
        score: Number.isFinite(numericScore) ? numericScore.toFixed(1) : "—",
        flags: flags.join(", ") || "—",
        requiresReview:
          numericScore < 60 ||
          flags.length > 0 ||
          subType.includes("(NeedsReview)"),
      };
    });
}

function boundedJson(value: unknown, depth = 0): unknown {
  if (typeof value === "string") {
    return value.length > 2_000
      ? `${value.slice(0, 2_000)}… [truncated]`
      : value;
  }
  if (value === null || typeof value !== "object") return value;
  if (depth >= 4) return "[nested value omitted]";
  if (Array.isArray(value)) {
    const rows = value.slice(0, 20).map((item) => boundedJson(item, depth + 1));
    return value.length > rows.length
      ? [...rows, `[${value.length - rows.length} more items]`]
      : rows;
  }
  const entries = Object.entries(value).slice(0, 40);
  const projected = Object.fromEntries(
    entries.map(([key, item]) => [key, boundedJson(item, depth + 1)]),
  );
  if (Object.keys(value).length > entries.length) {
    projected["…"] = `[${Object.keys(value).length - entries.length} more fields]`;
  }
  return projected;
}

function ArtifactTimelineItem({
  item,
  actions,
}: {
  item: TimelineArtifactItem;
  actions?: ConversationWorkspaceActions;
}) {
  const [preview, setPreview] = useState<
    | { readonly state: "idle" | "loading" }
    | { readonly state: "error"; readonly message: string }
    | {
        readonly state: "ready";
        readonly text?: string;
        readonly rows?: readonly (readonly string[])[];
        readonly annotationRows?: readonly AnnotationPreviewRow[];
        readonly imageUrl?: string;
      }
  >({ state: "idle" });

  useEffect(() => {
    if (item.previewMode === "none" || !actions?.onLoadArtifactContent) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setPreview({ state: "loading" });
    void actions
      .onLoadArtifactContent(item.artifactId)
      .then(async (blob) => {
        if (controller.signal.aborted) return;
        if (item.previewMode === "image") {
          objectUrl = URL.createObjectURL(blob);
          setPreview({ state: "ready", imageUrl: objectUrl });
          return;
        }
        const text = await blob.text();
        if (controller.signal.aborted) return;
        if (item.previewMode === "json") {
          try {
            const parsed = JSON.parse(text);
            setPreview({
              state: "ready",
              text: JSON.stringify(boundedJson(parsed), null, 2),
              annotationRows:
                item.artifactKind === "cluster_annotations"
                  ? annotationPreviewRows(parsed)
                  : undefined,
            });
          } catch {
            setPreview({ state: "ready", text });
          }
          return;
        }
        if (item.previewMode === "table") {
          setPreview({
            state: "ready",
            rows: parseTable(
              text,
              item.mediaType === "text/tab-separated-values" ? "\t" : ",",
            ),
          });
          return;
        }
        setPreview({ state: "ready", text });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPreview({
            state: "error",
            message: error instanceof Error ? error.message : "预览读取失败",
          });
        }
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.artifactId, item.previewMode]);

  return (
    <article className="oc-artifact-card">
      <header>
        <div>
          <small>ARTIFACT · {item.artifactKind}</small>
          <h3>{item.name}</h3>
        </div>
        <button
          type="button"
          onClick={() =>
            actions?.onDownloadArtifact?.(item.artifactId, item.name)
          }
        >
          <Icon name="download" /> 下载
        </button>
      </header>
      <p>
        {item.mediaType ?? "unknown"} · {item.sizeLabel}
      </p>
      {item.previewReason && (
        <div className="oc-artifact-fallback">{item.previewReason}</div>
      )}
      {preview.state === "loading" && (
        <div className="oc-artifact-fallback">正在读取已登记内容…</div>
      )}
      {preview.state === "error" && (
        <div className="oc-artifact-fallback is-error">{preview.message}</div>
      )}
      {preview.state === "ready" && preview.imageUrl && (
        <img alt={item.name} loading="lazy" src={preview.imageUrl} />
      )}
      {preview.state === "ready" && preview.annotationRows && (
        <div className="oc-artifact-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Cluster</th>
                <th>谱系</th>
                <th>细胞类型</th>
                <th>分数</th>
                <th>Flags</th>
                <th>复核</th>
              </tr>
            </thead>
            <tbody>
              {preview.annotationRows.map((row) => (
                <tr key={row.clusterId}>
                  <td>{row.clusterId}</td>
                  <td>{row.generalType}</td>
                  <td>{row.subType}</td>
                  <td>{row.score}</td>
                  <td>{row.flags}</td>
                  <td>{row.requiresReview ? "需要" : "否"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details>
            <summary>查看有界 JSON 证据</summary>
            <pre>{preview.text}</pre>
          </details>
        </div>
      )}
      {preview.state === "ready" &&
        preview.text !== undefined &&
        !preview.annotationRows &&
        (item.mediaType === "text/markdown" ? (
          <MessageContent content={preview.text} />
        ) : (
          <pre>{preview.text}</pre>
        ))}
      {preview.state === "ready" && preview.rows && (
        <div className="oc-artifact-table-wrap">
          <table>
            <tbody>
              {preview.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, columnIndex) => (
                    <td key={columnIndex}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <time>{item.occurredAtLabel}</time>
    </article>
  );
}

function ReviewTimelineItem({
  item,
  actions,
}: {
  item: TimelineReviewItem;
  actions?: ConversationWorkspaceActions;
}) {
  return (
    <article className="oc-review-card" data-state={item.state}>
      <div className="oc-review-kicker">人工审核</div>
      <div className="oc-review-copy">
        <h3>{item.title}</h3>
        <p>{item.description}</p>
        <time>{item.occurredAtLabel}</time>
      </div>
      {item.state === "pending" ? (
        <div className="oc-review-actions">
          <button
            type="button"
            disabled={item.decisionPending}
            onClick={() => actions?.onReviewDecision?.(item.reviewId, "reject")}
          >
            拒绝
          </button>
          <button
            className="is-approve"
            type="button"
            disabled={item.decisionPending}
            onClick={() =>
              actions?.onReviewDecision?.(item.reviewId, "approve")
            }
          >
            批准并继续
          </button>
        </div>
      ) : (
        <StatusPill
          label={item.state === "approved" ? "已批准" : "已拒绝"}
          tone={item.state === "approved" ? "success" : "neutral"}
        />
      )}
    </article>
  );
}

function NoticeTimelineItem({ item }: { item: TimelineNoticeItem }) {
  return (
    <article className="oc-notice" data-tone={item.tone}>
      <span />
      <div>
        <strong>{item.title}</strong>
        {item.description && <p>{item.description}</p>}
      </div>
      <time>{item.occurredAtLabel}</time>
    </article>
  );
}

function Timeline({
  items,
  actions,
}: {
  items: readonly TimelineItem[];
  actions?: ConversationWorkspaceActions;
}) {
  if (items.length === 0) {
    return (
      <div className="oc-timeline-empty">
        <div className="oc-empty-orbit">
          <span>S</span>
          <span>T</span>
          <i />
        </div>
        <h2>从一个明确的分析目标开始</h2>
        <p>
          Agent 会根据目标直接回答、加载 Skill、调用 Tool，
          并把 Backend 操作与结果持续记录到时间线。
        </p>
        <div className="oc-empty-capabilities">
          <span>Skill · 方法与组合规则</span>
          <span>Tool · 可验证科学操作</span>
          <span>Backend · 命令与实时输出</span>
        </div>
      </div>
    );
  }
  return (
    <div className="oc-timeline-list">
      {items.map((item) => {
        if (item.kind === "message")
          return <MessageTimelineItem item={item} key={item.id} />;
        if (item.kind === "task")
          return <TaskTimelineItem item={item} key={item.id} />;
        if (item.kind === "tool")
          return <ToolTimelineItem item={item} key={item.id} />;
        if (item.kind === "skill")
          return <SkillTimelineItem item={item} key={item.id} />;
        if (item.kind === "runtime")
          return <RuntimeTimelineItem item={item} key={item.id} />;
        if (item.kind === "artifact")
          return (
            <ArtifactTimelineItem item={item} actions={actions} key={item.id} />
          );
        if (item.kind === "review")
          return (
            <ReviewTimelineItem item={item} actions={actions} key={item.id} />
          );
        return <NoticeTimelineItem item={item} key={item.id} />;
      })}
    </div>
  );
}

function TaskList({ tasks }: { tasks: readonly TaskViewModel[] }) {
  if (!tasks.length)
    return (
      <InspectorEmpty
        title="尚无任务"
        description="Agent 创建任务后会在这里显示权威状态。"
      />
    );
  return (
    <div className="oc-inspector-list">
      {tasks.map((task) => (
        <article className="oc-task-row" key={task.id}>
          <span className="oc-task-check" data-tone={workTone[task.state]}>
            {task.state === "completed" ? "✓" : ""}
          </span>
          <div>
            <strong>{task.title}</strong>
            {task.description && <p>{task.description}</p>}
            <StatusPill
              label={task.stateLabel}
              tone={workTone[task.state]}
              pulse={task.state === "running"}
            />
          </div>
        </article>
      ))}
    </div>
  );
}

function ToolExecutionList({
  toolExecutions,
}: {
  toolExecutions: readonly ToolExecutionViewModel[];
}) {
  if (!toolExecutions.length)
    return (
      <InspectorEmpty
        title="尚无 Tool 调用"
        description="已经进入执行生命周期的科学 Tool 会显示在这里。"
      />
    );
  return (
    <div className="oc-inspector-list">
      {toolExecutions.map((tool) => (
        <article
          className="oc-inspector-capability"
          data-family={tool.family}
          key={tool.id}
        >
          <span className="oc-capability-monogram">
            {{
              inspect: "I",
              transform: "T",
              analyze: "A",
              annotate: "N",
              visualize: "V",
              custom: "C",
            }[tool.family]}
          </span>
          <div>
            <small>
              TOOL · {tool.name}
              {tool.invocationCount
                ? ` · ${tool.invocationCount} 次`
                : ""}
            </small>
            <strong>{tool.title}</strong>
            <p>{tool.description}</p>
            <StatusPill
              label={tool.stateLabel}
              tone={workTone[tool.state]}
              pulse={tool.state === "running"}
            />
          </div>
        </article>
      ))}
    </div>
  );
}

function ReviewList({
  reviews,
  actions,
}: {
  reviews: readonly ReviewViewModel[];
  actions?: ConversationWorkspaceActions;
}) {
  const [comments, setComments] = useState<Record<string, string>>({});
  if (!reviews.length)
    return (
      <InspectorEmpty
        title="无需审核"
        description="需要人工确认的 Tool 调用会集中出现在这里。"
      />
    );
  return (
    <div className="oc-inspector-list">
      {reviews.map((review) => (
        <article className="oc-inspector-review" key={review.id}>
          <div className="oc-review-status-line">
            <span>{review.capabilityLabel}</span>
            <StatusPill
              label={
                review.state === "pending"
                  ? "待决策"
                  : (review.decisionLabel ?? "已处理")
              }
              tone={review.state === "pending" ? "warning" : "neutral"}
            />
          </div>
          <strong>{review.title}</strong>
          <p>{review.description}</p>
          {review.state === "pending" && (
            <>
              <textarea
                className="oc-review-comment"
                aria-label={`审核备注 ${review.id}`}
                placeholder="可选：记录批准或拒绝的原因"
                value={comments[review.id] ?? ""}
                disabled={review.decisionPending}
                onChange={(event) =>
                  setComments((current) => ({
                    ...current,
                    [review.id]: event.target.value,
                  }))
                }
              />
              <div className="oc-review-actions">
                <button
                  type="button"
                  disabled={review.decisionPending}
                  onClick={() =>
                    actions?.onReviewDecision?.(
                      review.id,
                      "reject",
                      comments[review.id]?.trim() || undefined,
                    )
                  }
                >
                  拒绝
                </button>
                <button
                  className="is-approve"
                  type="button"
                  disabled={review.decisionPending}
                  onClick={() =>
                    actions?.onReviewDecision?.(
                      review.id,
                      "approve",
                      comments[review.id]?.trim() || undefined,
                    )
                  }
                >
                  批准
                </button>
              </div>
            </>
          )}
        </article>
      ))}
    </div>
  );
}

function ArtifactList({
  artifacts,
  actions,
}: {
  artifacts: readonly ArtifactViewModel[];
  actions?: ConversationWorkspaceActions;
}) {
  if (!artifacts.length)
    return (
      <InspectorEmpty
        title="尚无产物"
        description="分析生成的表格、图片与报告会作为 artifact 出现。"
      />
    );
  return (
    <div className="oc-inspector-list">
      {artifacts.map((artifact) => (
        <article className="oc-artifact-row" key={artifact.id}>
          <span className="oc-file-glyph">
            {artifact.kindLabel.slice(0, 1)}
          </span>
          <div>
            <strong>{artifact.name}</strong>
            <small>
              {artifact.kindLabel} · {artifact.sizeLabel} ·{" "}
              {artifact.createdAtLabel}
            </small>
          </div>
          <button
            type="button"
            aria-label={`下载 ${artifact.name}`}
            disabled={!artifact.canDownload || artifact.downloadPending}
            onClick={() =>
              actions?.onDownloadArtifact?.(artifact.id, artifact.name)
            }
          >
            <Icon name="download" />
          </button>
        </article>
      ))}
    </div>
  );
}

function EventList({ events }: { events: readonly EventViewModel[] }) {
  if (!events.length)
    return (
      <InspectorEmpty
        title="尚无事件"
        description="持久化事件到达后会按 sequence 展示。"
      />
    );
  return (
    <ol className="oc-event-list">
      {events.map((event) => (
        <li data-tone={event.tone} key={event.id}>
          <span className="oc-event-sequence" title={event.sequence}>
            {event.runId.slice(0, 8)} · #{event.sequence}
          </span>
          <div>
            <strong>{event.type}</strong>
            <p>{event.summary}</p>
            {event.context && <span className="oc-event-context">{event.context}</span>}
            <time title={event.occurredAtIso}>{event.occurredAtLabel}</time>
            <details className="oc-event-metadata">
              <summary>metadata · {event.metadata.length}</summary>
              <dl>
                {event.metadata.map((item) => (
                  <div key={item.label}>
                    <dt>{item.label}</dt>
                    <dd title={item.value}>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </div>
        </li>
      ))}
    </ol>
  );
}

function InspectorEmpty({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="oc-inspector-empty">
      <span>·</span>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

function InspectorPanel({
  model,
  actions,
  onClose,
}: ConversationWorkspaceProps & { onClose?: () => void }) {
  const initialTab = model.reviews.some(
    (review) =>
      review.state === "pending" &&
      (model.run === undefined || review.runId === model.run.id),
  )
    ? "reviews"
    : "tasks";
  const [tab, setTab] = useState<InspectorTab>(initialTab);
  const [scope, setScope] = useState<"run" | "conversation">("run");
  const effectiveScope = model.run ? scope : "conversation";
  const inScope = <T extends { readonly runId?: string }>(
    items: readonly T[],
  ): readonly T[] =>
    effectiveScope === "conversation"
      ? items
      : items.filter((item) => item.runId === model.run?.id);
  const scopedTasks = inScope(model.tasks);
  const scopedToolExecutions = inScope(model.toolExecutions);
  const scopedReviews = inScope(model.reviews);
  const scopedArtifacts = inScope(model.artifacts);
  const scopedEvents = inScope(model.events);
  const counts = useMemo(
    () => ({
      tasks: scopedTasks.length,
      toolExecutions: scopedToolExecutions.length,
      reviews: scopedReviews.length,
      artifacts: scopedArtifacts.length,
      events: scopedEvents.length,
    }),
    [
      scopedArtifacts,
      scopedEvents,
      scopedReviews,
      scopedTasks,
      scopedToolExecutions,
    ],
  );

  return (
    <div className="oc-inspector-panel">
      <header className="oc-inspector-header">
        <div>
          <small>
            {effectiveScope === "run"
              ? `RUN ${model.run?.id.slice(0, 8)}`
              : "CONVERSATION"}
          </small>
          <h2>运行检查器</h2>
        </div>
        {onClose && (
          <button
            className="oc-icon-button oc-drawer-close"
            type="button"
            aria-label="关闭运行检查器"
            onClick={onClose}
          >
            <Icon name="x" />
          </button>
        )}
      </header>
      <div className="oc-inspector-scope" role="group" aria-label="检查器作用域">
        <button
          className={effectiveScope === "run" ? "is-active" : ""}
          disabled={!model.run}
          onClick={() => setScope("run")}
          type="button"
        >
          当前 Run
        </button>
        <button
          className={effectiveScope === "conversation" ? "is-active" : ""}
          onClick={() => setScope("conversation")}
          type="button"
        >
          全部会话
        </button>
      </div>
      <div className="oc-inspector-tabs" role="tablist" aria-label="运行检查器">
        {(Object.keys(tabLabels) as InspectorTab[]).map((key) => (
          <button
            aria-selected={tab === key}
            className={tab === key ? "is-active" : ""}
            key={key}
            onClick={() => setTab(key)}
            role="tab"
            type="button"
          >
            <span>{tabLabels[key]}</span>
            <small>{counts[key]}</small>
          </button>
        ))}
      </div>
      <div className="oc-inspector-body" role="tabpanel">
        {tab === "tasks" && <TaskList tasks={scopedTasks} />}
        {tab === "toolExecutions" && (
          <ToolExecutionList toolExecutions={scopedToolExecutions} />
        )}
        {tab === "reviews" && (
          <ReviewList reviews={scopedReviews} actions={actions} />
        )}
        {tab === "artifacts" && (
          <ArtifactList artifacts={scopedArtifacts} actions={actions} />
        )}
        {tab === "events" && <EventList events={scopedEvents} />}
      </div>
      <footer className="oc-inspector-footer">
        <span className="oc-authority-mark" />
        PostgreSQL 持久化事件为权威来源
      </footer>
    </div>
  );
}

function LoadingWorkspace() {
  return (
    <div className="oc-workspace-state" aria-label="正在加载 conversation">
      <div className="oc-loading-emblem">
        <span />
        <span />
      </div>
      <h2>正在恢复工作区</h2>
      <p>读取 conversation、运行记录与持久化事件…</p>
      <div className="oc-skeleton-lines">
        <i />
        <i />
        <i />
      </div>
    </div>
  );
}

function ErrorWorkspace({
  message,
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="oc-workspace-state is-error" role="alert">
      <span className="oc-error-glyph">!</span>
      <h2>工作区暂时无法加载</h2>
      <p>{message || "未能读取 conversation。请稍后重试。"}</p>
      <button type="button" onClick={onRetry}>
        重新加载
      </button>
    </div>
  );
}

function CommandErrorBanner({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <div className="oc-command-error" role="alert">
      <strong>操作未完成</strong>
      <span>{message}</span>
    </div>
  );
}

function Composer({ model, actions }: ConversationWorkspaceProps) {
  const [draft, setDraft] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const instruction = draft.trim();
    if (!instruction || model.composer.disabled) return;
    try {
      if ((await actions?.onSubmit?.(instruction)) === true) {
        setDraft("");
      }
    } catch {
      // 父级 mutation 负责展示 conversation-scoped 错误；保留草稿供重试。
    }
  };
  return (
    <form className="oc-composer" onSubmit={submit}>
      <div className="oc-composer-field">
        <textarea
          aria-label="分析指令"
          disabled={model.composer.disabled}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={model.composer.placeholder}
          rows={2}
          value={draft}
        />
        <button
          aria-label="发送分析指令"
          disabled={model.composer.disabled || !draft.trim()}
          type="submit"
        >
          <Icon name="send" />
        </button>
      </div>
      <div className="oc-composer-meta">
        <span>
          {model.selectedDatasetId ? "已绑定当前数据集" : "尚未选择数据集"}
        </span>
        <span>
          {model.composer.disabledReason ||
            "Agent 会按需直接回复、加载 Skill 或调用 Tool"}
        </span>
      </div>
    </form>
  );
}

export function ConversationWorkspace({
  model,
  actions,
}: ConversationWorkspaceProps) {
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const isActive =
    model.run?.state === "running" ||
    model.run?.state === "pending" ||
    model.run?.state === "cancelling";

  return (
    <div className="oc-workspace-shell">
      <aside
        className={
          navigationOpen
            ? "oc-sidebar oc-sidebar-left is-open"
            : "oc-sidebar oc-sidebar-left"
        }
        aria-label="对话与数据集导航"
      >
        <NavigationPanel
          model={model}
          actions={actions}
          onClose={() => setNavigationOpen(false)}
        />
      </aside>
      <main className="oc-workspace-main">
        <header className="oc-workspace-header">
          <button
            className="oc-mobile-trigger oc-nav-trigger"
            type="button"
            onClick={() => setNavigationOpen(true)}
          >
            <Icon name="message" />
            <span>对话</span>
          </button>
          <div className="oc-title-block">
            <small>CONVERSATION WORKSPACE</small>
            <h1>{model.title}</h1>
            {model.subtitle && <p>{model.subtitle}</p>}
          </div>
          <div className="oc-run-controls">
            <StatusPill
              label={model.connectionLabel}
              tone={
                model.connection === "connected"
                  ? "success"
                  : model.connection === "reconnecting"
                    ? "warning"
                    : "danger"
              }
              pulse={model.connection === "reconnecting"}
            />
            {model.run && (
              <StatusPill
                label={model.run.stateLabel}
                tone={runTone[model.run.state]}
                pulse={isActive}
              />
            )}
            {model.run?.canCancel && (
              <button
                className="oc-cancel-button"
                type="button"
                disabled={model.commands.cancelRunPending}
                onClick={() => actions?.onCancelRun?.(model.run!.id)}
              >
                取消运行
              </button>
            )}
          </div>
          <button
            className="oc-mobile-trigger oc-inspector-trigger"
            type="button"
            onClick={() => setInspectorOpen(true)}
          >
            <Icon name="panel" />
            <span>检查器</span>
          </button>
        </header>
        <ConnectionBanner
          state={model.connection}
          label={model.connectionLabel}
        />
        <CommandErrorBanner message={model.commandErrorMessage} />
        <div className="oc-workspace-content">
          {model.viewState === "loading" && <LoadingWorkspace />}
          {model.viewState === "error" && (
            <ErrorWorkspace
              message={model.errorMessage}
              onRetry={actions?.onRetry}
            />
          )}
          {(model.viewState === "ready" || model.viewState === "empty") && (
            <Timeline items={model.timeline} actions={actions} />
          )}
        </div>
        {(model.viewState === "ready" || model.viewState === "empty") && (
          <Composer
            key={model.selectedConversationId ?? "no-conversation"}
            model={model}
            actions={actions}
          />
        )}
      </main>
      <aside
        className={
          inspectorOpen
            ? "oc-sidebar oc-sidebar-right is-open"
            : "oc-sidebar oc-sidebar-right"
        }
        aria-label="运行检查器"
      >
        <InspectorPanel
          model={model}
          actions={actions}
          onClose={() => setInspectorOpen(false)}
        />
      </aside>
      <button
        className={
          navigationOpen || inspectorOpen
            ? "oc-drawer-backdrop is-open"
            : "oc-drawer-backdrop"
        }
        aria-label="关闭抽屉"
        type="button"
        onClick={() => {
          setNavigationOpen(false);
          setInspectorOpen(false);
        }}
      />
    </div>
  );
}

export default ConversationWorkspace;
