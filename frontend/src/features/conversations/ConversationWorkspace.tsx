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
  MemoryCommandViewModel,
  MemoryItemViewModel,
  MemoryKind,
  MemoryRunMode,
  ReviewViewModel,
  RunState,
  TaskViewModel,
  TimelineArtifactItem,
  TimelineItem,
  TimelineMessageItem,
  TimelineMemoryItem,
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
  | "tasks"
  | "toolExecutions"
  | "reviews"
  | "artifacts"
  | "events"
  | "memories";

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
  memories: "记忆",
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

function MemoryTimelineItem({
  item,
  memories,
  commandsPending,
  command,
  actions,
}: {
  item: TimelineMemoryItem;
  memories: readonly MemoryItemViewModel[];
  commandsPending: boolean;
  command?: MemoryCommandViewModel;
  actions?: ConversationWorkspaceActions;
}) {
  const [confirmRejectProposal, setConfirmRejectProposal] = useState(false);
  const needsConfirmation =
    item.operation === "proposal" || item.operation === "forget";
  const primaryIdentity = item.identities[0];
  const currentMemory = primaryIdentity
    ? memories.find((memory) => memory.id === primaryIdentity.itemId)
    : undefined;
  const matchingMemory =
    currentMemory?.versionId === primaryIdentity?.versionId
      ? currentMemory
      : undefined;
  const commandTargetsOperation =
    (item.operation === "proposal" &&
      (command?.kind === "approve" || command?.kind === "purge")) ||
    (item.operation === "forget" && command?.kind === "forget");
  const cardCommand =
    commandTargetsOperation &&
    primaryIdentity !== undefined &&
    command?.memoryId === primaryIdentity.itemId
      ? command
      : undefined;
  const commandAction =
    cardCommand?.kind === "approve"
      ? "确认记住"
      : cardCommand?.kind === "purge"
        ? "拒绝并清除"
        : cardCommand?.kind === "forget"
          ? "确认忘记"
          : cardCommand?.kind === "correct"
            ? "保存纠正"
            : "更新记忆";
  const cardPending = cardCommand?.pending === true;
  const cardError = cardCommand?.errorMessage;
  const canApproveProposal =
    item.operation === "proposal" &&
    primaryIdentity !== undefined &&
    currentMemory?.status === "proposed" &&
    currentMemory.versionId === primaryIdentity.versionId;
  const canConfirmForget =
    item.operation === "forget" &&
    primaryIdentity !== undefined &&
    currentMemory?.status === "active" &&
    currentMemory.versionId === primaryIdentity.versionId;
  const canRejectProposal = canApproveProposal && currentMemory.canPurge;
  const resolution =
    item.operation === "proposal"
      ? currentMemory?.status === "purged"
        ? "rejected"
        : currentMemory?.status === "revoked"
          ? "forgotten"
        : matchingMemory?.status === "active"
          ? "remembered"
          : matchingMemory?.status === "proposed"
            ? "pending"
            : currentMemory
              ? "stale"
              : "historical"
      : item.operation === "forget"
        ? currentMemory?.status === "revoked" ||
          currentMemory?.status === "purged"
          ? "forgotten"
          : matchingMemory?.status === "active"
            ? "pending"
            : currentMemory
              ? "stale"
              : "historical"
        : "completed";
  const display =
    resolution === "remembered"
      ? {
          title: "已记住这条内容",
          stateLabel: "已记住",
          resultSummary: "已确认；未来相关对话可以使用这条记忆",
          finalStep: "已确认记住",
        }
      : resolution === "forgotten"
        ? {
            title: "已忘记这条内容",
            stateLabel:
              currentMemory?.status === "purged" ? "已清除" : "已忘记",
            resultSummary:
              currentMemory?.status === "purged"
                ? "正文已清除；未来对话不会再使用或从旧对话重新学习"
                : "已确认；未来对话不再使用这条记忆",
            finalStep:
              currentMemory?.status === "purged" ? "已彻底删除" : "已确认忘记",
          }
        : resolution === "rejected"
          ? {
              title: "已拒绝这条记忆候选",
              stateLabel: "未采用",
              resultSummary:
                currentMemory?.status === "purged"
                  ? "候选正文已清除；未来对话不会使用或从旧对话重新提议"
                  : "候选未采用；未来对话不会使用这条内容",
              finalStep:
                currentMemory?.status === "purged"
                  ? "已拒绝并清除候选"
                  : "已拒绝候选",
            }
        : resolution === "stale"
          ? {
              title: "这条记忆请求已失效",
              stateLabel: "已失效",
              resultSummary: "记忆状态或版本已经变化，无需再次确认",
              finalStep: "请求已失效",
            }
          : resolution === "historical"
            ? {
                title: "当前无法核对记忆状态",
                stateLabel: "状态不可用",
                resultSummary: "无法读取对应的记忆资源；请稍后重试记忆服务",
                finalStep: undefined,
              }
          : {
              title: item.title,
              stateLabel: item.stateLabel,
              resultSummary: item.resultSummary,
              finalStep: undefined,
            };
  const process = cardPending
    ? item.process.map((step, index) =>
        index === item.process.length - 1
          ? {
              ...step,
              label: `正在${commandAction}`,
              detail: "请求已经提交，正在等待 backend 返回权威状态",
              state: "active" as const,
            }
          : step,
      )
    : cardError
      ? item.process.map((step, index) =>
          index === item.process.length - 1
            ? {
                ...step,
                label: `${commandAction}失败`,
                detail: cardError,
                state: "failed" as const,
              }
            : step,
        )
      : resolution === "historical"
      ? item.process.map((step, index) =>
          index === item.process.length - 1
            ? {
                ...step,
                label: "无法核对当前资源",
                detail: "记忆资源暂时不可用，当前不会执行确认操作",
                state: "failed" as const,
              }
            : step,
        )
        : display.finalStep === undefined
          ? item.process
          : item.process.map((step, index) =>
              index === item.process.length - 1
                ? {
                    ...step,
                    label: display.finalStep,
                    detail: undefined,
                    state: "completed" as const,
                  }
                : step,
            );
  const memoryContent = matchingMemory?.content;
  const memoryCharacters =
    memoryContent === undefined ? undefined : Array.from(memoryContent);
  const contentLength = memoryCharacters?.length ?? 0;
  const contentPreview = memoryCharacters?.slice(0, 280).join("");
  const contentTruncated = contentLength > 280;
  const tone = cardPending
    ? "active"
    : cardError
      ? "danger"
      : item.outcome === "degraded" ||
          resolution === "pending" ||
          resolution === "historical"
        ? "warning"
        : resolution === "stale"
          ? "neutral"
          : "success";
  const visibleStateLabel = cardPending
    ? "处理中"
    : cardError
      ? "操作失败"
      : display.stateLabel;
  const visibleResultSummary = cardPending
    ? `正在${commandAction}，请稍候`
    : cardError
      ? `${commandAction}未完成；可以核对错误后重试`
      : display.resultSummary;
  const operationLabel = {
    snapshot: "记忆快照",
    search: "记忆搜索",
    proposal: "记忆提议",
    forget: "遗忘请求",
  }[item.operation];
  const toolName =
    item.operation === "search"
      ? "search_memory"
      : item.operation === "proposal"
        ? "propose_memory"
        : item.operation === "forget"
          ? "forget_memory"
          : undefined;
  const activityKind = item.operation === "snapshot" ? "BACKEND" : "TOOL";
  return (
    <article
      className={`oc-activity-card oc-memory-activity ${
        item.operation === "snapshot"
          ? "oc-backend-activity"
          : "oc-tool-activity"
      }`}
      data-activity-kind={activityKind.toLowerCase()}
      data-state={item.outcome}
      data-operation={item.operation}
    >
      <header>
        <div className="oc-activity-heading">
          <span className="oc-activity-kind">{activityKind}</span>
          <div>
            <h3>{display.title}</h3>
            <p>{item.description}</p>
          </div>
        </div>
        <StatusPill label={visibleStateLabel} tone={tone} />
      </header>
      <div className="oc-activity-purpose">
        <span className="oc-activity-section-label">动作</span>
        <strong>{item.actionSummary}</strong>
      </div>
      <ActivityProcess steps={process} />
      <div
        className="oc-activity-result"
        data-state={
          cardError || item.outcome === "degraded"
            ? "failed"
            : cardPending ||
                (needsConfirmation &&
                (resolution === "pending" || resolution === "historical")
                )
              ? "pending"
              : "completed"
        }
      >
        <span className="oc-activity-section-label">结果</span>
        <strong>{visibleResultSummary}</strong>
        {cardError && <small>错误：{cardError}</small>}
        {item.degradedCode && <code>{item.degradedCode}</code>}
      </div>
      {contentPreview && (
        <div className="oc-memory-confirm-preview">
          <span>
            {item.operation === "proposal"
              ? "将完整保存的来源消息"
              : "要忘记的内容"}
          </span>
          {item.operation === "proposal" && (
            <small>
              共 {contentLength} 字
              {contentTruncated ? " · 当前预览前 280 字" : " · 当前已显示全文"}
            </small>
          )}
          <p>{contentPreview}{contentTruncated ? "…" : ""}</p>
          {item.operation === "proposal" && contentTruncated && memoryContent && (
            <details>
              <summary>查看完整原文</summary>
              <pre className="oc-memory-full-content">{memoryContent}</pre>
            </details>
          )}
        </div>
      )}
      {item.identities.length > 0 && (
        <details className="oc-memory-identities">
          <summary>查看版本身份 · {item.identities.length}</summary>
          <dl>
            {item.identities.map((identity) => (
              <div key={`${identity.itemId}:${identity.versionId}`}>
                <dt>
                  {identity.kind} · v{identity.version}
                </dt>
                <dd>
                  item {identity.itemId} · version {identity.versionId} ·{" "}
                  {identity.source} · {identity.reason}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {(canApproveProposal || canConfirmForget) && (
        <div className="oc-memory-inline-actions">
          <span>
            {canApproveProposal
              ? "候选已按整条来源消息保存；确认后才会用于未来相关对话。"
              : "确认后，未来对话将不再使用这条记忆。"}
          </span>
          <div>
            {canRejectProposal && (
              <button
                disabled={commandsPending}
                type="button"
                onClick={() => setConfirmRejectProposal(true)}
              >
                不采用并清除
              </button>
            )}
            <button
              className={canApproveProposal ? "is-primary" : undefined}
              disabled={commandsPending}
              type="button"
              onClick={() => {
                if (canApproveProposal) {
                  void actions?.onApproveMemory?.(
                    primaryIdentity.itemId,
                    primaryIdentity.version,
                  );
                } else {
                  void actions?.onForgetMemory?.(
                    primaryIdentity.itemId,
                    primaryIdentity.version,
                  );
                }
              }}
            >
              {canApproveProposal ? "确认记住" : "确认忘记"}
            </button>
          </div>
        </div>
      )}
      {canRejectProposal && confirmRejectProposal && (
        <div
          aria-label="确认不采用记忆候选"
          className="oc-memory-reject-confirm"
          role="group"
        >
          <span>候选正文会被清除，原始对话仍会保留。</span>
          <div>
            <button
              disabled={commandsPending}
              type="button"
              onClick={() => setConfirmRejectProposal(false)}
            >
              取消
            </button>
            <button
              className="is-danger"
              disabled={commandsPending}
              type="button"
              onClick={() =>
                void actions?.onPurgeMemory?.(
                  primaryIdentity.itemId,
                  primaryIdentity.version,
                )
              }
            >
              确认不采用并清除
            </button>
          </div>
        </div>
      )}
      <footer>
        <time>{item.occurredAtLabel}</time>
        <span>
          {toolName ? `Tool · ${toolName}` : `Memory Plane · ${operationLabel}`}
        </span>
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
  memories,
  memoryCommandsPending,
  memoryCommand,
  actions,
}: {
  items: readonly TimelineItem[];
  memories: readonly MemoryItemViewModel[];
  memoryCommandsPending: boolean;
  memoryCommand?: MemoryCommandViewModel;
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
        if (item.kind === "memory")
          return (
            <MemoryTimelineItem
              actions={actions}
              command={memoryCommand}
              commandsPending={memoryCommandsPending}
              item={item}
              key={item.id}
              memories={memories}
            />
          );
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

const memoryKindLabels: Record<MemoryKind, string> = {
  response_preference: "回复偏好",
  profile_fact: "个人事实",
  project_context: "项目上下文",
  scientific_observation: "科研观察",
};
const creatableMemoryKinds: MemoryKind[] = [
  "response_preference",
  "profile_fact",
  "project_context",
];

function MemorySettingSwitch({
  checked,
  disabled,
  label,
  description,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  description: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="oc-memory-setting">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <input
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.checked)}
        type="checkbox"
      />
    </label>
  );
}

function MemoryItemCard({
  item,
  actions,
  disabled,
}: {
  item: MemoryItemViewModel;
  actions?: ConversationWorkspaceActions;
  disabled: boolean;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState(item.content ?? "");
  const [confirmForget, setConfirmForget] = useState(false);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const pending = disabled || actions === undefined;

  useEffect(() => {
    // Version/status changes represent a different immutable memory view.
    // Reset every local plaintext-bearing draft, especially when purge swaps
    // the item to its body-less tombstone representation.
    setCorrection(item.content ?? "");
    setCorrecting(false);
    setConfirmForget(false);
    setConfirmPurge(false);
  }, [item.status, item.versionId]);

  const runAndClose = async (
    command: boolean | Promise<boolean> | undefined,
    close: () => void,
  ) => {
    if ((await command) === true) close();
  };

  return (
    <article className="oc-memory-item" data-status={item.status}>
      <header>
        <div>
          <small>{item.kindLabel}</small>
          <strong>
            {item.statusLabel}
            {item.version ? ` · v${item.version}` : ""}
          </strong>
        </div>
        <StatusPill
          label={item.statusLabel}
          tone={
            item.status === "active"
              ? "success"
              : item.status === "proposed"
                ? "warning"
                : "neutral"
          }
        />
      </header>
      {item.content ? (
        <p className="oc-memory-content">{item.content}</p>
      ) : (
        <p className="oc-memory-content is-empty">
          这条记忆已被清除，正文不可用。
        </p>
      )}
      <dl className="oc-memory-meta">
        <div>
          <dt>来源</dt>
          <dd>
            {item.sourceLabel}
            {item.sourceDetail ? ` · ${item.sourceDetail}` : ""}
          </dd>
        </div>
        {item.datasetScopeLabel && (
          <div>
            <dt>数据范围</dt>
            <dd>{item.datasetScopeLabel}</dd>
          </div>
        )}
        <div>
          <dt>更新</dt>
          <dd>{item.updatedAtLabel}</dd>
        </div>
      </dl>
      <details className="oc-memory-identities">
        <summary>技术信息</summary>
        <dl>
          <div>
            <dt>稳定键</dt>
            <dd>{item.stableKey}</dd>
          </div>
          <div>
            <dt>身份</dt>
            <dd>
              item {item.id}
              {item.versionId ? ` · version ${item.versionId}` : ""}
            </dd>
          </div>
          {item.contentSha256 && (
            <div>
              <dt>内容哈希</dt>
              <dd>{item.contentSha256}</dd>
            </div>
          )}
        </dl>
      </details>
      {correcting && item.version !== undefined && (
        <div className="oc-memory-correction">
          <label>
            新版本正文
            <textarea
              maxLength={8_000}
              onChange={(event) => setCorrection(event.currentTarget.value)}
              rows={4}
              value={correction}
            />
          </label>
          <div>
            <button type="button" onClick={() => setCorrecting(false)}>
              取消
            </button>
            <button
              className="is-primary"
              disabled={!correction.trim() || pending}
              type="button"
              onClick={() =>
                void runAndClose(
                  actions?.onCorrectMemory?.(
                    item.id,
                    item.version!,
                    correction.trim(),
                  ),
                  () => setCorrecting(false),
                )
              }
            >
              保存为新版本
            </button>
          </div>
        </div>
      )}
      {confirmForget && item.version !== undefined && (
        <div className="oc-memory-confirm" data-tone="warning">
          <p>忘记后，未来对话将不再使用这条记忆。</p>
          <button type="button" onClick={() => setConfirmForget(false)}>
            取消
          </button>
          <button
            disabled={pending}
            type="button"
            onClick={() =>
              void runAndClose(
                actions?.onForgetMemory?.(item.id, item.version!),
                () => setConfirmForget(false),
              )
            }
          >
            确认忘记
          </button>
        </div>
      )}
      {confirmPurge && item.version !== undefined && (
        <div className="oc-memory-confirm" data-tone="danger">
          <p>
            彻底删除会清除保存的正文，但不会删除原始对话，也无法撤回已经发送给
            当前模型的内容。
          </p>
          <button type="button" onClick={() => setConfirmPurge(false)}>
            取消
          </button>
          <button
            className="is-danger"
            disabled={pending}
            type="button"
            onClick={() =>
              void runAndClose(
                actions?.onPurgeMemory?.(item.id, item.version!),
                () => setConfirmPurge(false),
              )
            }
          >
            确认彻底删除
          </button>
        </div>
      )}
      <footer>
        {item.canApprove && item.version !== undefined && (
          <button
            disabled={pending}
            type="button"
            onClick={() =>
              void actions?.onApproveMemory?.(item.id, item.version!)
            }
          >
            确认采用
          </button>
        )}
        {item.canCorrect && !correcting && (
          <button
            disabled={pending}
            type="button"
            onClick={() => {
              setCorrection(item.content ?? "");
              setCorrecting(true);
            }}
          >
            纠正
          </button>
        )}
        {item.canForget && !confirmForget && (
          <button
            disabled={pending}
            type="button"
            onClick={() => setConfirmForget(true)}
          >
            忘记
          </button>
        )}
        {item.canPurge && !confirmPurge && (
          <button
            className="is-danger"
            disabled={pending}
            type="button"
            onClick={() => setConfirmPurge(true)}
          >
            彻底删除
          </button>
        )}
      </footer>
    </article>
  );
}

function MemoryManager({
  model,
  actions,
}: ConversationWorkspaceProps) {
  const [consentOpen, setConsentOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [kind, setKind] = useState<MemoryKind>("response_preference");
  const [content, setContent] = useState("");
  const memory = model.memory;
  const disabled = memory.loading || memory.commandsPending;
  const savedMemories = memory.items.filter(
    (item) => item.status !== "purged",
  );
  const purgedMemories = memory.items.filter(
    (item) => item.status === "purged",
  );
  const memoryEnabled =
    memory.useMemory &&
    memory.generateCandidates &&
    memory.enableAgentTools &&
    memory.providerConsentGranted;

  const create = async () => {
    const normalized = content.trim();
    if (!normalized) return;
    const succeeded = await actions?.onCreateMemory?.({
      kind,
      content: normalized,
    });
    if (succeeded === true) {
      setContent("");
      setCreateOpen(false);
    }
  };

  return (
    <div className="oc-memory-manager">
      <div className="oc-memory-disclosure">
        <strong>长期记忆</strong>
        <p>
          开启后，Agent 会自动回忆相关背景，也会在发现稳定、可复用的偏好或项目信息时
          主动提出记忆候选。候选和撤销请求都会在当前时间线等待你确认。
        </p>
      </div>
      {memory.errorMessage && (
        <div className="oc-memory-error" role="status">
          <strong>长期记忆暂不可用</strong>
          <span>
            {memory.errorMessage}。普通会话仍可继续，本次不会读取历史记忆。
          </span>
        </div>
      )}
      {memory.commandErrorMessage && (
        <div className="oc-memory-error" role="alert">
          <strong>记忆操作未完成</strong>
          <span>{memory.commandErrorMessage}</span>
        </div>
      )}
      <section className="oc-memory-settings" aria-label="记忆设置">
        <MemorySettingSwitch
          checked={memoryEnabled}
          description="自动回忆相关内容，并主动提议值得长期保留的信息"
          disabled={disabled || !memory.available}
          label="跨会话记忆"
          onChange={(checked) => {
            if (!checked) {
              void actions?.onDisableMemory?.();
            } else if (memory.providerConsentGranted) {
              void actions?.onGrantMemoryConsentAndEnable?.();
            } else {
              setConsentOpen(true);
            }
          }}
        />
      </section>
      {consentOpen && (
        <section
          aria-labelledby="memory-consent-title"
          className="oc-memory-consent"
          role="dialog"
        >
          <strong id="memory-consent-title">开启长期记忆</strong>
          <p>
            为了在新对话中使用记忆，相关内容会随当前问题发送给你已经配置的
            LLM。你可以随时关闭、纠正或删除记忆。
          </p>
          <div>
            <button type="button" onClick={() => setConsentOpen(false)}>
              暂不开启
            </button>
            <button
              className="is-primary"
              disabled={disabled}
              type="button"
              onClick={async () => {
                if (
                  (await actions?.onGrantMemoryConsentAndEnable?.()) === true
                ) {
                  setConsentOpen(false);
                }
              }}
            >
              开启
            </button>
          </div>
        </section>
      )}
      <details className="oc-memory-delete-boundary">
        <summary>隐私与授权</summary>
        <p>
          只有与当前问题相关的已确认记忆会发送给当前 LLM。关闭后，新对话不再读取
          记忆；已经发送的内容无法撤回。
        </p>
        {memory.providerConsentGranted && (
          <button
            disabled={disabled}
            type="button"
            onClick={() => void actions?.onRevokeMemoryConsent?.()}
          >
            关闭并撤回授权
          </button>
        )}
      </details>
      <div className="oc-memory-manager-heading">
        <div>
          <strong>已保存的记忆</strong>
          <small>{savedMemories.length} 条</small>
        </div>
        <button
          disabled={disabled || !memory.available}
          type="button"
          onClick={() => setCreateOpen((current) => !current)}
        >
          {createOpen ? "取消新增" : "新增"}
        </button>
      </div>
      {createOpen && (
        <section className="oc-memory-create">
          <label>
            类型
            <select
              onChange={(event) =>
                setKind(event.currentTarget.value as MemoryKind)
              }
              value={kind}
            >
              {creatableMemoryKinds.map((value) => (
                <option key={value} value={value}>
                  {memoryKindLabels[value]}
                </option>
              ))}
            </select>
          </label>
          <label>
            需要记住的内容
            <textarea
              maxLength={8_000}
              onChange={(event) => setContent(event.currentTarget.value)}
              placeholder="例如：回答时先给结论，正文不超过三点"
              rows={5}
              value={content}
            />
          </label>
          <p>
            当前数据得出的科研结论需要由分析流程记录来源，不能在这里手动添加。
          </p>
          <button
            className="is-primary"
            disabled={
              disabled ||
              !content.trim()
            }
            type="button"
            onClick={() => void create()}
          >
            保存记忆
          </button>
        </section>
      )}
      {memory.loading ? (
        <p className="oc-memory-loading">正在读取长期记忆…</p>
      ) : savedMemories.length === 0 ? (
        <InspectorEmpty
          title="还没有长期记忆"
          description="在自然对话中表达长期偏好或项目背景，Agent 会按需提出候选；也可以在这里手动新增。"
        />
      ) : (
        <div className="oc-memory-list">
          {savedMemories.map((item) => (
            <MemoryItemCard
              actions={actions}
              disabled={disabled}
              item={item}
              key={`${item.id}:${item.versionId ?? item.status}`}
            />
          ))}
        </div>
      )}
      {purgedMemories.length > 0 && (
        <details className="oc-memory-delete-boundary">
          <summary>已清除记录 · {purgedMemories.length}</summary>
          <p>这里只保留不含正文的删除记录，用于避免 Agent 从旧对话重新学习。</p>
          <div className="oc-memory-list">
            {purgedMemories.map((item) => (
              <MemoryItemCard
                actions={actions}
                disabled={disabled}
                item={item}
                key={`${item.id}:${item.status}`}
              />
            ))}
          </div>
        </details>
      )}
      <details className="oc-memory-delete-boundary">
        <summary>忘记与彻底删除有什么区别？</summary>
        <p>
          “忘记”让未来对话不再使用这条内容；“彻底删除”还会清除保存的正文，并阻止
          Agent 从旧对话重新学习它。两者都不会删除原始对话。
        </p>
      </details>
    </div>
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
  const savedMemoryCount = model.memory.items.filter(
    (item) => item.status !== "purged",
  ).length;
  const counts = useMemo(
    () => ({
      tasks: scopedTasks.length,
      toolExecutions: scopedToolExecutions.length,
      reviews: scopedReviews.length,
      artifacts: scopedArtifacts.length,
      events: scopedEvents.length,
      memories: savedMemoryCount,
    }),
    [
      scopedArtifacts,
      scopedEvents,
      scopedReviews,
      scopedTasks,
      scopedToolExecutions,
      savedMemoryCount,
    ],
  );

  return (
    <div className="oc-inspector-panel">
      <header className="oc-inspector-header">
        <div>
          <small>
            {tab === "memories"
              ? "GLOBAL MEMORY"
              : effectiveScope === "run"
              ? `RUN ${model.run?.id.slice(0, 8)}`
              : "CONVERSATION"}
          </small>
          <h2>{tab === "memories" ? "记忆管理器" : "运行检查器"}</h2>
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
      <div
        className="oc-inspector-scope"
        data-hidden={tab === "memories"}
        role="group"
        aria-label="检查器作用域"
      >
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
        {tab === "memories" && (
          <MemoryManager actions={actions} model={model} />
        )}
      </div>
      <footer className="oc-inspector-footer">
        <span className="oc-authority-mark" />
        {tab === "memories"
          ? "长期记忆保存在本机 PostgreSQL"
          : "PostgreSQL 持久化事件为权威来源"}
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

function CommandErrorBanner({
  message,
  title = "操作未完成",
}: {
  message?: string;
  title?: string;
}) {
  if (!message) return null;
  return (
    <div className="oc-command-error" role="alert">
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  );
}

function Composer({ model, actions }: ConversationWorkspaceProps) {
  const [draft, setDraft] = useState("");
  const memoryEnabled =
    model.memory.available &&
    model.memory.useMemory &&
    model.memory.generateCandidates &&
    model.memory.enableAgentTools &&
    model.memory.providerConsentGranted;
  const submissionMemoryMode: MemoryRunMode = memoryEnabled
    ? "default"
    : "off";
  const memoryCommandPending = model.memory.commandsPending;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const instruction = draft.trim();
    if (
      !instruction ||
      model.composer.disabled ||
      memoryCommandPending
    ) {
      return;
    }
    try {
      const memorySnapshot = {
        mode: submissionMemoryMode,
        refs: [],
      } as const;
      if (
        (await actions?.onSubmit?.(instruction, memorySnapshot)) === true
      ) {
        setDraft("");
      }
    } catch {
      // 父级 mutation 负责展示 conversation-scoped 错误；保留草稿供重试。
    }
  };
  return (
    <form className="oc-composer" onSubmit={submit}>
      <div className="oc-composer-memory">
        <span data-enabled={memoryEnabled}>
          <strong>{memoryEnabled ? "记忆已开启" : "记忆未启用"}</strong>
          {" · "}
          {!model.memory.available
            ? "服务不可用，本次按普通会话执行"
            : memoryEnabled
              ? "会自动使用与当前问题相关的内容"
              : "可在右侧记忆面板中开启"}
        </span>
      </div>
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
          disabled={
            model.composer.disabled ||
            memoryCommandPending ||
            !draft.trim()
          }
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
            (memoryCommandPending
              ? "记忆设置正在确认，完成后才能发送"
              : "Agent 会按需直接回复、加载 Skill 或调用 Tool")}
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
        <CommandErrorBanner
          message={model.memory.commandErrorMessage}
          title="记忆操作未完成"
        />
        <div className="oc-workspace-content">
          {model.viewState === "loading" && <LoadingWorkspace />}
          {model.viewState === "error" && (
            <ErrorWorkspace
              message={model.errorMessage}
              onRetry={actions?.onRetry}
            />
          )}
          {(model.viewState === "ready" || model.viewState === "empty") && (
            <Timeline
              actions={actions}
              items={model.timeline}
              memoryCommand={model.memory.command}
              memoryCommandsPending={model.memory.commandsPending}
              memories={model.memory.items}
            />
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
