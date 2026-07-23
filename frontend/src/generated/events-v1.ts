// 此文件由 frontend/scripts/generate-contracts.mjs 生成，请勿手工修改。
export type EventContract = (PersistedEvent | TransientEvent)
export type PersistedEvent = (RunCreatedEvent | RunStartedEvent | AgentTurnStartedEvent | MessageCompletedEvent | TaskCreatedEvent | TaskUpdatedEvent | SkillLoadStartedEvent | SkillLoadCompletedEvent | SkillLoadFailedEvent | CapabilityStartedEvent | CapabilityCompletedEvent | CapabilityFailedEvent | CapabilityRetryingEvent | CapabilityProgressEvent | RuntimeCommandStartedEvent | RuntimeOutputEvent | RuntimeCommandCompletedEvent | ArtifactCreatedEvent | ReviewRequestedEvent | ReviewResolvedEvent | BudgetExhaustedEvent | RunCancelRequestedEvent | RunInterruptedEvent | RunCompletedEvent | RunFailedEvent | RunCancelledEvent)
export type ConversationId = string
export type EventId = string
export type OccurredAt = string
export type Status = "pending"
export type RunId = string
export type SchemaVersion = 1
export type Sequence = string
export type Type = "run.created"
export type ConversationId1 = string
export type EventId1 = string
export type OccurredAt1 = string
export type Status1 = "running"
export type RunId1 = string
export type SchemaVersion1 = 1
export type Sequence1 = string
export type Type1 = "run.started"
export type ConversationId2 = string
export type EventId2 = string
export type OccurredAt2 = string
export type RemainingTurns = number
export type TurnIndex = number
export type RunId2 = string
export type SchemaVersion2 = 1
export type Sequence2 = string
export type Type2 = "agent.turn_started"
export type ConversationId3 = string
export type EventId3 = string
export type OccurredAt3 = string
export type Content = string
export type ContentArtifactId = (string | null)
export type HasToolCalls = boolean
export type MessageId = string
export type MessageRole = ("user" | "assistant" | "system" | "tool")
export type StopReason = (string | null)
export type TurnIndex1 = (number | null)
export type RunId3 = string
export type SchemaVersion3 = 1
export type Sequence3 = string
export type Type3 = "message.completed"
export type ConversationId4 = string
export type EventId4 = string
export type OccurredAt4 = string
export type CapabilityName = (string | null)
export type Description = (string | null)
export type Status2 = "pending"
export type TaskId = string
export type Title = string
export type RunId4 = string
export type SchemaVersion4 = 1
export type Sequence4 = string
export type Type4 = "task.created"
export type ConversationId5 = string
export type EventId5 = string
export type OccurredAt5 = string
export type TaskStatus = ("pending" | "in_progress" | "completed" | "failed" | "cancelled")
export type Summary = (string | null)
export type TaskId1 = string
export type RunId5 = string
export type SchemaVersion5 = 1
export type Sequence5 = string
export type Type5 = "task.updated"
export type ConversationId6 = string
export type EventId6 = string
export type OccurredAt6 = string
export type Purpose = ("domain_method" | "validation_rules" | "workflow_guidance" | "reference_lookup" | "example_lookup")
export type ResourceKind = ("body" | "reference" | "example")
export type ResourceName = (string | null)
export type SkillLoadId = string
export type SkillName = string
export type RunId6 = string
export type SchemaVersion6 = 1
export type Sequence6 = string
export type Type6 = "skill.load_started"
export type ConversationId7 = string
export type EventId7 = string
export type OccurredAt7 = string
export type ContentBytes = number
export type Outcome = ("loaded" | "already_loaded")
export type Purpose1 = ("domain_method" | "validation_rules" | "workflow_guidance" | "reference_lookup" | "example_lookup")
export type ResourceKind1 = ("body" | "reference" | "example")
export type ResourceName1 = (string | null)
export type SkillLoadId1 = string
export type SkillName1 = string
export type RunId7 = string
export type SchemaVersion7 = 1
export type Sequence7 = string
export type Type7 = "skill.load_completed"
export type ConversationId8 = string
export type EventId8 = string
export type OccurredAt8 = string
export type ErrorCode = "skill_resource_unavailable"
export type ErrorSummary = string
export type Purpose2 = ("domain_method" | "validation_rules" | "workflow_guidance" | "reference_lookup" | "example_lookup")
export type ResourceKind2 = ("body" | "reference" | "example")
export type ResourceName2 = (string | null)
export type SkillLoadId2 = string
export type SkillName2 = string
export type RunId8 = string
export type SchemaVersion8 = 1
export type Sequence8 = string
export type Type8 = "skill.load_failed"
export type ConversationId9 = string
export type EventId9 = string
export type OccurredAt9 = string
export type Attempt = number
export type CapabilityCallId = string
export type CapabilityName1 = string
export type TaskId2 = (string | null)
export type RunId9 = string
export type SchemaVersion9 = 1
export type Sequence9 = string
export type Type9 = "capability.started"
export type ConversationId10 = string
export type EventId10 = string
export type OccurredAt10 = string
/**
 * @maxItems 100
 */
