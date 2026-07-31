"""Cell annotation capability and bounded marker-table inspection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import uuid4

from omnicell_agent.annotation.graph import build_annotation_graph
from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import (
    CapabilityEffect,
    CapabilityMode,
    CapabilityRequest,
    CapabilitySpec,
    CellAnnotationClusterSummary,
    CellAnnotationRequest,
    CellAnnotationResult,
    InspectMarkerTableRequest,
    InspectMarkerTableResult,
    MarkerClusterSummary,
    MarkerSelectionEvidence,
)
from .errors import CapabilityExecutionError, CapabilityInputError
from .registry import CapabilityContext


AnnotationEngineFactory = Callable[[], Any]
ANNOTATION_ARTIFACT_SCHEMA_VERSION = 1


def _needs_manual_review(annotation: Mapping[str, Any]) -> bool:
    try:
        score = float(annotation.get("cs_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    flags = annotation.get("flags") or []
    return (
        score < 75.0
        or bool(flags)
        or _validator_status(annotation) != "supported"
    )


def _validator_status(
    annotation: Mapping[str, Any],
) -> str:
    status = str(annotation.get("validator_status") or "not_run")
    if status not in {"supported", "unsupported", "failed", "not_run"}:
        return "not_run"
    return status


def _cluster_sort_key(cluster_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(cluster_id))
    except ValueError:
        return (1, cluster_id)


def _bounded_text(value: Any, *, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text[:200]


def _annotation_summary(
    cluster_id: str,
    annotation: Mapping[str, Any],
) -> CellAnnotationClusterSummary:
    try:
        raw_score = float(annotation.get("cs_score", 0.0))
    except (TypeError, ValueError):
        raw_score = 0.0
    score = min(max(raw_score, 0.0), 100.0)
    raw_flags = annotation.get("flags") or []
    flags = (
        [
            text
            for item in raw_flags[:20]
            if (text := str(item).strip()[:100])
        ]
        if isinstance(raw_flags, list)
        else []
    )
    return CellAnnotationClusterSummary(
        cluster_id=cluster_id[:256],
        general_type=_bounded_text(
            annotation.get("general_type"),
            fallback="Unknown",
        ),
        sub_type=_bounded_text(
            annotation.get("sub_type"),
            fallback="Unknown",
        ),
        confidence_score=score,
        flags=flags,
        validator_status=_validator_status(annotation),
        requires_manual_review=_needs_manual_review(annotation),
    )


class InspectMarkerTableCapability:
    spec = CapabilitySpec(
        name="inspect_marker_table",
        mode=CapabilityMode.INSPECT,
        effect=CapabilityEffect.INSPECT,
        description="校验 marker-table artifact，并返回有界的 cluster 与 marker 摘要。",
        prompt_hint=(
            "仅在需要确认 marker table 是否可用于注释，或需要查看有界 cluster/marker 摘要时调用；"
            "这是只读检查，不生成细胞类型结论；不要用它代替注释或 marker 重算。"
        ),
        consumes=("marker_table",),
        preconditions=("输入是当前 conversation 已登记的 marker_table",),
        recommended_skills=(
            "cluster-and-marker-analysis",
            "cell-type-annotation",
        ),
    )
    request_model = InspectMarkerTableRequest
    result_model = InspectMarkerTableResult

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> InspectMarkerTableResult:
        typed = cast(InspectMarkerTableRequest, request)
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

        return InspectMarkerTableResult(
            source_marker_table=typed.marker_table,
            marker_count=len(contract.markers),
            cluster_count=len(grouped),
            clusters=summaries,
            truncated=(len(grouped) > typed.max_clusters or marker_projection_truncated),
        )


class CellAnnotationCapability:
    spec = CapabilitySpec(
        name="annotate_cell_clusters",
        mode=CapabilityMode.COMPOSITE,
        effect=CapabilityEffect.ANNOTATE,
        description="基于 marker table 生成 cluster 级暂定注释、证据复核、启发式评分、一致性检查和报告。",
        prompt_hint=(
            "仅在用户要求基于已有 marker table 进行细胞类型注释、证据复核和报告时调用；"
            "只查看 marker 或只问方法时不要调用。输出是基于当前证据的暂定注释，"
            "启发式分数不是校准概率，低分或冲突结果需要人工复核；"
            "执行前必须加载 cell-type-annotation Skill。"
        ),
        consumes=("marker_table",),
        produces=("cluster_annotations", "annotation_report"),
        preconditions=("marker table 非空且包含可识别的 cluster 与 gene",),
        recommended_skills=("cell-type-annotation",),
        required_skills=("cell-type-annotation",),
    )
    request_model = CellAnnotationRequest
    result_model = CellAnnotationResult

    def __init__(
        self,
        *,
        graph_factory: AnnotationEngineFactory = build_annotation_graph,
    ) -> None:
        self._graph_factory = graph_factory

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> CellAnnotationResult:
        typed = cast(CellAnnotationRequest, request)
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
                    f"internal/cell-annotation/{uuid4().hex}/markers.json"
                ),
                marker_stream,
                max_bytes=max(typed.marker_table.size_bytes, 1),
                kind="marker_table",
                media_type="application/json",
                metadata={
                    "source_artifact_id": str(
                        typed.marker_table.artifact_id
                    ),
                    "purpose": "cell_annotation_verified_input",
                },
            )
            contract_path = context.artifacts.resolve(
                pinned_input,
                expected_kind="marker_table",
            )
        marker_clusters = {marker.cluster_id for marker in contract.markers}
        if not marker_clusters:
            raise CapabilityInputError("marker contract 不包含可注释 cluster")
        raw_selection = contract.metadata.get("selection")
        if raw_selection is None:
            raise CapabilityInputError(
                "细胞类型注释要求完整的 marker selection evidence；"
                "缺失时不能推断 cluster 覆盖完整"
            )
        try:
            marker_selection = MarkerSelectionEvidence.model_validate(
                raw_selection
            )
        except Exception as exc:
            raise CapabilityInputError(
                "marker contract 的 selection evidence 无效"
            ) from exc
        if marker_clusters != set(marker_selection.reported_clusters):
            raise CapabilityInputError(
                "marker contract 行与 reported cluster coverage 不一致"
            )
        expected_clusters = set(marker_selection.all_clusters)
        omitted_marker_clusters = dict(
            marker_selection.omitted_clusters
        )
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
        if not final_report.strip() or final_report.startswith("Error:"):
            raise CapabilityExecutionError(
                "细胞类型注释未生成有效 annotation report"
            )
        actual_clusters = set(cluster_annotations)
        unexpected_clusters = actual_clusters - expected_clusters
        if unexpected_clusters:
            raise CapabilityExecutionError(
                "细胞类型注释返回了输入证据之外的 cluster："
                f"{sorted(unexpected_clusters)}"
            )
        missing_clusters = expected_clusters - actual_clusters
        unaccounted_missing = missing_clusters - set(
            omitted_marker_clusters
        )
        if unaccounted_missing:
            raise CapabilityExecutionError(
                "细胞类型注释未完整收敛："
                f"missing={sorted(unaccounted_missing)}"
            )
        for cluster_id in sorted(
            missing_clusters,
            key=_cluster_sort_key,
        ):
            cluster_annotations[cluster_id] = {
                "general_type": "Unknown",
                "sub_type": "Unknown",
                "cs_score": 0.0,
                "validator_status": "not_run",
                "flags": [
                    "marker_coverage_incomplete",
                    "needs_review",
                ],
                "marker_omission_reason": omitted_marker_clusters[cluster_id],
            }
        if missing_clusters:
            final_report = (
                final_report.rstrip()
                + "\n\n## Marker coverage 未覆盖的 cluster\n\n"
                + "\n".join(
                    (
                        f"- Cluster **{cluster_id}**："
                        f"{omitted_marker_clusters[cluster_id]}，"
                        "未生成自动标签，必须人工复核。"
                    )
                    for cluster_id in sorted(
                        missing_clusters,
                        key=_cluster_sort_key,
                    )
                )
            )
        for annotation in cluster_annotations.values():
            if not isinstance(annotation, Mapping):
                raise TypeError("cluster annotation 必须是 mapping")

        cluster_count = len(cluster_annotations)
        manual_review_count = sum(
            _needs_manual_review(annotation)
            for annotation in cluster_annotations.values()
        )
        cluster_summaries = [
            _annotation_summary(str(cluster_id), annotation)
            for cluster_id, annotation in sorted(
                cluster_annotations.items(),
                key=lambda item: _cluster_sort_key(str(item[0])),
            )
        ]
        output_token = uuid4().hex
        output_root = context.artifacts.scoped_output_path(
            f"artifacts/cell-annotation/v1/{output_token}"
        )
        common_metadata = {
            "schema_version": ANNOTATION_ARTIFACT_SCHEMA_VERSION,
            "source_marker_table_id": str(typed.marker_table.artifact_id),
            "species": typed.species,
            "tissue": typed.tissue,
            "cluster_count": cluster_count,
            "marker_coverage_complete": not omitted_marker_clusters,
            "omitted_marker_cluster_count": len(
                omitted_marker_clusters
            ),
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

        return CellAnnotationResult(
            source_marker_table=typed.marker_table,
            annotations=annotations_ref,
            report=report_ref,
            cluster_count=cluster_count,
            manual_review_count=manual_review_count,
            marker_coverage_complete=not omitted_marker_clusters,
            omitted_marker_cluster_count=len(omitted_marker_clusters),
            cluster_summaries=cluster_summaries,
        )


__all__ = [
    "CellAnnotationCapability",
    "InspectMarkerTableCapability",
]
