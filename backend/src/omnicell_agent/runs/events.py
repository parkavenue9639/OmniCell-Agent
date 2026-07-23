"""面向 API 与 frontend 的版本化事件契约。

本模块只表达稳定、可重放的产品事实和有界的瞬态通知。
数据库行、LangGraph checkpoint、宿主路径、provider 配置与
大型执行输出均不属于该契约。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .status import ReviewDecision, ReviewStatus, RunStatus, TaskStatus

SCHEMA_VERSION = 1
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
MAX_SEQUENCE_VALUE = (1 << 63) - 1


def _decimal_string(value: object, *, allow_zero: bool) -> str:
    if isinstance(value, bool):
        raise ValueError("sequence 必须是十进制整数")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("sequence 必须是十进制字符串")
    if len(value) > 1 and value.startswith("0"):
        raise ValueError("sequence 不允许前导零")
    number = int(value)
    minimum = 0 if allow_zero else 1
    if number < minimum or number > MAX_SEQUENCE_VALUE:
        raise ValueError("sequence 超出 PostgreSQL BIGINT 范围")
    return value


def _positive_decimal_string(value: object) -> str:
    return _decimal_string(value, allow_zero=False)


def _cursor_decimal_string(value: object) -> str:
    return _decimal_string(value, allow_zero=True)


# 公共 sequence 始终编码为字符串，避免 JavaScript Number 精度丢失。
DecimalSequence: TypeAlias = Annotated[
    str,
    BeforeValidator(_positive_decimal_string),
    Field(pattern=r"^[1-9][0-9]{0,18}$", max_length=19),
]
DecimalCursor: TypeAlias = Annotated[
    str,
    BeforeValidator(_cursor_decimal_string),
    Field(pattern=r"^(0|[1-9][0-9]{0,18})$", max_length=19),
]


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    AGENT_TURN_STARTED = "agent.turn_started"
    MESSAGE_COMPLETED = "message.completed"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    SKILL_LOAD_STARTED = "skill.load_started"
    SKILL_LOAD_COMPLETED = "skill.load_completed"
    SKILL_LOAD_FAILED = "skill.load_failed"
    CAPABILITY_STARTED = "capability.started"
    CAPABILITY_COMPLETED = "capability.completed"
    CAPABILITY_FAILED = "capability.failed"
    CAPABILITY_RETRYING = "capability.retrying"
    RUNTIME_COMMAND_STARTED = "runtime.command_started"
    RUNTIME_OUTPUT = "runtime.output"
    RUNTIME_COMMAND_COMPLETED = "runtime.command_completed"
    ARTIFACT_CREATED = "artifact.created"
    REVIEW_REQUESTED = "review.requested"
    REVIEW_RESOLVED = "review.resolved"
    BUDGET_EXHAUSTED = "budget.exhausted"
    RUN_CANCEL_REQUESTED = "run.cancel_requested"
    RUN_INTERRUPTED = "run.interrupted"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    ASSISTANT_DELTA = "assistant.delta"
    CAPABILITY_PROGRESS = "capability.progress"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class BudgetKind(StrEnum):
    TURN = "turn"
    WALL_TIME = "wall_time"
    MODEL_CALL = "model_call"
    CAPABILITY_CALL = "capability_call"
    RETRY = "retry"


class EventPayload(BaseModel):
    """所有事件 payload 的封闭基类。

    序列化后的 payload 还必须满足统一总大小上限。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _bounded_payload(self) -> "EventPayload":
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("event payload 超过 64 KiB")
        return self


class RunCreatedPayload(EventPayload):
    status: Literal[RunStatus.PENDING] = RunStatus.PENDING


class RunStartedPayload(EventPayload):
    status: Literal[RunStatus.RUNNING] = RunStatus.RUNNING


class AgentTurnStartedPayload(EventPayload):
    turn_index: int = Field(ge=1, le=10_000)
    remaining_turns: int = Field(ge=0, le=10_000)


class MessageCompletedPayload(EventPayload):
    message_id: UUID
    role: MessageRole
    content: str = Field(max_length=20_000)
    turn_index: int | None = Field(default=None, ge=1, le=10_000)
    has_tool_calls: bool = False
    stop_reason: str | None = Field(default=None, min_length=1, max_length=128)
    content_artifact_id: UUID | None = None