export type ArtifactIds = string[]
export type Attempt1 = number
export type CapabilityCallId1 = string
export type CapabilityName2 = string
export type ResultStatus = (("completed" | "aborted") | null)
export type Summary1 = (string | null)
export type TaskId3 = (string | null)
export type RunId10 = string
export type SchemaVersion10 = 1
export type Sequence10 = string
export type Type10 = "capability.completed"
export type ConversationId11 = string
export type EventId11 = string
export type OccurredAt11 = string
export type Attempt2 = number
export type CapabilityCallId2 = string
export type CapabilityName3 = string
export type ErrorCode1 = string
export type ErrorSummary1 = string
export type Retryable = boolean
export type TaskId4 = (string | null)
export type RunId11 = string
export type SchemaVersion11 = 1
export type Sequence11 = string
export type Type11 = "capability.failed"
export type ConversationId12 = string
export type EventId12 = string
export type OccurredAt12 = string
export type CapabilityCallId3 = string
export type CapabilityName4 = string
export type DelaySeconds = number
export type NextAttempt = number
export type Reason = string
export type TaskId5 = (string | null)
export type RunId12 = string
export type SchemaVersion12 = 1
export type Sequence12 = string
export type Type12 = "capability.retrying"
export type ConversationId13 = string
export type EventId13 = string
export type OccurredAt13 = string
export type Attempt3 = number
export type CapabilityCallId4 = string
export type CapabilityName5 = string
export type Current = number
export type Message = string
export type Stage = "isolated_execution"
export type TaskId6 = (string | null)
export type Total = (number | null)
export type RunId13 = string
export type SchemaVersion13 = 1
export type Sequence13 = string
export type Type13 = "capability.progress"
export type ConversationId14 = string
export type EventId14 = string
export type OccurredAt14 = string
export type Attempt4 = number
export type Backend = string
export type CapabilityCallId5 = string
export type CapabilityName6 = string
export type Code = (string | null)
/**
 * @minItems 1
 * @maxItems 16
 */
