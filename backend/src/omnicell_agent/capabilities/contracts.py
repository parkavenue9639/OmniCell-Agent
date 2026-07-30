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


class CapabilityMode(StrEnum):
    INSPECT = "inspect"
    ATOMIC = "atomic"
    COMPOSITE = "composite"


class CapabilityEffect(StrEnum):
    INSPECT = "inspect"
    TRANSFORM = "transform"
    ANALYZE = "analyze"
    ANNOTATE = "annotate"
    VISUALIZE = "visualize"
    CUSTOM = "custom"


class CapabilityStatus(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    SKIPPED = "skipped"


class AtomicOperationDisposition(StrEnum):
    """What the deterministic recipe actually did to satisfy this request."""

    EXECUTED = "executed"
    REUSED = "reused"


class DatasetScientificState(BaseModel):
    """Bounded, verified projection of the dataset state seen by one Tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    n_obs: int = Field(ge=0)
    n_vars: int = Field(ge=0)
    expression_space: Literal[
        "unknown",
        "normalized_log1p",
        "log1p_detected",
    ]
    expression_space_basis: Literal[
        "none",
        "omnicell_operation",
        "anndata_log1p_metadata",
        "bounded_value_detection",
        "omnicell_lineage_and_matrix",
        "omnicell_hint_and_matrix",
        "anndata_metadata_and_matrix",
    ]
    has_pca: bool
    has_neighbors: bool
    has_leiden: bool
    cluster_ids: list[
        Annotated[str, Field(min_length=1, max_length=256)]
    ] = Field(default_factory=list, max_length=500)
    quality_control_signature: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    normalization_signature: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pca_signature: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    clustering_signature: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def _state_is_self_consistent(self) -> "DatasetScientificState":
        if self.has_leiden != bool(self.cluster_ids):
            raise ValueError("Leiden 状态与 cluster_ids 不一致")
        if not self.has_pca and self.pca_signature is not None:
            raise ValueError("缺少 PCA 时不能声明 pca_signature")
        if not self.has_leiden and self.clustering_signature is not None:
            raise ValueError("缺少 Leiden 时不能声明 clustering_signature")
        return self


class MarkerSelectionEvidence(BaseModel):
    """Hard marker-selection contract verified against the published rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statistical_input: Literal["adata.X"]
    method: Literal["wilcoxon", "t-test", "logreg"]
    adjusted_p_value_max: float = Field(gt=0, le=1)
    min_log2_fold_change: float = Field(ge=0, le=100)
    top_n_per_cluster: int = Field(ge=1, le=500)
    all_clusters: list[
        Annotated[str, Field(min_length=1, max_length=256)]
    ] = Field(max_length=500)
    tested_clusters: list[
        Annotated[str, Field(min_length=1, max_length=256)]
    ] = Field(max_length=500)
    reported_clusters: list[
        Annotated[str, Field(min_length=1, max_length=256)]
    ] = Field(max_length=500)
    omitted_clusters: dict[str, Literal["too_few_cells", "no_threshold_hits"]]
    selected_counts: dict[str, int]
    marker_count: int = Field(ge=0)
    thresholds_strict: Literal[True]

    @model_validator(mode="after")
    def _coverage_is_complete(self) -> "MarkerSelectionEvidence":
        all_clusters = set(self.all_clusters)
        tested = set(self.tested_clusters)
        reported = set(self.reported_clusters)
        omitted = set(self.omitted_clusters)
        if len(all_clusters) != len(self.all_clusters):
            raise ValueError("marker all_clusters 不能重复")
        if not tested.issubset(all_clusters):
            raise ValueError("tested_clusters 必须属于 all_clusters")
        if not reported.issubset(tested):
            raise ValueError("reported_clusters 必须属于 tested_clusters")
        if reported & omitted or reported | omitted != all_clusters:
            raise ValueError("每个 cluster 必须且只能是 reported 或 omitted")
        if set(self.selected_counts) != reported:
            raise ValueError("selected_counts 必须精确覆盖 reported_clusters")
        if any(count <= 0 or count > self.top_n_per_cluster for count in self.selected_counts.values()):
            raise ValueError("selected_counts 超出 marker 输出契约")
        if sum(self.selected_counts.values()) != self.marker_count:
            raise ValueError("marker_count 与 selected_counts 不一致")
        return self


class AtomicScientificEvidence(BaseModel):
    """Current-invocation scientific facts produced by deterministic checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    evidence_level: Literal["validated_observation"] = "validated_observation"
    operation: str = Field(min_length=1, max_length=128)
    source_artifact_id: UUID
    disposition: AtomicOperationDisposition
    parameters: dict[str, Any]
    random_seed: int | None = Field(default=None, ge=0)
    input_state: DatasetScientificState
    output_state: DatasetScientificState
    validation_status: Literal["verified"]
    validation_checks: list[
        Annotated[str, Field(min_length=1, max_length=128)]
    ] = Field(min_length=1, max_length=32)
    marker_selection: MarkerSelectionEvidence | None = None

    @field_validator("parameters")
    @classmethod
    def _bounded_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("scientific evidence parameters 必须可 JSON 序列化") from exc
        if len(encoded) > 16 * 1024:
            raise ValueError("scientific evidence parameters 超过 16 KiB")
        return value

    @model_validator(mode="after")
    def _operation_specific_evidence(self) -> "AtomicScientificEvidence":
        if (self.operation == "find_marker_genes") != (
            self.marker_selection is not None
        ):
            raise ValueError("marker selection evidence 与 operation 不一致")
        return self


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    mode: CapabilityMode
    effect: CapabilityEffect
    description: str = Field(min_length=1, max_length=500)
    version: str = Field(
        default="1.0",
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+$",
    )
    prompt_hint: str = Field(min_length=1, max_length=1_000)
    consumes: tuple[
        Annotated[str, Field(min_length=1, max_length=128)],
        ...,
    ] = Field(default_factory=tuple, max_length=16)
    produces: tuple[
        Annotated[str, Field(min_length=1, max_length=128)],
        ...,
    ] = Field(default_factory=tuple, max_length=16)
    preconditions: tuple[
        Annotated[str, Field(min_length=1, max_length=300)],
        ...,
    ] = Field(default_factory=tuple, max_length=16)
    recommended_skills: tuple[
        Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=128)],
        ...,
    ] = Field(default_factory=tuple, max_length=8)
    required_skills: tuple[
        Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=128)],
        ...,
    ] = Field(default_factory=tuple, max_length=4)

    @model_validator(mode="after")
    def _validate_public_semantics(self) -> "CapabilitySpec":
        public_text = " ".join(
            (self.name, self.description, self.prompt_hint, *self.preconditions)
        ).lower()
        forbidden = ("graph a", "graph b", "graph_a", "graph_b", "图 a", "图 b")
        if any(token in public_text for token in forbidden):
            raise ValueError("公开 capability 不能包含历史 DAG 分类")
        if self.mode == CapabilityMode.INSPECT and self.effect != CapabilityEffect.INSPECT:
            raise ValueError("inspect mode 必须使用 inspect effect")
        if self.required_skills and not set(self.required_skills).issubset(
            self.recommended_skills
        ):
            raise ValueError("required_skills 必须同时出现在 recommended_skills")
        return self

    def model_description(self) -> str:
        details = [self.description, f"科学效果：{self.effect.value}。"]
        if self.consumes:
            details.append(f"输入 artifact：{', '.join(self.consumes)}。")
        if self.produces:
            details.append(f"输出 artifact：{', '.join(self.produces)}。")
        if self.preconditions:
            details.append(f"前置条件：{'；'.join(self.preconditions)}。")
        if self.required_skills:
            details.append(
                f"调用前必须加载 Skill：{', '.join(self.required_skills)}。"
            )
        return "".join(details)


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
    execution_mode: Literal["deterministic", "generated"]
    operation_summary: str = Field(min_length=1, max_length=2_000)
    status: Literal["completed", "pending"]


class ExploratoryAnalysisRequest(CapabilityRequest):
    dataset: ArtifactRef
    goal: str = Field(min_length=1, max_length=20_000)
    acceptance_criterion: Literal[
        "marker_table",
        "cluster_summary",
        "dataset_shape",
        "other",
    ] = Field(
        description=(
            "与当前用户目标对应的 backend 验收器；没有匹配验收器时必须使用 "
            "other，结果只能形成局部事实，不能自动宣称整体科学目标完成。"
        )
    )


class ExploratoryArtifactEvidence(BaseModel):
    """Backend-verified facts for one exploratory output artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    kind: str = Field(min_length=1, max_length=128)
    verification_level: Literal[
        "scientific",
        "structural",
        "unverified",
    ]
    checks: list[
        Annotated[str, Field(min_length=1, max_length=128)]
    ] = Field(default_factory=list, max_length=16)
    facts: dict[str, Any] = Field(default_factory=dict)

    @field_validator("facts")
    @classmethod
    def _bounded_facts(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 16 * 1024:
            raise ValueError("exploratory artifact facts 超过 16 KiB")
        return value


class ExploratoryResultManifest(BaseModel):
    """Result inventory independently derived by the backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    acceptance_criterion: Literal[
        "marker_table",
        "cluster_summary",
        "dataset_shape",
        "other",
    ]
    acceptance_checks: list[
        Annotated[str, Field(min_length=1, max_length=128)]
    ] = Field(default_factory=list, max_length=16)
    scientific_goal_status: Literal[
        "validated",
        "partial",
        "unverified",
    ]
    items: list[ExploratoryArtifactEvidence] = Field(max_length=500)
    authoritative_fact_count: int = Field(ge=0)
    limitations: list[
        Annotated[str, Field(min_length=1, max_length=500)]
    ] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _manifest_is_consistent(self) -> "ExploratoryResultManifest":
        scientific_facts = sum(
            len(item.facts)
            for item in self.items
            if item.verification_level == "scientific"
        )
        if scientific_facts != self.authoritative_fact_count:
            raise ValueError("authoritative_fact_count 与科学级 artifact facts 不一致")
        if self.scientific_goal_status == "validated" and (
            not scientific_facts
            or self.acceptance_criterion == "other"
            or "goal_acceptance_verified" not in self.acceptance_checks
        ):
            raise ValueError("validated exploratory result 必须通过目标验收")
        return self


class ExploratoryAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CapabilityStatus
    context: DatasetContext
    steps: list[AnalysisStepSummary] = Field(max_length=500)
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=500)
    marker_table: ArtifactRef | None = None
    result_manifest: ExploratoryResultManifest
    diagnostic_summary: str | None = Field(default=None, max_length=2_000)


class InspectDatasetContextRequest(CapabilityRequest):
    dataset: ArtifactRef
    goal: str = Field(min_length=1, max_length=20_000)


class InspectDatasetContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: DatasetContext


class DatasetCapabilityRequest(CapabilityRequest):
    dataset: ArtifactRef


class QualityControlRequest(DatasetCapabilityRequest):
    min_genes_per_cell: int = Field(default=200, ge=1, le=20_000)
    min_cells_per_gene: int = Field(default=3, ge=1, le=10_000)
    max_mito_percent: float = Field(default=20.0, gt=0, le=100)


class NormalizeExpressionRequest(DatasetCapabilityRequest):
    target_sum: float = Field(default=10_000.0, gt=0, le=10_000_000)


class ClusterCellsRequest(DatasetCapabilityRequest):
    n_top_genes: int = Field(default=2_000, ge=100, le=20_000)
    n_pcs: int = Field(default=40, ge=2, le=200)
    n_neighbors: int = Field(default=10, ge=2, le=200)
    resolution: float = Field(default=1.0, gt=0, le=10)


class FindMarkerGenesRequest(DatasetCapabilityRequest):
    method: Literal["wilcoxon", "t-test", "logreg"] = "wilcoxon"
    top_n_per_cluster: int = Field(default=50, ge=1, le=500)
    adjusted_p_value_max: float = Field(default=0.05, gt=0, le=1)
    min_log2_fold_change: float = Field(default=1.0, ge=0, le=100)


class PlotPcaClustersRequest(DatasetCapabilityRequest):
    dpi: int = Field(default=300, ge=72, le=600)
    point_size: float = Field(default=50.0, gt=0, le=500)
    palette: str = Field(default="Set2", min_length=1, max_length=64)


class AtomicAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CapabilityStatus
    operation: str = Field(min_length=1, max_length=128)
    source_dataset: ArtifactRef
    output_dataset: ArtifactRef | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=32)
    marker_table: ArtifactRef | None = None
    scientific_evidence: AtomicScientificEvidence
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


class CellAnnotationRequest(CapabilityRequest):
    marker_table: ArtifactRef
    species: str = Field(min_length=1, max_length=200)
    tissue: str = Field(min_length=1, max_length=200)


class CellAnnotationClusterSummary(BaseModel):
    """Bounded authoritative facts that the top-level Agent may explain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str = Field(min_length=1, max_length=256)
    general_type: str = Field(min_length=1, max_length=200)
    sub_type: str = Field(min_length=1, max_length=200)
    confidence_score: float = Field(
        ge=0,
        le=100,
        description=(
            "内部规则合成的启发式证据评分，不是经过校准的概率或验证结论。"
        ),
    )
    flags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list,
        max_length=20,
    )
    validator_status: Literal[
        "supported",
        "unsupported",
        "failed",
        "not_run",
    ]
    requires_manual_review: bool


class CellAnnotationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[CapabilityStatus.COMPLETED] = CapabilityStatus.COMPLETED
    source_marker_table: ArtifactRef
    annotations: ArtifactRef
    report: ArtifactRef
    cluster_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    marker_coverage_complete: bool
    omitted_marker_cluster_count: int = Field(ge=0)
    cluster_summaries: list[CellAnnotationClusterSummary] = Field(
        max_length=500,
    )


class InspectMarkerTableRequest(CapabilityRequest):
    marker_table: ArtifactRef
    top_markers_per_cluster: int = Field(default=10, ge=1, le=20)
    max_clusters: int = Field(default=100, ge=1, le=500)


class MarkerClusterSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(max_length=256)
    marker_count: int = Field(ge=0)
    top_markers: list[Annotated[str, Field(max_length=256)]] = Field(max_length=20)


class InspectMarkerTableResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_marker_table: ArtifactRef
    marker_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    clusters: list[MarkerClusterSummary] = Field(max_length=500)
    truncated: bool

    @model_validator(mode="after")
    def _bounded_projection(self) -> "InspectMarkerTableResult":
        if len(self.clusters) > self.cluster_count:
            raise ValueError("cluster summary 数量不能大于 cluster_count")
        return self


__all__ = [
    "AnalysisStepSummary",
    "ArtifactRef",
    "AtomicAnalysisResult",
    "AtomicOperationDisposition",
    "AtomicScientificEvidence",
    "CapabilityEffect",
    "CapabilityMode",
    "CapabilityRequest",
    "CapabilitySpec",
    "CapabilityStatus",
    "CellAnnotationClusterSummary",
    "CellAnnotationRequest",
    "CellAnnotationResult",
    "ClusterCellsRequest",
    "DatasetContext",
    "DatasetCapabilityRequest",
    "DatasetScientificState",
    "ExploratoryAnalysisRequest",
    "ExploratoryAnalysisResult",
    "ExploratoryArtifactEvidence",
    "ExploratoryResultManifest",
    "FindMarkerGenesRequest",
    "InspectDatasetContextRequest",
    "InspectDatasetContextResult",
    "InspectMarkerTableRequest",
    "InspectMarkerTableResult",
    "MarkerClusterSummary",
    "MarkerSelectionEvidence",
    "NormalizeExpressionRequest",
    "PlotPcaClustersRequest",
    "QualityControlRequest",
]
