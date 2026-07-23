"""Graph B workflow capability and bounded marker-contract inspection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import uuid4

from omnicell_agent.annotation.graph import build_annotation_graph
from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import (
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
    DeepCellAnnotationRequest,
    DeepCellAnnotationResult,
    InspectMarkerContractRequest,
    InspectMarkerContractResult,
    MarkerClusterSummary,
)
from .errors import CapabilityExecutionError, CapabilityInputError
from .registry import CapabilityContext


GraphFactory = Callable[[], Any]
ANNOTATION_ARTIFACT_SCHEMA_VERSION = 1


def _needs_manual_review(annotation: Mapping[str, Any]) -> bool:
    try:
        score = float(annotation.get("cs_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    flags = annotation.get("flags") or []
    sub_type = annotation.get("sub_type", "Unknown")
    return (
        score < 60.0
        or bool(flags)
        or (isinstance(sub_type, str) and "(NeedsReview)" in sub_type)
    )


class InspectMarkerContractCapability:
    spec = CapabilitySpec(
        name="inspect_marker_contract",
        kind=CapabilityKind.READ_ONLY,
        description="校验 marker-table artifact，并返回有界的 cluster 与 marker 摘要。",
        prompt_hint=(
            "仅在需要确认 marker table 是否可用于注释，或需要查看有界 cluster/marker 摘要时调用；"
            "不要用它代替完整细胞注释。"
        ),
    )
    request_model = InspectMarkerContractRequest
    result_model = InspectMarkerContractResult

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> InspectMarkerContractResult:
        typed = cast(InspectMarkerContractRequest, request)
        with context.artifacts.open_verified(
            typed.marker_table,
            expected_kind="marker_table",
        ) as marker_stream:
            contract = MarkerTableContract.load_from_stream(marker_stream)

        grouped: dict[str, list[Any]] = {}
        for marker in contract.markers:
            grouped.setdefault(marker.cluster_id, []).append(marker)

        selected = list(grouped.items())[: typed.max_clusters]
        summaries: list[MarkerClusterSummary] = []
        marker_projection_truncated = False
        for cluster_id, markers in selected:
            ordered = sorted(markers, key=lambda marker: marker.p_val_adj)
            if len(ordered) > typed.top_markers_per_cluster:
                marker_projection_truncated = True
            summaries.append(
                MarkerClusterSummary(
                    cluster_id=cluster_id,
                    marker_count=len(ordered),
                    top_markers=[
                        marker.gene_name
                        for marker in ordered[: typed.top_markers_per_cluster]
                    ],
                )
            )

        return InspectMarkerContractResult(
            source_marker_table=typed.marker_table,
            marker_count=len(contract.markers),
            cluster_count=len(grouped),
            clusters=summaries,
            truncated=(len(grouped) > typed.max_clusters or marker_projection_truncated),
        )


class DeepCellAnnotationCapability:
    spec = CapabilitySpec(
        name="deep_cell_annotation",
        kind=CapabilityKind.WORKFLOW,
        description="运行保留 fan-out、验证、评分、可选增强和一致性审阅语义的完整 Graph B。",
        prompt_hint=(
            "仅在用户要求基于 marker table 的完整深度注释、验证与报告时调用；"
            "执行前应加载 deep-cell-annotation Skill。"
        ),
    )
    request_model = DeepCellAnnotationRequest
    result_model = DeepCellAnnotationResult

    def __init__(self, *, graph_factory: GraphFactory = build_annotation_graph) -> None:
        self._graph_factory = graph_factory

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> DeepCellAnnotationResult:
        typed = cast(DeepCellAnnotationRequest, request)
        with context.artifacts.open_verified(
            typed.marker_table,
            expected_kind="marker_table",
        ) as marker_stream:
            try:
                contract = MarkerTableContract.load_from_stream(marker_stream)
            except Exception as exc:
                raise CapabilityInputError(
                    "marker contract 无法解析或校验"
                ) from exc
            marker_stream.seek(0)
            pinned_input = context.artifacts.import_stream(
                context.artifacts.scoped_output_path(
                    f"internal/graph-b/{uuid4().hex}/markers.json"
                ),
                marker_stream,
                max_bytes=max(typed.marker_table.size_bytes, 1),
                kind="marker_table",
                media_type="application/json",
                metadata={
                    "source_artifact_id": str(
                        typed.marker_table.artifact_id
                    ),
                    "purpose": "graph_b_verified_input",
                },
            )
            contract_path = context.artifacts.resolve(
                pinned_input,
                expected_kind="marker_table",
            )
        expected_clusters = {marker.cluster_id for marker in contract.markers}
        if not expected_clusters:
            raise CapabilityInputError("marker contract 不包含可注释 cluster")
        initial_state = {
            "contract_file_path": str(contract_path),
            "species": typed.species,
            "tissue": typed.tissue,
            "cluster_annotations": {},
            "final_report": "",
        }
        final_state = self._graph_factory().invoke(initial_state)

        cluster_annotations = dict(final_state.get("cluster_annotations") or {})
        final_report = str(final_state.get("final_report") or "")
        actual_clusters = set(cluster_annotations)
        if actual_clusters != expected_clusters:
            raise CapabilityExecutionError(
                "Graph B cluster annotation 未完整收敛："
                f"expected={sorted(expected_clusters)}, actual={sorted(actual_clusters)}"
            )
        if not final_report.strip() or final_report.startswith("Error:"):
            raise CapabilityExecutionError("Graph B 未生成有效 annotation report")
        for annotation in cluster_annotations.values():
            if not isinstance(annotation, Mapping):
                raise TypeError("Graph B cluster annotation 必须是 mapping")

        cluster_count = len(cluster_annotations)
        manual_review_count = sum(
            _needs_manual_review(annotation)
            for annotation in cluster_annotations.values()
        )
        output_token = uuid4().hex
        output_root = context.artifacts.scoped_output_path(
            f"artifacts/graph-b/v1/{output_token}"
        )
        common_metadata = {
            "schema_version": ANNOTATION_ARTIFACT_SCHEMA_VERSION,
            "source_marker_table_id": str(typed.marker_table.artifact_id),
            "species": typed.species,
            "tissue": typed.tissue,
            "cluster_count": cluster_count,
        }
        annotations_ref = context.artifacts.write_json(
            f"{output_root}/annotations.json",
            {
                "schema_version": ANNOTATION_ARTIFACT_SCHEMA_VERSION,
                "source_marker_table_id": str(typed.marker_table.artifact_id),
                "species": typed.species,
                "tissue": typed.tissue,
                "cluster_annotations": cluster_annotations,
            },
            kind="cluster_annotations",
            metadata=common_metadata,
        )
        report_ref = context.artifacts.write_text(
            f"{output_root}/report.md",
            final_report,
            kind="annotation_report",
            media_type="text/markdown",
            metadata={
                **common_metadata,
                "manual_review_count": manual_review_count,
            },
        )

        return DeepCellAnnotationResult(
            source_marker_table=typed.marker_table,
            annotations=annotations_ref,
            report=report_ref,
            cluster_count=cluster_count,
            manual_review_count=manual_review_count,
        )


__all__ = [
    "DeepCellAnnotationCapability",
    "InspectMarkerContractCapability",
]
