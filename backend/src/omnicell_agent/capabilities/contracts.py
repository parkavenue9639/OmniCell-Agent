"""Agent-facing domain capability contracts.

These models deliberately project stable domain facts instead of exposing
LangGraph state, ORM rows, Docker identities, or large scientific objects.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CapabilityKind(StrEnum):
    READ_ONLY = "read_only"
    ATOMIC = "atomic"
    WORKFLOW = "workflow"


class CapabilityStatus(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    SKIPPED = "skipped"


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    kind: CapabilityKind
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(
        default="1.0",
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+$",
    )
    prompt_hint: str = Field(min_length=1, max_length=1_000)


class ArtifactRef(BaseModel):
    """A bounded reference to a file owned by one conversation workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    conversation_id: UUID
    kind: str = Field(min_length=1, max_length=128)
    uri: str = Field(min_length=1, max_length=2048)
    # `None` 与空 metadata 也是权威引用的一部分；字段本身必须显式出现，
    # 这样 Tool schema 不会诱导模型省略后再触发 canonical identity mismatch。
    media_type: str | None = Field(max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, Any] = Field()

    @field_validator("uri")
    @classmethod
    def _workspace_uri_only(cls, value: str) -> str:
        if not value.startswith("workspace://"):
            raise ValueError("artifact uri 必须使用 workspace:// scheme")
        return value

    @field_validator("metadata")
    @classmethod
    def _bounded_json_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("artifact metadata 必须可 JSON 序列化") from exc
        if len(encoded) > 64 * 1024:
            raise ValueError("artifact metadata 超过 64 KiB")
        return value


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species: str = Field(max_length=200)
    tissue: str = Field(max_length=200)
    disease_state: str = Field(max_length=500)
    goal_type: str = Field(max_length=200)


class AnalysisStepSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    step_type: str = Field(min_length=1, max_length=128)
    skill_name: str | None = Field(default=None, max_length=128)
    instruction: str | None = Field(default=None, max_length=2_000)
    status: Literal["completed", "pending"]


class SingleCellAnalysisRequest(CapabilityRequest):
    dataset: ArtifactRef
    goal: str = Field(min_length=1, max_length=20_000)


class SingleCellAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CapabilityStatus
    context: DatasetContext
    steps: list[AnalysisStepSummary] = Field(max_length=500)
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=500)
    marker_table: ArtifactRef | None = None
    diagnostic_summary: str | None = Field(default=None, max_length=2_000)


class InspectDatasetContextRequest(CapabilityRequest):
    dataset: ArtifactRef
    goal: str = Field(min_length=1, max_length=20_000)


class InspectDatasetContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: DatasetContext


class AtomicAnalysisRequest(CapabilityRequest):
    dataset: ArtifactRef


class AtomicAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CapabilityStatus
    operation: str = Field(min_length=1, max_length=128)
    source_dataset: ArtifactRef
    output_dataset: ArtifactRef | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=32)
    marker_table: ArtifactRef | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    diagnostic_summary: str | None = Field(default=None, max_length=2_000)

    @field_validator("metrics")
    @classmethod
    def _bounded_metrics(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("atomic analysis metrics 必须可 JSON 序列化") from exc
        if len(encoded) > 32 * 1024:
            raise ValueError("atomic analysis metrics 超过 32 KiB")
        return value


class DeepCellAnnotationRequest(CapabilityRequest):
    marker_table: ArtifactRef
    species: str = Field(min_length=1, max_length=200)
    tissue: str = Field(min_length=1, max_length=200)


class DeepCellAnnotationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[CapabilityStatus.COMPLETED] = CapabilityStatus.COMPLETED
    source_marker_table: ArtifactRef
    annotations: ArtifactRef
    report: ArtifactRef
    cluster_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)


class InspectMarkerContractRequest(CapabilityRequest):
    marker_table: ArtifactRef
    top_markers_per_cluster: int = Field(default=10, ge=1, le=20)
    max_clusters: int = Field(default=100, ge=1, le=500)


class MarkerClusterSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(max_length=256)
    marker_count: int = Field(ge=0)
    top_markers: list[Annotated[str, Field(max_length=256)]] = Field(max_length=20)


class InspectMarkerContractResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_marker_table: ArtifactRef
    marker_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    clusters: list[MarkerClusterSummary] = Field(max_length=500)
    truncated: bool

    @model_validator(mode="after")
    def _bounded_projection(self) -> "InspectMarkerContractResult":
        if len(self.clusters) > self.cluster_count:
            raise ValueError("cluster summary 数量不能大于 cluster_count")
        return self


__all__ = [
    "AnalysisStepSummary",
    "ArtifactRef",
    "AtomicAnalysisRequest",
    "AtomicAnalysisResult",
    "CapabilityKind",
    "CapabilityRequest",
    "CapabilitySpec",
    "CapabilityStatus",
    "DatasetContext",
    "DeepCellAnnotationRequest",
    "DeepCellAnnotationResult",
    "InspectDatasetContextRequest",
    "InspectDatasetContextResult",
    "InspectMarkerContractRequest",
    "InspectMarkerContractResult",
    "MarkerClusterSummary",
    "SingleCellAnalysisRequest",
    "SingleCellAnalysisResult",
]