class TaskCreatedPayload(EventPayload):
    task_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2_000)
    status: Literal[TaskStatus.PENDING] = TaskStatus.PENDING
    capability_name: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class TaskUpdatedPayload(EventPayload):
    task_id: UUID
    status: TaskStatus
    summary: str | None = Field(default=None, max_length=2_000)


class CapabilityStartedPayload(EventPayload):
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)


class SkillLoadStartedPayload(EventPayload):
    skill_load_id: UUID
    skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    resource_kind: Literal["body", "reference", "example"]
    resource_name: str | None = Field(default=None, max_length=256)
    purpose: Literal[
        "domain_method",
        "validation_rules",
        "workflow_guidance",
        "reference_lookup",
        "example_lookup",
    ]


class SkillLoadCompletedPayload(EventPayload):
    skill_load_id: UUID
    skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    resource_kind: Literal["body", "reference", "example"]
    resource_name: str | None = Field(default=None, max_length=256)
    purpose: Literal[
        "domain_method",
        "validation_rules",
        "workflow_guidance",
        "reference_lookup",
        "example_lookup",
    ]
    outcome: Literal["loaded", "already_loaded"]
    content_bytes: int = Field(ge=0, le=64 * 1024)


class SkillLoadFailedPayload(EventPayload):
    skill_load_id: UUID
    skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    resource_kind: Literal["body", "reference", "example"]
    resource_name: str | None = Field(default=None, max_length=256)
    purpose: Literal[
        "domain_method",
        "validation_rules",
        "workflow_guidance",
        "reference_lookup",
        "example_lookup",
    ]
    error_code: Literal["skill_resource_unavailable"]
    error_summary: str = Field(min_length=1, max_length=500)


class CapabilityCompletedPayload(EventPayload):
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    result_status: Literal["completed", "aborted"] | None = None
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=100)
    summary: str | None = Field(default=None, max_length=2_000)


class CapabilityFailedPayload(EventPayload):
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    error_code: str = Field(min_length=1, max_length=128)
    error_summary: str = Field(min_length=1, max_length=2_000)
    retryable: bool


class CapabilityRetryingPayload(EventPayload):
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    next_attempt: int = Field(ge=2, le=100)
    delay_seconds: float = Field(ge=0, le=3_600)
    reason: str = Field(min_length=1, max_length=1_000)


class CapabilityProgressPayload(EventPayload):
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    stage: Literal["isolated_execution"] = "isolated_execution"
    current: int = Field(ge=1, le=1_000_000_000)
    total: int | None = Field(default=None, ge=1, le=1_000_000_000)
    message: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _current_within_total(self) -> "CapabilityProgressPayload":
        if self.total is not None and self.current > self.total:
            raise ValueError("capability progress current 不能大于 total")
        return self


