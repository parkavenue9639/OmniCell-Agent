"""REST API v1 的公共 Pydantic 契约。

这些 DTO 是持久化模型的显式投影，不暴露数据库结构、
workspace URI、宿主路径、checkpoint 内容或 provider 配置。
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from omnicell_agent.memory.types import MEMORY_PROVIDER_CONSENT_VERSION
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


class MemoryKind(StrEnum):
    RESPONSE_PREFERENCE = "response_preference"
    PROFILE_FACT = "profile_fact"
    PROJECT_CONTEXT = "project_context"
    SCIENTIFIC_OBSERVATION = "scientific_observation"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REVOKED = "revoked"
    PURGED = "purged"


class MemoryRunMode(StrEnum):
    OFF = "off"
    DEFAULT = "default"
    SELECTED = "selected"


class MemorySourceKind(StrEnum):
    EXPLICIT = "explicit"
    PROPOSED = "proposed"
    CORRECTED = "corrected"


class MemorySelectionRef(ApiModel):
    item_id: UUID
    version_id: UUID


class RunCreateRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=20_000)
    input_artifact_ids: BoundedIdList = Field(default_factory=list)
    request_key: str | None = Field(default=None, min_length=1, max_length=255)
    memory_mode: MemoryRunMode = MemoryRunMode.OFF
    selected_memories: list[MemorySelectionRef] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def _selected_memory_matches_mode(self) -> "RunCreateRequest":
        if self.memory_mode is MemoryRunMode.SELECTED:
            if not self.selected_memories:
                raise ValueError("selected memory mode 必须包含至少一个精确版本引用")
        elif self.selected_memories:
            raise ValueError("只有 selected memory mode 可以携带 selected_memories")
        identities = {
            (item.item_id, item.version_id) for item in self.selected_memories
        }
        if len(identities) != len(self.selected_memories):
            raise ValueError("selected_memories 不允许重复")
        return self


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


class MemorySettingsRead(VersionedApiModel):
    scope_key: Literal["local-default"] = "local-default"
    use_memory: bool
    generate_candidates: bool
    enable_agent_tools: bool
    provider_consent_granted: bool
    provider_consent_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    provider_consented_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class MemorySettingsUpdateRequest(ApiModel):
    use_memory: bool | None = None
    generate_candidates: bool | None = None
    enable_agent_tools: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_setting(self) -> "MemorySettingsUpdateRequest":
        if (
            self.use_memory is None
            and self.generate_candidates is None
            and self.enable_agent_tools is None
        ):
            raise ValueError("至少需要更新一个 memory setting")
        return self


class MemoryProviderConsentRequest(ApiModel):
    decision: Literal["grant", "revoke"]
    statement_version: Literal[MEMORY_PROVIDER_CONSENT_VERSION]
    confirmed: Literal[True]


class MemorySourceRead(ApiModel):
    source_kind: MemorySourceKind
    conversation_id: UUID | None = None
    run_id: UUID | None = None
    message_ids: list[UUID] = Field(default_factory=list, max_length=32)


class MemoryCreateRequest(ApiModel):
    kind: MemoryKind
    stable_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    content: str = Field(min_length=1, max_length=8_000)
    dataset_scope: dict[str, str] | None = Field(
        default=None,
        max_length=16,
    )
    source_conversation_id: UUID | None = None
    source_run_id: UUID | None = None
    source_message_ids: list[UUID] = Field(default_factory=list, max_length=32)
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _scientific_memory_is_scoped(self) -> "MemoryCreateRequest":
        source_complete = bool(
            self.source_conversation_id
            and self.source_run_id
            and self.source_message_ids
        )
        source_partial = bool(
            self.source_conversation_id
            or self.source_run_id
            or self.source_message_ids
        )
        if source_partial and not source_complete:
            raise ValueError("memory source identity 必须完整提供")
        if self.kind is MemoryKind.SCIENTIFIC_OBSERVATION:
            if not self.dataset_scope or "artifact_id" not in self.dataset_scope:
                raise ValueError(
                    "scientific_observation 必须包含 artifact_id dataset scope"
                )
            if not source_complete:
                raise ValueError(
                    "scientific_observation 必须包含完整的 conversation/run/message 来源"
                )
        return self


class MemoryRead(VersionedApiModel):
    memory_id: UUID
    scope_key: Literal["local-default"] = "local-default"
    stable_key: str
    kind: MemoryKind
    status: MemoryStatus
    current_version: int | None = Field(default=None, ge=1)
    version_id: UUID | None = None
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    content: str | None = Field(default=None, max_length=8_000)
    dataset_scope: dict[str, str] | None = Field(
        default=None,
        max_length=16,
    )
    source: MemorySourceRead | None = None
    expires_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MemoryListResponse(VersionedApiModel):
    items: list[MemoryRead] = Field(max_length=100)
    page: PageInfo


class MemoryApproveRequest(ApiModel):
    expected_version: int = Field(ge=1, le=1_000_000_000)


class MemoryCorrectRequest(ApiModel):
    expected_version: int = Field(ge=1, le=1_000_000_000)
    content: str = Field(min_length=1, max_length=8_000)
    dataset_scope: dict[str, str] | None = Field(
        default=None,
        max_length=16,
    )
    source_message_ids: list[UUID] = Field(default_factory=list, max_length=32)


class MemoryForgetRequest(ApiModel):
    expected_version: int = Field(ge=1, le=1_000_000_000)
    confirmed: Literal[True]


class MemoryPurgeRequest(ApiModel):
    expected_version: int = Field(ge=1, le=1_000_000_000)
    confirmed: Literal[True]


class MemoryCommandResponse(VersionedApiModel):
    memory: MemoryRead


class RunMemoryInputRead(ApiModel):
    item_id: UUID
    version_id: UUID
    version_number: int = Field(ge=1, le=1_000_000_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: MemoryKind
    source_kind: MemorySourceKind
    selection_reason: Literal["default", "selected", "tool_search"]


class RunMemoryContextRead(VersionedApiModel):
    run_id: UUID
    snapshot_id: UUID | None = None
    scope_key: Literal["local-default"] = "local-default"
    mode: MemoryRunMode
    outcome: Literal["off", "pending", "loaded", "empty", "degraded"]
    inputs: list[RunMemoryInputRead] = Field(default_factory=list, max_length=32)
    degraded_code: str | None = Field(default=None, max_length=128)
    created_at: AwareDatetime | None = None


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
    "MemoryApproveRequest",
    "MemoryCommandResponse",
    "MemoryCorrectRequest",
    "MemoryCreateRequest",
    "MemoryForgetRequest",
    "MemoryKind",
    "MemoryListResponse",
    "MemoryProviderConsentRequest",
    "MemoryPurgeRequest",
    "MemoryRead",
    "MemoryRunMode",
    "MemorySelectionRef",
    "MemorySettingsRead",
    "MemorySettingsUpdateRequest",
    "MemorySourceKind",
    "MemorySourceRead",
    "MemoryStatus",
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
    "RunMemoryContextRead",
    "RunMemoryInputRead",
    "RunRead",
    "RunResumeRequest",
    "RunResumeResponse",
    "VersionedApiModel",
]