export type Command = [string]|[string, string]|[string, string, string]|[string, string, string, string]|[string, string, string, string, string]|[string, string, string, string, string, string]|[string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string, string, string, string, string, string]|[string, string, string, string, string, string, string, string, string, string, string, string, string, string, string, string]
export type CommandTruncated = boolean
export type Redacted = boolean
export type RuntimeCommandId = string
export type TaskId7 = (string | null)
export type Workdir = string
export type RunId14 = string
export type SchemaVersion14 = 1
export type Sequence14 = string
export type Type14 = "runtime.command_started"
export type ConversationId15 = string
export type EventId15 = string
export type OccurredAt15 = string
export type Attempt5 = number
export type CapabilityCallId6 = string
export type CapabilityName7 = string
export type Chunk = string
export type Encoding = ("utf8" | "utf8_replacement")
export type Index = number
export type Redacted1 = boolean
export type RuntimeCommandId1 = string
export type Stream = ("stdout" | "stderr")
export type TaskId8 = (string | null)
export type Truncated = boolean
export type RunId15 = string
export type SchemaVersion15 = 1
export type Sequence15 = string
export type Type15 = "runtime.output"
export type ConversationId16 = string
export type EventId16 = string
export type OccurredAt16 = string
export type Attempt6 = number
export type CapabilityCallId7 = string
export type CapabilityName8 = string
export type DurationMs = number
export type ExitCode = (number | null)
export type Outcome1 = ("completed" | "failed" | "timeout" | "cancelled")
export type Redacted2 = boolean
export type RuntimeCommandId2 = string
export type StderrObservedBytes = number
export type StderrPublishedBytes = number
export type StderrTruncated = boolean
export type StdoutObservedBytes = number
export type StdoutPublishedBytes = number
export type StdoutTruncated = boolean
export type TaskId9 = (string | null)
export type RunId16 = string
export type SchemaVersion16 = 1
export type Sequence16 = string
export type Type16 = "runtime.command_completed"
export type ConversationId17 = string
export type EventId17 = string
export type OccurredAt17 = string
export type ArtifactId = string
export type Kind = string
export type MediaType = (string | null)
export type Sha256 = string
export type SizeBytes = number
export type RunId17 = string
export type SchemaVersion17 = 1
export type Sequence17 = string
export type Type17 = "artifact.created"
export type ConversationId18 = string
export type EventId18 = string
export type OccurredAt18 = string
export type Prompt = string
export type ReviewId = string
export type Status3 = "pending"
export type TaskId10 = (string | null)
export type RunId18 = string
export type SchemaVersion18 = 1
export type Sequence18 = string
export type Type18 = "review.requested"
export type ConversationId19 = string
export type EventId19 = string
export type OccurredAt19 = string
export type Comment = (string | null)
export type ReviewDecision = ("approve" | "reject")
export type ReviewId1 = string
export type Status4 = ("approved" | "rejected" | "cancelled")
export type RunId19 = string
export type SchemaVersion19 = 1
export type Sequence19 = string
export type Type19 = "review.resolved"
export type ConversationId20 = string
export type EventId20 = string
export type OccurredAt20 = string
export type BudgetKind = ("turn" | "wall_time" | "model_call" | "capability_call" | "retry")
export type Limit = number
export type Unit = string
export type Used = number
export type RunId20 = string
export type SchemaVersion20 = 1
export type Sequence20 = string
export type Type20 = "budget.exhausted"
export type ConversationId21 = string
export type EventId21 = string
export type OccurredAt21 = string
export type Reason1 = (string | null)
export type Status5 = "cancelling"
export type RunId21 = string
export type SchemaVersion21 = 1
export type Sequence21 = string
export type Type21 = "run.cancel_requested"
export type ConversationId22 = string
export type EventId22 = string
export type OccurredAt22 = string
export type Reason2 = string
export type Resumable = true
export type ReviewId2 = (string | null)
export type Status6 = "review_required"
export type RunId22 = string
export type SchemaVersion22 = 1
export type Sequence22 = string
export type Type22 = "run.interrupted"
export type ConversationId23 = string
export type EventId23 = string
export type OccurredAt23 = string
/**
 * @maxItems 100
 */
export type ArtifactIds1 = string[]
export type FinalMessageId = (string | null)
export type Status7 = "completed"
export type RunId23 = string
export type SchemaVersion23 = 1
export type Sequence23 = string
export type Type23 = "run.completed"
export type ConversationId24 = string
export type EventId24 = string
export type OccurredAt24 = string
export type ErrorCode2 = string
export type ErrorSummary2 = string
export type Retryable1 = boolean
export type Status8 = "failed"
export type RunId24 = string
export type SchemaVersion24 = 1
export type Sequence24 = string
export type Type24 = "run.failed"
export type ConversationId25 = string
export type EventId25 = string
export type OccurredAt25 = string
export type Reason3 = (string | null)
export type Status9 = "cancelled"
export type RunId25 = string
export type SchemaVersion25 = 1
export type Sequence25 = string
export type Type25 = "run.cancelled"
export type TransientEvent = AssistantDeltaEvent
export type ConversationId26 = string
export type OccurredAt26 = string
export type Delta = string
export type Index1 = number
export type MessageId1 = string
export type RunId26 = string
export type SchemaVersion26 = 1
export type Type26 = "assistant.delta"