class RuntimeCommandStartedPayload(EventPayload):
    runtime_command_id: UUID
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    backend: str = Field(min_length=1, max_length=64)
    command: list[str] = Field(min_length=1, max_length=16)
    code: str | None = Field(default=None, max_length=24_000)
    workdir: str = Field(min_length=1, max_length=512)
    command_truncated: bool = False
    redacted: bool = False

    @field_validator("command")
    @classmethod
    def _bounded_command(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 8_000 for item in value):
            raise ValueError("runtime command token 为空或超过上限")
        if sum(len(item) for item in value) > 24_000:
            raise ValueError("runtime command 总长度超过上限")
        return value

    @field_validator("workdir")
    @classmethod
    def _container_logical_workdir(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith(("/Users/", "/home/")):
            raise ValueError("runtime workdir 必须是容器逻辑绝对路径")
        return value


class RuntimeOutputPayload(EventPayload):
    runtime_command_id: UUID
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    stream: Literal["stdout", "stderr"]
    index: int = Field(ge=0, le=1_000_000)
    chunk: str = Field(min_length=1, max_length=8_000)
    encoding: Literal["utf8", "utf8_replacement"] = "utf8"
    truncated: bool = False
    redacted: bool = False


class RuntimeCommandCompletedPayload(EventPayload):
    runtime_command_id: UUID
    capability_call_id: UUID
    capability_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    task_id: UUID | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    outcome: Literal["completed", "failed", "timeout", "cancelled"]
    exit_code: int | None = Field(default=None, ge=-255, le=255)
    duration_ms: int = Field(ge=0, le=86_400_000)
    stdout_observed_bytes: int = Field(ge=0, le=1 << 30)
    stdout_published_bytes: int = Field(ge=0, le=1 << 30)
    stderr_observed_bytes: int = Field(ge=0, le=1 << 30)
    stderr_published_bytes: int = Field(ge=0, le=1 << 30)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    redacted: bool = False


class ArtifactCreatedPayload(EventPayload):
    artifact_id: UUID
    kind: str = Field(min_length=1, max_length=128)
    media_type: str | None = Field(default=None, max_length=255)
    size_bytes: int = Field(ge=0, le=1 << 50)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewRequestedPayload(EventPayload):
    review_id: UUID
    task_id: UUID | None = None
    status: Literal[ReviewStatus.PENDING] = ReviewStatus.PENDING
    prompt: str = Field(min_length=1, max_length=10_000)


class ReviewResolvedPayload(EventPayload):
    review_id: UUID
    status: Literal[
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
        ReviewStatus.CANCELLED,
    ]
    decision: ReviewDecision | None = None
    comment: str | None = Field(default=None, max_length=5_000)

    @model_validator(mode="after")
    def _decision_matches_status(self) -> "ReviewResolvedPayload":
        expected = {
            ReviewStatus.APPROVED: ReviewDecision.APPROVE,
            ReviewStatus.REJECTED: ReviewDecision.REJECT,
            ReviewStatus.CANCELLED: None,
        }[self.status]
        if self.decision is not expected:
            raise ValueError("review decision 与 status 不一致")
        return self


class BudgetExhaustedPayload(EventPayload):
    budget: BudgetKind
    limit: float = Field(ge=0, le=1_000_000_000)
    used: float = Field(ge=0, le=1_000_000_000)
    unit: str = Field(min_length=1, max_length=64)


class RunCancelRequestedPayload(EventPayload):
    status: Literal[RunStatus.CANCELLING] = RunStatus.CANCELLING
    reason: str | None = Field(default=None, max_length=2_000)


class RunInterruptedPayload(EventPayload):
    status: Literal[RunStatus.REVIEW_REQUIRED] = RunStatus.REVIEW_REQUIRED
    review_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2_000)
    resumable: Literal[True] = True


class RunCompletedPayload(EventPayload):
    status: Literal[RunStatus.COMPLETED] = RunStatus.COMPLETED
    final_message_id: UUID | None = None
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=100)


class RunFailedPayload(EventPayload):
    status: Literal[RunStatus.FAILED] = RunStatus.FAILED
    error_code: str = Field(min_length=1, max_length=128)
    error_summary: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class RunCancelledPayload(EventPayload):
    status: Literal[RunStatus.CANCELLED] = RunStatus.CANCELLED
    reason: str | None = Field(default=None, max_length=2_000)


class EventEnvelopeBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # 公共 JSON Schema 描述的是已经序列化的 wire envelope；带默认值的
        # schema_version/type 在 wire 上仍然必须存在。
        json_schema_serialization_defaults_required=True,
    )

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    conversation_id: UUID
    run_id: UUID
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at 必须包含时区")
        return value


class PersistedEventEnvelope(EventEnvelopeBase):
    event_id: UUID
    sequence: DecimalSequence


class RunCreatedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_CREATED] = EventType.RUN_CREATED
    payload: RunCreatedPayload


class RunStartedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_STARTED] = EventType.RUN_STARTED
    payload: RunStartedPayload


class AgentTurnStartedEvent(PersistedEventEnvelope):
    type: Literal[EventType.AGENT_TURN_STARTED] = EventType.AGENT_TURN_STARTED
    payload: AgentTurnStartedPayload


class MessageCompletedEvent(PersistedEventEnvelope):
    type: Literal[EventType.MESSAGE_COMPLETED] = EventType.MESSAGE_COMPLETED
    payload: MessageCompletedPayload


class TaskCreatedEvent(PersistedEventEnvelope):
    type: Literal[EventType.TASK_CREATED] = EventType.TASK_CREATED
    payload: TaskCreatedPayload


class TaskUpdatedEvent(PersistedEventEnvelope):
    type: Literal[EventType.TASK_UPDATED] = EventType.TASK_UPDATED
    payload: TaskUpdatedPayload


