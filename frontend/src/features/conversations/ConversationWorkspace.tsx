import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  ArtifactViewModel,
  CapabilityViewModel,
  ConnectionState,
  ConversationWorkspaceActions,
  ConversationWorkspaceViewModel,
  EventViewModel,
  ReviewViewModel,
  RunState,
  TaskViewModel,
  TimelineCapabilityItem,
  TimelineArtifactItem,
  TimelineItem,
  TimelineMessageItem,
  TimelineNoticeItem,
  TimelineReviewItem,
  TimelineRuntimeItem,
  TimelineSkillItem,
  TimelineTaskItem,
  WorkItemState,
} from "./view-model";

type InspectorTab =
  "tasks" | "capabilities" | "reviews" | "artifacts" | "events";

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
  cancelled: "neutral",
};

const tabLabels: Record<InspectorTab, string> = {
  tasks: "任务",
  capabilities: "能力",
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

function CapabilityTimelineItem({ item }: { item: TimelineCapabilityItem }) {
  const graphLabel =
    item.family === "graph_a"
      ? "Graph A"
      : item.family === "graph_b"
        ? "Graph B"
        : "Tool";
  return (
    <article className="oc-capability-card" data-family={item.family}>
      <div className="oc-capability-rail">
        <span>
          {item.family === "graph_a"
            ? "A"
            : item.family === "graph_b"
              ? "B"
              : "T"}
        </span>
      </div>
      <div className="oc-capability-content">
        <header>
          <div>
            <small>
              {graphLabel} · {item.capability}
            </small>
            <h3>{item.title}</h3>
          </div>
          <StatusPill
            label={item.stateLabel}
            tone={workTone[item.state]}
            pulse={item.state === "running"}
          />
        </header>
        <p>{item.description}</p>
        {item.resultSummary && (
          <div className="oc-result-summary">
            <span>结果</span>
            {item.resultSummary}
          </div>
        )}
        {item.progressLabel && (
          <div className="oc-capability-progress">
            <span />
            {item.progressLabel}
          </div>
        )}
        <time>{item.occurredAtLabel}</time>
      </div>
    </article>
  );
}

function TaskTimelineItem({ item }: { item: TimelineTaskItem }) {
  return (
    <article className="oc-task-card">
      <span className="oc-task-check" data-tone={workTone[item.state]}>
        {item.state === "completed" ? "✓" : item.state === "running" ? "…" : ""}
      </span>
      <div>
        <small>{item.capability ? `计划任务 · ${item.capability}` : "计划任务"}</small>
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
    <article className="oc-skill-card" data-state={item.state}>
      <div className="oc-skill-mark">S</div>
      <div className="oc-skill-copy">
        <small>SKILL · 渐进加载</small>
        <h3>{item.skillName}</h3>
        <p>
          {item.resourceLabel} · {item.purposeLabel}
        </p>
        {item.resultSummary && (
          <div className="oc-skill-result">{item.resultSummary}</div>
        )}
        <time>{item.occurredAtLabel}</time>
      </div>
      <StatusPill
        label={item.stateLabel}
        tone={tone}
        pulse={item.state === "running"}
      />
    </article>
  );
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
    <article className="oc-runtime-card" data-state={item.state}>
      <header>
        <div>
          <small>
            RUNTIME · {item.backend} · {item.capability}
          </small>
          <h3>容器执行</h3>
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
      <dl className="oc-runtime-meta">
        <div>
          <dt>workdir</dt>
          <dd>{item.workdir}</dd>
        </div>
        <div>
          <dt>exit</dt>
          <dd>{item.exitCode ?? (item.state === "running" ? "running" : "—")}</dd>
        </div>
        {item.durationLabel && (
          <div>
            <dt>duration</dt>
            <dd>{item.durationLabel}</dd>
          </div>
        )}
      </dl>
      <details>
        <summary>argv · {item.command.length}</summary>
        <pre>{JSON.stringify(item.command, null, 2)}</pre>
      </details>
      {item.code && (
        <details open>
          <summary>执行代码{item.commandTruncated ? " · 已截断" : ""}</summary>
          <pre>{item.code}</pre>
        </details>
      )}
      {(item.stdout || item.state === "running") && (
        <details open>
          <summary>stdout{item.stdoutTruncated ? " · 已截断" : ""}</summary>
          <pre>{item.stdout || "等待输出…"}</pre>
        </details>
      )}
      {item.stderr && (
        <details open>
          <summary>stderr{item.stderrTruncated ? " · 已截断" : ""}</summary>
          <pre className="is-stderr">{item.stderr}</pre>
        </details>
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
      <time>{item.occurredAtLabel}</time>
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
            setPreview({
              state: "ready",
              text: JSON.stringify(boundedJson(JSON.parse(text)), null, 2),
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
      {preview.state === "ready" && preview.text !== undefined && (
        <pre>{preview.text}</pre>
      )}
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
          <span>A</span>
          <span>B</span>
          <i />
        </div>
        <h2>从一个明确的分析目标开始</h2>
        <p>
          Agent 会按需调用 Graph A 数据分析与 Graph B
          深度注释能力，并将可恢复的事实持续记录到时间线。
        </p>
        <div className="oc-empty-capabilities">
          <span>Graph A · 单细胞分析</span>
          <span>Graph B · 深度注释</span>
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
        if (item.kind === "skill")
          return <SkillTimelineItem item={item} key={item.id} />;
        if (item.kind === "capability")
          return <CapabilityTimelineItem item={item} key={item.id} />;
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

function CapabilityList({
  capabilities,
}: {
  capabilities: readonly CapabilityViewModel[];
}) {
  if (!capabilities.length)
    return (
      <InspectorEmpty
        title="尚无能力调用"
        description="只有已经进入事件日志的能力事实会显示在这里。"
      />
    );
  return (
    <div className="oc-inspector-list">
      {capabilities.map((capability) => (
        <article
          className="oc-inspector-capability"
          data-family={capability.family}
          key={capability.id}
        >
          <span className="oc-capability-monogram">
            {capability.family === "graph_a"
              ? "A"
              : capability.family === "graph_b"
                ? "B"
                : "T"}
          </span>
          <div>
            <small>
              {capability.name}
              {capability.invocationCount
                ? ` · ${capability.invocationCount} 次`
                : ""}
            </small>
            <strong>{capability.title}</strong>
            <p>{capability.description}</p>
            <StatusPill
              label={capability.stateLabel}
              tone={workTone[capability.state]}
              pulse={capability.state === "running"}
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
        description="需要人工确认的能力调用会集中出现在这里。"
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
            #{event.sequence}
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
  const initialTab = model.reviews.some((review) => review.state === "pending")
    ? "reviews"
    : "tasks";
  const [tab, setTab] = useState<InspectorTab>(initialTab);
  const counts = useMemo(
    () => ({
      tasks: model.tasks.length,
      capabilities: model.capabilities.length,
      reviews: model.reviews.length,
      artifacts: model.artifacts.length,
      events: model.events.length,
    }),
    [model],
  );

  return (
    <div className="oc-inspector-panel">
      <header className="oc-inspector-header">
        <div>
          <small>RUN INSPECTOR</small>
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
        {tab === "tasks" && <TaskList tasks={model.tasks} />}
        {tab === "capabilities" && (
          <CapabilityList capabilities={model.capabilities} />
        )}
        {tab === "reviews" && (
          <ReviewList reviews={model.reviews} actions={actions} />
        )}
        {tab === "artifacts" && (
          <ArtifactList artifacts={model.artifacts} actions={actions} />
        )}
        {tab === "events" && <EventList events={model.events} />}
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
            "Agent 会按需选择能力；结果以持久化事件为准"}
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
