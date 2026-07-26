export type WorkspaceViewState = "loading" | "empty" | "error" | "ready";

export type ConnectionState = "connected" | "reconnecting" | "offline";

export type RunState =
  | "idle"
  | "pending"
  | "running"
  | "review_required"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkItemState =
  | "pending"
  | "running"
  | "review_required"
  | "completed"
  | "failed"
  | "interrupted"
  | "cancelled";

export type ToolFamily =
  | "inspect"
  | "transform"
  | "analyze"
  | "annotate"
  | "visualize"
  | "custom";

export interface ConversationNavItem {
  id: string;
  title: string;
  updatedAtLabel: string;
  runState?: RunState;
}

export interface DatasetNavItem {
  artifactId: string;
  name: string;
  detail: string;
  sizeLabel?: string;
}

export interface RunSummaryViewModel {
  id: string;
  state: RunState;
  stateLabel: string;
  attemptLabel?: string;
  startedAtLabel?: string;
  terminalSummary?: string;
  canCancel: boolean;
}

export interface TimelineMessageItem {
  id: string;
  kind: "message";
  role: "user" | "assistant";
  authorLabel: string;
  content: string;
  occurredAtLabel: string;
}

export type ActivityProcessState =
  | "completed"
  | "active"
  | "pending"
  | "failed";

export interface TimelineToolItem {
  id: string;
  kind: "tool";
  toolName: string;
  family: ToolFamily;
  title: string;
  purpose: string;
  state: WorkItemState;
  stateLabel: string;
  attempt: number;
  durationLabel?: string;
  process: readonly {
    label: string;
    detail?: string;
    state: ActivityProcessState;
  }[];
  resultSummary?: string;
  artifactCount: number;
  errorCode?: string;
  recoveryHint?: string;
  occurredAtLabel: string;
}

export interface TimelineTaskItem {
  id: string;
  kind: "task";
  title: string;
  description?: string;
  capability?: string;
  state: WorkItemState;
  stateLabel: string;
  occurredAtLabel: string;
}

export interface TimelineSkillItem {
  id: string;
  kind: "skill";
  skillName: string;
  resourceLabel: string;
  purposeLabel: string;
  state: "running" | "completed" | "failed" | "cancelled";
  stateLabel: string;
  process: readonly {
    label: string;
    detail?: string;
    state: ActivityProcessState;
  }[];
  resultSummary?: string;
  occurredAtLabel: string;
}

export interface TimelineRuntimeItem {
  id: string;
  kind: "runtime";
  runtimeCommandId: string;
  toolName: string;
  backend: string;
  command: readonly string[];
  code?: string;
  workdir: string;
  state: "running" | "completed" | "failed" | "timeout" | "cancelled";
  stdout: string;
  stderr: string;
  exitCode?: number;
  durationLabel?: string;
  commandTruncated: boolean;
  stdoutTruncated: boolean;
  stderrTruncated: boolean;
  redacted: boolean;
  process: readonly {
    label: string;
    detail?: string;
    state: ActivityProcessState;
  }[];
  occurredAtLabel: string;
}

export type ArtifactPreviewMode = "image" | "json" | "text" | "table" | "none";

export interface TimelineArtifactItem {
  id: string;
  kind: "artifact";
  artifactId: string;
  name: string;
  artifactKind: string;
  mediaType?: string;
  sizeLabel: string;
  previewMode: ArtifactPreviewMode;
  previewReason?: string;
  occurredAtLabel: string;
}

export interface TimelineReviewItem {
  id: string;
  kind: "review";
  reviewId: string;
  title: string;
  description: string;
  state: "pending" | "approved" | "rejected";
  decisionPending: boolean;
  occurredAtLabel: string;
}

export interface TimelineNoticeItem {
  id: string;
  kind: "notice";
  tone: "neutral" | "warning" | "error";
  title: string;
  description?: string;
  occurredAtLabel: string;
}

export type TimelineItem =
  | TimelineMessageItem
  | TimelineTaskItem
  | TimelineToolItem
  | TimelineSkillItem
  | TimelineRuntimeItem
  | TimelineArtifactItem
  | TimelineReviewItem
  | TimelineNoticeItem;

export interface TaskViewModel {
  id: string;
  runId: string;
  title: string;
  description?: string;
  state: WorkItemState;
  stateLabel: string;
}

export interface ToolExecutionViewModel {
  id: string;
  runId: string;
  name: string;
  family: ToolFamily;
  title: string;
  description: string;
  state: WorkItemState;
  stateLabel: string;
  invocationCount?: number;
}

export interface ReviewViewModel {
  id: string;
  runId?: string;
  title: string;
  description: string;
  capabilityLabel: string;
  state: "pending" | "approved" | "rejected";
  decisionPending: boolean;
  decisionLabel?: string;
}

export interface ArtifactViewModel {
  id: string;
  runId?: string;
  name: string;
  kindLabel: string;
  sizeLabel: string;
  createdAtLabel: string;
  canDownload: boolean;
  downloadPending: boolean;
}

export interface EventViewModel {
  id: string;
  runId: string;
  sequence: string;
  type: string;
  occurredAtLabel: string;
  occurredAtIso: string;
  summary: string;
  context?: string;
  tone: "neutral" | "active" | "success" | "warning" | "danger";
  metadata: readonly EventMetadataItem[];
}

export interface EventMetadataItem {
  label: string;
  value: string;
}

export interface ConversationWorkspaceViewModel {
  viewState: WorkspaceViewState;
  errorMessage?: string;
  commandErrorMessage?: string;
  connection: ConnectionState;
  connectionLabel: string;
  conversations: readonly ConversationNavItem[];
  selectedConversationId?: string;
  datasets: readonly DatasetNavItem[];
  selectedDatasetId?: string;
  title: string;
  subtitle?: string;
  run?: RunSummaryViewModel;
  timeline: readonly TimelineItem[];
  tasks: readonly TaskViewModel[];
  toolExecutions: readonly ToolExecutionViewModel[];
  reviews: readonly ReviewViewModel[];
  artifacts: readonly ArtifactViewModel[];
  events: readonly EventViewModel[];
  commands: {
    createConversationPending: boolean;
    importDatasetPending: boolean;
    cancelRunPending: boolean;
  };
  composer: {
    placeholder: string;
    disabled: boolean;
    disabledReason?: string;
  };
}

export type ReviewDecision = "approve" | "reject";

export interface ConversationWorkspaceActions {
  onCreateConversation?: () => void;
  onSelectConversation?: (conversationId: string) => void;
  onSelectDataset?: (artifactId: string) => void;
  onImportDataset?: () => void;
  onRetry?: () => void;
  onSubmit?: (instruction: string) => boolean | Promise<boolean>;
  onCancelRun?: (runId: string) => void;
  onReviewDecision?: (
    reviewId: string,
    decision: ReviewDecision,
    comment?: string,
  ) => void;
  onDownloadArtifact?: (artifactId: string, fileName: string) => void;
  onLoadArtifactContent?: (artifactId: string) => Promise<Blob>;
}