class SkillLoadStartedEvent(PersistedEventEnvelope):
    type: Literal[EventType.SKILL_LOAD_STARTED] = EventType.SKILL_LOAD_STARTED
    payload: SkillLoadStartedPayload


class SkillLoadCompletedEvent(PersistedEventEnvelope):
    type: Literal[EventType.SKILL_LOAD_COMPLETED] = EventType.SKILL_LOAD_COMPLETED
    payload: SkillLoadCompletedPayload


class SkillLoadFailedEvent(PersistedEventEnvelope):
    type: Literal[EventType.SKILL_LOAD_FAILED] = EventType.SKILL_LOAD_FAILED
    payload: SkillLoadFailedPayload


class CapabilityStartedEvent(PersistedEventEnvelope):
    type: Literal[EventType.CAPABILITY_STARTED] = EventType.CAPABILITY_STARTED
    payload: CapabilityStartedPayload


class CapabilityCompletedEvent(PersistedEventEnvelope):
    type: Literal[EventType.CAPABILITY_COMPLETED] = EventType.CAPABILITY_COMPLETED
    payload: CapabilityCompletedPayload


class CapabilityFailedEvent(PersistedEventEnvelope):
    type: Literal[EventType.CAPABILITY_FAILED] = EventType.CAPABILITY_FAILED
    payload: CapabilityFailedPayload


class CapabilityRetryingEvent(PersistedEventEnvelope):
    type: Literal[EventType.CAPABILITY_RETRYING] = EventType.CAPABILITY_RETRYING
    payload: CapabilityRetryingPayload


class CapabilityProgressEvent(PersistedEventEnvelope):
    type: Literal[EventType.CAPABILITY_PROGRESS] = EventType.CAPABILITY_PROGRESS
    payload: CapabilityProgressPayload


class RuntimeCommandStartedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUNTIME_COMMAND_STARTED] = EventType.RUNTIME_COMMAND_STARTED
    payload: RuntimeCommandStartedPayload


class RuntimeOutputEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUNTIME_OUTPUT] = EventType.RUNTIME_OUTPUT
    payload: RuntimeOutputPayload


class RuntimeCommandCompletedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUNTIME_COMMAND_COMPLETED] = EventType.RUNTIME_COMMAND_COMPLETED
    payload: RuntimeCommandCompletedPayload


class ArtifactCreatedEvent(PersistedEventEnvelope):
    type: Literal[EventType.ARTIFACT_CREATED] = EventType.ARTIFACT_CREATED
    payload: ArtifactCreatedPayload


class ReviewRequestedEvent(PersistedEventEnvelope):
    type: Literal[EventType.REVIEW_REQUESTED] = EventType.REVIEW_REQUESTED
    payload: ReviewRequestedPayload


class ReviewResolvedEvent(PersistedEventEnvelope):
    type: Literal[EventType.REVIEW_RESOLVED] = EventType.REVIEW_RESOLVED
    payload: ReviewResolvedPayload


class BudgetExhaustedEvent(PersistedEventEnvelope):
    type: Literal[EventType.BUDGET_EXHAUSTED] = EventType.BUDGET_EXHAUSTED
    payload: BudgetExhaustedPayload


class RunCancelRequestedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_CANCEL_REQUESTED] = EventType.RUN_CANCEL_REQUESTED
    payload: RunCancelRequestedPayload


class RunInterruptedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_INTERRUPTED] = EventType.RUN_INTERRUPTED
    payload: RunInterruptedPayload


class RunCompletedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_COMPLETED] = EventType.RUN_COMPLETED
    payload: RunCompletedPayload


class RunFailedEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_FAILED] = EventType.RUN_FAILED
    payload: RunFailedPayload


class RunCancelledEvent(PersistedEventEnvelope):
    type: Literal[EventType.RUN_CANCELLED] = EventType.RUN_CANCELLED
    payload: RunCancelledPayload


