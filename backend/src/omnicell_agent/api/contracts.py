"""REST API v1 的公共 Pydantic 契约。

这些 DTO 是持久化模型的显式投影，不暴露数据库结构、
workspace URI、宿主路径、checkpoint 内容或 provider 配置。
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from omnicell_agent.runs.events import DecimalCursor, PersistedEvent
from omnicell_agent.runs.status import ReviewDecision, ReviewStatus, RunStatus

API_SCHEMA_VERSION = 1

BoundedIdList = Annotated[list[UUID], Field(max_length=100)]
MetadataValue = str | int | float | bool | None


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedApiModel(ApiModel):
    schema_version: Literal[API_SCHEMA_VERSION] = API_SCHEMA_VERSION


class HealthComponentStatus(StrEnum):
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


class HealthComponentsRead(ApiModel):
    api: HealthComponentStatus
    postgres_application: HealthComponentStatus
    postgres_checkpointer: HealthComponentStatus
    execution_backend: HealthComponentStatus


class LivenessResponse(VersionedApiModel):
    status: Literal["alive"] = "alive"


class ReadinessResponse(VersionedApiModel):
    ready: bool
    components: HealthComponentsRead


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class PageInfo(ApiModel):
    next_cursor: str | None = Field(default=None, min_length=1, max_length=2_048)
    has_more: bool


class ConversationCreateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)


class ConversationRead(VersionedApiModel):
    conversation_id: UUID
    title: str | None = Field(default=None, max_length=300)
    status: ConversationStatus
    dataset_artifact_id: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConversationListRequest(ApiModel):
    cursor: str | None = Field(default=None, min_length=1, max_length=2_048)
    limit: int = Field(default=50, ge=1, le=100)
    status: ConversationStatus | None = None


class ConversationListResponse(VersionedApiModel):
    items: list[ConversationRead] = Field(max_length=100)
    page: PageInfo


class RunCreateRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=20_000)
    input_artifact_ids: BoundedIdList = Field(default_factory=list)
    request_key: str | None = Field(default=None, min_length=1, max_length=255)


class RunRead(VersionedApiModel):
    run_id: UUID
    conversation_id: UUID
    status: RunStatus
    last_sequence: DecimalCursor
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    error_summary: str | None = Field(default=None, max_length=2_000)


class RunCreateResponse(VersionedApiModel):
    run: RunRead


class RunHistoryRequest(ApiModel):
    cursor: str | None = Field(default=None, min_length=1, max_length=2_048)
    limit: int = Field(default=50, ge=1, le=100)


class RunHistoryResponse(VersionedApiModel):
    conversation_id: UUID
    order: Literal["newest_first"] = Field(
        description=(
            "items 按 created_at 降序排列；时间相同时按 run_id 降序排列。"
            "分页 cursor 延续相同顺序。"
        )
    )
    items: list[RunRead] = Field(
        max_length=100,
        description="当前分页内从最新到最旧的 run。",
    )
    page: PageInfo


class EventReplayRequest(ApiModel):
    after_sequence: DecimalCursor = "0"
    limit: int = Field(default=200, ge=1, le=500)


class EventReplayResponse(VersionedApiModel):
    conversation_id: UUID
    run_id: UUID
    events: list[PersistedEvent] = Field(max_length=500)
    next_sequence: DecimalCursor
    has_more: bool


class RunCancelRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=2_000)


class RunCancelResponse(VersionedApiModel):
    run: RunRead
    accepted: bool


class RunResumeRequest(ApiModel):
    review_id: UUID | None = None


class RunResumeResponse(VersionedApiModel):
    run: RunRead
    accepted: bool


class ReviewRead(VersionedApiModel):
    review_id: UUID
    conversation_id: UUID
    run_id: UUID
    task_id: UUID | None = None
    status: ReviewStatus
    prompt: str = Field(min_length=1, max_length=10_000)
    decision: ReviewDecision | None = None
    comment: str | None = Field(default=None, max_length=5_000)
    requested_at: AwareDatetime
    resolved_at: AwareDatetime | None = None


class ReviewListRequest(ApiModel):
    run_id: UUID | None = None
    status: ReviewStatus | None = None
    cursor: str | None = Field(default=None, min_length=1, max_length=2_048)
    limit: int = Field(default=50, ge=1, le=100)


class ReviewListResponse(VersionedApiModel):
    conversation_id: UUID
    items: list[ReviewRead] = Field(max_length=100)
    page: PageInfo


class ReviewDecisionRequest(ApiModel):
    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=5_000)


class ReviewDecisionResponse(VersionedApiModel):
    review: ReviewRead
    run: RunRead


class ArtifactRead(VersionedApiModel):
    artifact_id: UUID
    conversation_id: UUID
    run_id: UUID | None = None
    source_event_id: UUID | None = None
    kind: str = Field(min_length=1, max_length=128)
    media_type: str | None = Field(default=None, max_length=255)
    size_bytes: int = Field(ge=0, le=1 << 50)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, MetadataValue] = Field(default_factory=dict, max_length=50)
    created_at: AwareDatetime

    @field_validator("metadata")
    @classmethod
    def _public_metadata_only(
        cls,
        value: dict[str, MetadataValue],
    ) -> dict[str, MetadataValue]:
        forbidden = {
            "apikey",
            "checkpoint",
            "dsn",
            "hostpath",
            "ormrow",
            "password",
            "providersecret",
            "rawcheckpoint",
            "secret",
            "uri",
            "workspaceuri",
        }
        for key, item in value.items():
            if not key or len(key) > 128:
                raise ValueError(
                    "artifact metadata key 长度必须在 1 到 128 之间"
                )
            normalized_key = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            if normalized_key in forbidden:
                raise ValueError(f"artifact metadata 不允许内部字段：{key}")
            if isinstance(item, str) and len(item) > 2_000:
                raise ValueError("artifact metadata 字符串值超过 2000 字符")
            if (
                isinstance(item, int)
                and not isinstance(item, bool)
                and abs(item) > 10**18
            ):
                raise ValueError("artifact metadata 整数值超出公共契约范围")
            if isinstance(item, float) and (
                not math.isfinite(item) or abs(item) > 10**18
            ):
                raise ValueError("artifact metadata 浮点值超出公共契约范围")
        return value


class ArtifactListRequest(ApiModel):
    run_id: UUID | None = None
    kind: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=2_048)
    limit: int = Field(default=50, ge=1, le=100)


class ArtifactListResponse(VersionedApiModel):
    conversation_id: UUID
    items: list[ArtifactRead] = Field(max_length=100)
    page: PageInfo


class ErrorDetail(ApiModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)
    field: str | None = Field(default=None, min_length=1, max_length=256)


class ErrorInfo(ApiModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False
    details: list[ErrorDetail] = Field(default_factory=list, max_length=50)


class ErrorEnvelope(VersionedApiModel):
    request_id: UUID
    error: ErrorInfo


__all__ = [
    "API_SCHEMA_VERSION",
    "ApiModel",
    "ArtifactListRequest",
    "ArtifactListResponse",
    "ArtifactRead",
    "ConversationCreateRequest",
    "ConversationListRequest",
    "ConversationListResponse",
    "ConversationRead",
    "ConversationStatus",
    "ErrorDetail",
    "ErrorEnvelope",
    "ErrorInfo",
    "EventReplayRequest",
    "EventReplayResponse",
    "HealthComponentsRead",
    "HealthComponentStatus",
    "LivenessResponse",
    "PageInfo",
    "ReadinessResponse",
    "ReviewDecisionRequest",
    "ReviewDecisionResponse",
    "ReviewListRequest",
    "ReviewListResponse",
    "ReviewRead",
    "RunCancelRequest",
    "RunCancelResponse",
    "RunCreateRequest",
    "RunCreateResponse",
    "RunHistoryRequest",
    "RunHistoryResponse",
    "RunRead",
    "RunResumeRequest",
    "RunResumeResponse",
    "VersionedApiModel",
]