export interface RunCreatedEvent {
conversation_id: ConversationId
event_id: EventId
occurred_at: OccurredAt
payload: RunCreatedPayload
run_id: RunId
schema_version: SchemaVersion
sequence: Sequence
type: Type
}
export interface RunCreatedPayload {
status?: Status
}
export interface RunStartedEvent {
conversation_id: ConversationId1
event_id: EventId1
occurred_at: OccurredAt1
payload: RunStartedPayload
run_id: RunId1
schema_version: SchemaVersion1
sequence: Sequence1
type: Type1
}
export interface RunStartedPayload {
status?: Status1
}
export interface AgentTurnStartedEvent {
conversation_id: ConversationId2
event_id: EventId2
occurred_at: OccurredAt2
payload: AgentTurnStartedPayload
run_id: RunId2
schema_version: SchemaVersion2
sequence: Sequence2
type: Type2
}
export interface AgentTurnStartedPayload {
remaining_turns: RemainingTurns
turn_index: TurnIndex
}
export interface MessageCompletedEvent {
conversation_id: ConversationId3
event_id: EventId3
occurred_at: OccurredAt3
payload: MessageCompletedPayload
run_id: RunId3
schema_version: SchemaVersion3
sequence: Sequence3
type: Type3
}
export interface MessageCompletedPayload {
content: Content
content_artifact_id?: ContentArtifactId
has_tool_calls?: HasToolCalls
message_id: MessageId
role: MessageRole
stop_reason?: StopReason
turn_index?: TurnIndex1
}
export interface TaskCreatedEvent {
conversation_id: ConversationId4
event_id: EventId4
occurred_at: OccurredAt4
payload: TaskCreatedPayload
run_id: RunId4
schema_version: SchemaVersion4
sequence: Sequence4
type: Type4
}
export interface TaskCreatedPayload {
capability_name?: CapabilityName
description?: Description
status?: Status2
task_id: TaskId
title: Title
}
export interface TaskUpdatedEvent {
conversation_id: ConversationId5
event_id: EventId5
occurred_at: OccurredAt5
payload: TaskUpdatedPayload
run_id: RunId5
schema_version: SchemaVersion5
sequence: Sequence5
type: Type5
}
export interface TaskUpdatedPayload {
status: TaskStatus
summary?: Summary
task_id: TaskId1
}
export interface SkillLoadStartedEvent {
conversation_id: ConversationId6
event_id: EventId6
occurred_at: OccurredAt6
payload: SkillLoadStartedPayload
run_id: RunId6
schema_version: SchemaVersion6
sequence: Sequence6
type: Type6
}
export interface SkillLoadStartedPayload {
purpose: Purpose
resource_kind: ResourceKind
resource_name?: ResourceName
skill_load_id: SkillLoadId
skill_name: SkillName
}
export interface SkillLoadCompletedEvent {
conversation_id: ConversationId7
event_id: EventId7
occurred_at: OccurredAt7
payload: SkillLoadCompletedPayload
run_id: RunId7
schema_version: SchemaVersion7
sequence: Sequence7
type: Type7
}
export interface SkillLoadCompletedPayload {
content_bytes: ContentBytes
outcome: Outcome
purpose: Purpose1
resource_kind: ResourceKind1
resource_name?: ResourceName1
skill_load_id: SkillLoadId1
skill_name: SkillName1
}
export interface SkillLoadFailedEvent {
conversation_id: ConversationId8
event_id: EventId8
occurred_at: OccurredAt8
payload: SkillLoadFailedPayload
run_id: RunId8
schema_version: SchemaVersion8
sequence: Sequence8
type: Type8
}
export interface SkillLoadFailedPayload {
error_code: ErrorCode
error_summary: ErrorSummary
purpose: Purpose2
resource_kind: ResourceKind2
resource_name?: ResourceName2
skill_load_id: SkillLoadId2
skill_name: SkillName2
}
export interface CapabilityStartedEvent {
conversation_id: ConversationId9
event_id: EventId9
occurred_at: OccurredAt9
payload: CapabilityStartedPayload
run_id: RunId9
schema_version: SchemaVersion9
sequence: Sequence9
type: Type9
}
export interface CapabilityStartedPayload {
attempt?: Attempt
capability_call_id: CapabilityCallId
capability_name: CapabilityName1
task_id?: TaskId2
}
export interface CapabilityCompletedEvent {
conversation_id: ConversationId10
event_id: EventId10
occurred_at: OccurredAt10
payload: CapabilityCompletedPayload
run_id: RunId10
schema_version: SchemaVersion10
sequence: Sequence10
type: Type10
}
export interface CapabilityCompletedPayload {
artifact_ids?: ArtifactIds
attempt?: Attempt1
capability_call_id: CapabilityCallId1
capability_name: CapabilityName2
result_status?: ResultStatus
summary?: Summary1
task_id?: TaskId3
}
export interface CapabilityFailedEvent {
conversation_id: ConversationId11
event_id: EventId11
occurred_at: OccurredAt11
payload: CapabilityFailedPayload
run_id: RunId11
schema_version: SchemaVersion11
sequence: Sequence11
type: Type11
}
export interface CapabilityFailedPayload {
attempt?: Attempt2
capability_call_id: CapabilityCallId2
capability_name: CapabilityName3
error_code: ErrorCode1
error_summary: ErrorSummary1
retryable: Retryable
task_id?: TaskId4
}
export interface CapabilityRetryingEvent {
conversation_id: ConversationId12
event_id: EventId12
occurred_at: OccurredAt12
payload: CapabilityRetryingPayload
run_id: RunId12
schema_version: SchemaVersion12
sequence: Sequence12
type: Type12
}
export interface CapabilityRetryingPayload {
capability_call_id: CapabilityCallId3
capability_name: CapabilityName4
delay_seconds: DelaySeconds
next_attempt: NextAttempt
reason: Reason
task_id?: TaskId5
}
export interface CapabilityProgressEvent {
conversation_id: ConversationId13
event_id: EventId13
occurred_at: OccurredAt13
payload: CapabilityProgressPayload
run_id: RunId13
schema_version: SchemaVersion13
sequence: Sequence13
type: Type13
}
export interface CapabilityProgressPayload {
attempt?: Attempt3
capability_call_id: CapabilityCallId4
capability_name: CapabilityName5
current: Current
message: Message
stage?: Stage
task_id?: TaskId6
total?: Total
}
export interface RuntimeCommandStartedEvent {
conversation_id: ConversationId14
event_id: EventId14
occurred_at: OccurredAt14
payload: RuntimeCommandStartedPayload
run_id: RunId14
schema_version: SchemaVersion14
sequence: Sequence14
type: Type14
}
export interface RuntimeCommandStartedPayload {
attempt?: Attempt4
backend: Backend
capability_call_id: CapabilityCallId5
capability_name: CapabilityName6
code?: Code
command: Command
command_truncated?: CommandTruncated
redacted?: Redacted
runtime_command_id: RuntimeCommandId
task_id?: TaskId7
workdir: Workdir
}
export interface RuntimeOutputEvent {
conversation_id: ConversationId15
event_id: EventId15
occurred_at: OccurredAt15
payload: RuntimeOutputPayload
run_id: RunId15
schema_version: SchemaVersion15
sequence: Sequence15
type: Type15
}
export interface RuntimeOutputPayload {
attempt?: Attempt5
capability_call_id: CapabilityCallId6
capability_name: CapabilityName7
chunk: Chunk
encoding?: Encoding
index: Index
redacted?: Redacted1
runtime_command_id: RuntimeCommandId1
stream: Stream
task_id?: TaskId8
truncated?: Truncated
}
export interface RuntimeCommandCompletedEvent {
conversation_id: ConversationId16
event_id: EventId16
occurred_at: OccurredAt16
payload: RuntimeCommandCompletedPayload
run_id: RunId16
schema_version: SchemaVersion16
sequence: Sequence16
type: Type16
}
export interface RuntimeCommandCompletedPayload {
attempt?: Attempt6
capability_call_id: CapabilityCallId7
capability_name: CapabilityName8
duration_ms: DurationMs
exit_code?: ExitCode
outcome: Outcome1
redacted?: Redacted2
runtime_command_id: RuntimeCommandId2
stderr_observed_bytes: StderrObservedBytes
stderr_published_bytes: StderrPublishedBytes
stderr_truncated?: StderrTruncated
stdout_observed_bytes: StdoutObservedBytes
stdout_published_bytes: StdoutPublishedBytes
stdout_truncated?: StdoutTruncated
task_id?: TaskId9
}
export interface ArtifactCreatedEvent {
conversation_id: ConversationId17
event_id: EventId17
occurred_at: OccurredAt17
payload: ArtifactCreatedPayload
run_id: RunId17
schema_version: SchemaVersion17
sequence: Sequence17
type: Type17
}
export interface ArtifactCreatedPayload {
artifact_id: ArtifactId
kind: Kind
media_type?: MediaType
sha256: Sha256
size_bytes: SizeBytes
}
export interface ReviewRequestedEvent {
conversation_id: ConversationId18
event_id: EventId18
occurred_at: OccurredAt18
payload: ReviewRequestedPayload
run_id: RunId18
schema_version: SchemaVersion18
sequence: Sequence18
type: Type18
}
export interface ReviewRequestedPayload {
prompt: Prompt
review_id: ReviewId
status?: Status3
task_id?: TaskId10
}
export interface ReviewResolvedEvent {
conversation_id: ConversationId19
event_id: EventId19
occurred_at: OccurredAt19
payload: ReviewResolvedPayload
run_id: RunId19
schema_version: SchemaVersion19
sequence: Sequence19
type: Type19
}
export interface ReviewResolvedPayload {
comment?: Comment
decision?: (ReviewDecision | null)
review_id: ReviewId1
status: Status4
}
export interface BudgetExhaustedEvent {
conversation_id: ConversationId20
event_id: EventId20
occurred_at: OccurredAt20
payload: BudgetExhaustedPayload
run_id: RunId20
schema_version: SchemaVersion20
sequence: Sequence20
type: Type20
}
export interface BudgetExhaustedPayload {
budget: BudgetKind
limit: Limit
unit: Unit
used: Used
}
export interface RunCancelRequestedEvent {
conversation_id: ConversationId21
event_id: EventId21
occurred_at: OccurredAt21
payload: RunCancelRequestedPayload
run_id: RunId21
schema_version: SchemaVersion21
sequence: Sequence21
type: Type21
}
export interface RunCancelRequestedPayload {
reason?: Reason1
status?: Status5
}
export interface RunInterruptedEvent {
conversation_id: ConversationId22
event_id: EventId22
occurred_at: OccurredAt22
payload: RunInterruptedPayload
run_id: RunId22
schema_version: SchemaVersion22
sequence: Sequence22
type: Type22
}
export interface RunInterruptedPayload {
reason: Reason2
resumable?: Resumable
review_id?: ReviewId2
status?: Status6
}
export interface RunCompletedEvent {
conversation_id: ConversationId23
event_id: EventId23
occurred_at: OccurredAt23
payload: RunCompletedPayload
run_id: RunId23
schema_version: SchemaVersion23
sequence: Sequence23
type: Type23
}
export interface RunCompletedPayload {
artifact_ids?: ArtifactIds1
final_message_id?: FinalMessageId
status?: Status7
}
export interface RunFailedEvent {
conversation_id: ConversationId24
event_id: EventId24
occurred_at: OccurredAt24
payload: RunFailedPayload
run_id: RunId24
schema_version: SchemaVersion24
sequence: Sequence24
type: Type24
}
export interface RunFailedPayload {
error_code: ErrorCode2
error_summary: ErrorSummary2
retryable?: Retryable1
status?: Status8
}
export interface RunCancelledEvent {
conversation_id: ConversationId25
event_id: EventId25
occurred_at: OccurredAt25
payload: RunCancelledPayload
run_id: RunId25
schema_version: SchemaVersion25
sequence: Sequence25
type: Type25
}
export interface RunCancelledPayload {
reason?: Reason3
status?: Status9
}
export interface AssistantDeltaEvent {
conversation_id: ConversationId26
occurred_at: OccurredAt26
payload: AssistantDeltaPayload
run_id: RunId26
schema_version: SchemaVersion26
type: Type26
}
export interface AssistantDeltaPayload {
delta: Delta
index: Index1
message_id: MessageId1
}