PersistedEvent: TypeAlias = Annotated[
    RunCreatedEvent
    | RunStartedEvent
    | AgentTurnStartedEvent
    | MessageCompletedEvent
    | TaskCreatedEvent
    | TaskUpdatedEvent
    | SkillLoadStartedEvent
    | SkillLoadCompletedEvent
    | SkillLoadFailedEvent
    | CapabilityStartedEvent
    | CapabilityCompletedEvent
    | CapabilityFailedEvent
    | CapabilityRetryingEvent
    | CapabilityProgressEvent
    | RuntimeCommandStartedEvent
    | RuntimeOutputEvent
    | RuntimeCommandCompletedEvent
    | ArtifactCreatedEvent
    | ReviewRequestedEvent
    | ReviewResolvedEvent
    | BudgetExhaustedEvent
    | RunCancelRequestedEvent
    | RunInterruptedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent,
    Field(discriminator="type"),
]
PERSISTED_EVENT_ADAPTER = TypeAdapter(PersistedEvent)


class AssistantDeltaPayload(EventPayload):
    message_id: UUID
    index: int = Field(ge=0, le=1_000_000_000)
    delta: str = Field(min_length=1, max_length=4_096)


class TransientEventEnvelope(EventEnvelopeBase):
    """不进入 Event Log 的通知；有意不定义 event_id 与 sequence。"""


class AssistantDeltaEvent(TransientEventEnvelope):
    type: Literal[EventType.ASSISTANT_DELTA] = EventType.ASSISTANT_DELTA
    payload: AssistantDeltaPayload


TransientEvent: TypeAlias = Annotated[
    AssistantDeltaEvent,
    Field(discriminator="type"),
]
TRANSIENT_EVENT_ADAPTER = TypeAdapter(TransientEvent)


def validate_persisted_event(value: object) -> PersistedEvent:
    return PERSISTED_EVENT_ADAPTER.validate_python(value)


def validate_transient_event(value: object) -> TransientEvent:
    return TRANSIENT_EVENT_ADAPTER.validate_python(value)


__all__ = [
    "AgentTurnStartedEvent",
    "AgentTurnStartedPayload",
    "ArtifactCreatedEvent",
    "ArtifactCreatedPayload",
    "AssistantDeltaEvent",
    "AssistantDeltaPayload",
    "BudgetExhaustedEvent",
    "BudgetExhaustedPayload",
    "BudgetKind",
    "CapabilityCompletedEvent",
    "CapabilityCompletedPayload",
    "CapabilityFailedEvent",
    "CapabilityFailedPayload",
    "CapabilityProgressEvent",
    "CapabilityProgressPayload",
    "CapabilityRetryingEvent",
    "CapabilityRetryingPayload",
    "CapabilityStartedEvent",
    "CapabilityStartedPayload",
    "DecimalCursor",
    "DecimalSequence",
    "EventEnvelopeBase",
    "EventPayload",
    "EventType",
    "MAX_EVENT_PAYLOAD_BYTES",
    "MAX_SEQUENCE_VALUE",
    "MessageCompletedEvent",
    "MessageCompletedPayload",
    "MessageRole",
    "PERSISTED_EVENT_ADAPTER",
    "PersistedEvent",
    "PersistedEventEnvelope",
    "ReviewRequestedEvent",
    "ReviewRequestedPayload",
    "ReviewResolvedEvent",
    "ReviewResolvedPayload",
    "RunCancelRequestedEvent",
    "RunCancelRequestedPayload",
    "RunCancelledEvent",
    "RunCancelledPayload",
    "RunCompletedEvent",
    "RunCompletedPayload",
    "RunCreatedEvent",
    "RunCreatedPayload",
    "RunFailedEvent",
    "RunFailedPayload",
    "RunInterruptedEvent",
    "RunInterruptedPayload",
    "RunStartedEvent",
    "RunStartedPayload",
    "SkillLoadCompletedEvent",
    "SkillLoadCompletedPayload",
    "SkillLoadFailedEvent",
    "SkillLoadFailedPayload",
    "SkillLoadStartedEvent",
    "SkillLoadStartedPayload",
    "RuntimeCommandCompletedEvent",
    "RuntimeCommandCompletedPayload",
    "RuntimeCommandStartedEvent",
    "RuntimeCommandStartedPayload",
    "RuntimeOutputEvent",
    "RuntimeOutputPayload",
    "SCHEMA_VERSION",
    "TRANSIENT_EVENT_ADAPTER",
    "TaskCreatedEvent",
    "TaskCreatedPayload",
    "TaskUpdatedEvent",
    "TaskUpdatedPayload",
    "TransientEvent",
    "TransientEventEnvelope",
    "validate_persisted_event",
    "validate_transient_event",
]
