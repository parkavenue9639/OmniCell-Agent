"""Bounded current-Run scientific evidence and final-response reconciliation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


_UUID_PATTERN = re.compile(
    r"(?<![0-9a-fA-F])"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    r"(?![0-9a-fA-F])"
)

_COUNT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "cluster_count": (
        re.compile(
            r"(?:共|总计|共有|有|得到|检测到|识别出)\s*(\d+)\s*个?(?:\s*)(?:cluster|簇)",
            re.I,
        ),
        re.compile(r"(?:cluster_count|聚类数)\s*[:=：]\s*(\d+)", re.I),
        re.compile(r"\b(\d+)\s+clusters?\b", re.I),
        re.compile(r"(\d+)\s*个?(?:细胞群|细胞簇|细胞群体)", re.I),
        re.compile(r"\b(\d+)\s+cell\s+populations?\b", re.I),
    ),
    "marker_count": (
        re.compile(
            r"(?:共|总计|共有|有|得到|检测到|识别出)\s*(\d+)\s*个?(?:\s*)marker(?:\s*genes?)?",
            re.I,
        ),
        re.compile(r"(?:marker_count|marker\s*数量)\s*[:=：]\s*(\d+)", re.I),
    ),
    "manual_review_count": (
        re.compile(r"(?:共|总计|共有|有)\s*(\d+)\s*个?(?:\s*)(?:cluster|簇)?\s*需要人工复核", re.I),
        re.compile(r"(?:manual_review_count|人工复核数)\s*[:=：]\s*(\d+)", re.I),
    ),
}

_OPERATION_TERMS: dict[str, tuple[str, ...]] = {
    "quality_control": ("质控", "质量控制", "过滤"),
    "normalize_expression": ("归一化", "log1p"),
    "cluster_cells": ("聚类", "leiden"),
    "find_marker_genes": ("marker", "标记基因"),
    "plot_pca_clusters": ("pca 图", "pca图", "聚类散点图"),
}
_SCIENTIFIC_CONTEXT_MAX_BYTES = 64 * 1024


def project_scientific_evidence(
    capability: str,
    result_payload: Mapping[str, Any],
    artifact_ids: list[str],
) -> dict[str, Any] | None:
    """Project only typed and backend-validated facts into Agent Loop state."""

    if capability in {
        "quality_control",
        "normalize_expression",
        "cluster_cells",
        "find_marker_genes",
        "plot_pca_clusters",
    }:
        raw = result_payload.get("scientific_evidence")
        if not isinstance(raw, Mapping):
            return None
        if (
            raw.get("evidence_level") != "validated_observation"
            or raw.get("validation_status") != "verified"
            or raw.get("operation") != capability
        ):
            return None
        return {
            "schema_version": 1,
            "kind": "atomic_analysis",
            "evidence_level": "validated_observation",
            "capability": capability,
            "artifact_ids": list(dict.fromkeys(artifact_ids))[:128],
            "source_artifact_id": str(raw.get("source_artifact_id") or ""),
            "disposition": str(raw.get("disposition") or ""),
            "parameters": _bounded_mapping(raw.get("parameters")),
            "random_seed": raw.get("random_seed"),
            "input_state": _dataset_state_projection(raw.get("input_state")),
            "output_state": _dataset_state_projection(raw.get("output_state")),
            "validation_checks": _bounded_strings(
                raw.get("validation_checks"),
                limit=32,
            ),
            **(
                {
                    "marker_selection": _marker_selection_projection(
                        raw["marker_selection"]
                    )
                }
                if isinstance(raw.get("marker_selection"), Mapping)
                else {}
            ),
        }

    if capability == "annotate_cell_clusters":
        summaries = result_payload.get("cluster_summaries")
        if not isinstance(summaries, list):
            return None
        return {
            "schema_version": 1,
            "kind": "cell_annotation",
            "evidence_level": "method_bounded_inference",
            "capability": capability,
            "artifact_ids": list(dict.fromkeys(artifact_ids))[:128],
            "cluster_count": _nonnegative_int(result_payload.get("cluster_count")),
            "manual_review_count": _nonnegative_int(
                result_payload.get("manual_review_count")
            ),
            "marker_coverage_complete": bool(
                result_payload.get("marker_coverage_complete")
            ),
            "omitted_marker_cluster_count": _nonnegative_int(
                result_payload.get("omitted_marker_cluster_count")
            ),
            "cluster_summaries": [
                {
                    "cluster_id": str(item.get("cluster_id") or "")[:256],
                    "general_type": str(item.get("general_type") or "")[:200],
                    "sub_type": str(item.get("sub_type") or "")[:200],
                    "confidence_score": item.get("confidence_score"),
                    "validator_status": str(item.get("validator_status") or ""),
                    "requires_manual_review": bool(
                        item.get("requires_manual_review")
                    ),
                    "flags": _bounded_strings(item.get("flags"), limit=20),
                }
                for item in summaries[:100]
                if isinstance(item, Mapping)
            ],
            "cluster_summaries_truncated": len(summaries) > 100,
        }

    if capability == "run_exploratory_analysis":
        manifest = result_payload.get("result_manifest")
        if not isinstance(manifest, Mapping):
            return None
        goal_status = str(manifest.get("scientific_goal_status") or "")
        if goal_status not in {"validated", "partial", "unverified"}:
            return None
        items = manifest.get("items")
        if not isinstance(items, list):
            return None
        return {
            "schema_version": 1,
            "kind": "exploratory_result",
            "evidence_level": (
                "validated_observation"
                if goal_status == "validated"
                else "structural_only"
            ),
            "capability": capability,
            "artifact_ids": list(dict.fromkeys(artifact_ids))[:128],
            "acceptance_criterion": str(
                manifest.get("acceptance_criterion") or ""
            ),
            "acceptance_checks": _bounded_strings(
                manifest.get("acceptance_checks"),
                limit=16,
            ),
            "scientific_goal_status": goal_status,
            "authoritative_fact_count": _nonnegative_int(
                manifest.get("authoritative_fact_count")
            ),
            "items": [
                {
                    "artifact_id": str(item.get("artifact_id") or ""),
                    "kind": str(item.get("kind") or "")[:128],
                    "verification_level": str(
                        item.get("verification_level") or ""
                    ),
                    "checks": _bounded_strings(
                        item.get("checks"),
                        limit=16,
                    ),
                    "facts": _bounded_mapping(item.get("facts")),
                }
                for item in items[:128]
                if isinstance(item, Mapping)
            ],
            "items_truncated": len(items) > 128,
            "limitations": _bounded_strings(
                manifest.get("limitations"),
                limit=20,
            ),
        }

    if capability == "inspect_marker_table":
        clusters = result_payload.get("clusters")
        if not isinstance(clusters, list):
            return None
        return {
            "schema_version": 1,
            "kind": "marker_table_inspection",
            "evidence_level": "validated_observation",
            "capability": capability,
            "artifact_ids": list(dict.fromkeys(artifact_ids))[:128],
            "marker_count": _nonnegative_int(result_payload.get("marker_count")),
            "cluster_count": _nonnegative_int(result_payload.get("cluster_count")),
            "truncated": bool(result_payload.get("truncated")),
            "clusters": [
                {
                    "cluster_id": str(item.get("cluster_id") or "")[:256],
                    "marker_count": _nonnegative_int(item.get("marker_count")),
                    "top_markers": _bounded_strings(
                        item.get("top_markers"),
                        limit=20,
                    ),
                }
                for item in clusters[:500]
                if isinstance(item, Mapping)
            ],
        }
    return None


def scientific_evidence_from_state(
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = state.get("tool_evidence")
    if not isinstance(raw, Mapping):
        return []
    result: list[dict[str, Any]] = []
    for item in raw.values():
        if not isinstance(item, Mapping):
            continue
        evidence = item.get("scientific_evidence")
        if isinstance(evidence, Mapping):
            result.append(dict(evidence))
    return result[-32:]


def render_scientific_evidence_context(
    evidence: list[dict[str, Any]],
) -> str:
    """Render bounded facts as transient model context, never as history."""

    selected: list[dict[str, Any]] = []
    for item in reversed(evidence):
        candidate = [item, *selected]
        encoded = json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) <= _SCIENTIFIC_CONTEXT_MAX_BYTES:
            selected = candidate
            continue
        compact = _compact_context_item(item)
        candidate = [compact, *selected]
        encoded = json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) <= _SCIENTIFIC_CONTEXT_MAX_BYTES:
            selected = candidate
    payload = json.dumps(
        selected,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        "以下是当前 Run 中已经通过 backend 校验的科研证据。"
        "它只约束当前数据结论；历史消息、Memory、stdout 和常识不能覆盖它。"
        "请准确区分 executed 与 reused；细胞注释属于 method_bounded_inference，"
        "validator_status、人工复核和 marker 覆盖限制必须如实说明。"
        "最终回复中的数量、Artifact 引用和证据等级不得与它冲突。\n"
        f"<current_run_scientific_evidence>{payload}"
        "</current_run_scientific_evidence>"
    )


def validate_scientific_final_response(
    text: str,
    evidence: list[dict[str, Any]],
    *,
    declared_artifact_ids: list[str] | None = None,
) -> list[str]:
    """Diagnose common deterministic contradictions in a model draft.

    This bounded parser is not the public safety boundary. Evidence-bearing
    responses are rendered from the typed backend ledger after diagnosis.
    """

    if not evidence:
        return []
    failures: list[str] = []
    known_artifacts = {
        str(artifact_id).lower()
        for item in evidence
        for artifact_id in item.get("artifact_ids", [])
    }
    mentioned_artifacts = {
        match.group(0).lower()
        for match in _UUID_PATTERN.finditer(text)
    }
    if declared_artifact_ids:
        mentioned_artifacts.update(
            str(artifact_id).lower()
            for artifact_id in declared_artifact_ids
        )
    unknown_artifacts = sorted(mentioned_artifacts - known_artifacts)
    if unknown_artifacts:
        failures.append("引用了不属于当前 Run 已验证结果的 artifact_id")

    latest_facts = _latest_numeric_facts(evidence)
    for fact_name, patterns in _COUNT_PATTERNS.items():
        if fact_name not in latest_facts:
            continue
        claimed = {
            int(match.group(1))
            for pattern in patterns
            for match in pattern.finditer(text)
        }
        if claimed and claimed != {latest_facts[fact_name]}:
            failures.append(f"{fact_name} 与当前 Run 已验证数量不一致")

    lowered = text.lower()
    for item in evidence:
        if item.get("kind") != "atomic_analysis":
            continue
        operation = str(item.get("capability") or "")
        terms = _OPERATION_TERMS.get(operation, ())
        if not any(term in lowered for term in terms):
            continue
        disposition = item.get("disposition")
        if disposition == "reused" and _claims_current_execution(lowered, terms):
            failures.append(f"{operation} 实际为 reused，不能声称本次重新执行")
        if disposition == "executed" and _claims_current_reuse(lowered, terms):
            failures.append(f"{operation} 实际为 executed，不能声称本次跳过或复用")

    annotations = [
        item for item in evidence if item.get("kind") == "cell_annotation"
    ]
    if annotations:
        latest_annotation = annotations[-1]
        if (
            int(latest_annotation.get("manual_review_count") or 0) > 0
            and any(
                phrase in lowered
                for phrase in (
                    "无需人工复核",
                    "不需要人工复核",
                    "全部验证通过",
                    "均已验证通过",
                )
            )
        ):
            failures.append("当前注释仍有必须人工复核的 cluster")
        if (
            not latest_annotation.get("marker_coverage_complete")
            and any(
                phrase in lowered
                for phrase in (
                    "覆盖全部cluster",
                    "覆盖全部 cluster",
                    "所有cluster均已覆盖",
                    "所有 cluster 均已覆盖",
                    "无遗漏",
                )
            )
        ):
            failures.append("marker 覆盖不完整，不能声称全部覆盖")
        if any(
            phrase in lowered
            for phrase in (
                "已确认为",
                "已验证为",
                "确定是",
                "100%确定",
                "100% 确定",
            )
        ):
            failures.append("细胞注释是方法约束下的推断，不能表述为已验证身份")
    exploratory = [
        item for item in evidence if item.get("kind") == "exploratory_result"
    ]
    if exploratory:
        latest_exploratory = exploratory[-1]
        if latest_exploratory.get("scientific_goal_status") != "validated" and any(
            phrase in lowered
            for phrase in (
                "科学结果已验证",
                "科研结论已验证",
                "结果已经科学验证",
                "验证完成且可信",
            )
        ):
            failures.append("探索性产物尚未形成已验证的科学目标证据")
    return list(dict.fromkeys(failures))


def deterministic_scientific_fallback(
    evidence: list[dict[str, Any]],
    failures: list[str],
) -> str:
    """Render a public response exclusively from backend-validated facts."""

    lines = (
        [
            "模型候选与当前 Run 科研证据不一致，未向用户发布；"
            "以下仅列出 backend 已验证事实。"
        ]
        if failures
        else ["以下仅列出当前 Run 中经过 backend 校验的科研事实。"]
    )
    for item in evidence[-8:]:
        kind = item.get("kind")
        if kind == "atomic_analysis":
            operation = str(item.get("capability") or "原子分析")
            disposition = str(item.get("disposition") or "unknown")
            output_state = item.get("output_state")
            summary = f"- {operation}：{disposition}"
            if isinstance(output_state, Mapping):
                summary += (
                    f"，输出 {output_state.get('n_obs', 0)} cells × "
                    f"{output_state.get('n_vars', 0)} genes"
                )
            marker = item.get("marker_selection")
            if isinstance(marker, Mapping):
                summary += f"，严格阈值下 {marker.get('marker_count', 0)} 个 marker"
            lines.append(summary + "。")
        elif kind == "cell_annotation":
            lines.append(
                "- 细胞注释："
                f"{item.get('cluster_count', 0)} 个 cluster，"
                f"{item.get('manual_review_count', 0)} 个需要人工复核，"
                + (
                    "marker 覆盖完整。"
                    if item.get("marker_coverage_complete")
                    else (
                        "marker 覆盖不完整，"
                        f"遗漏 {item.get('omitted_marker_cluster_count', 0)} 个 cluster。"
                    )
                )
            )
        elif kind == "marker_table_inspection":
            lines.append(
                "- Marker table 检查："
                f"{item.get('cluster_count', 0)} 个 cluster，"
                f"{item.get('marker_count', 0)} 个 marker。"
            )
        elif kind == "exploratory_result":
            lines.append(
                "- 探索性分析："
                f"科学目标状态为 {item.get('scientific_goal_status', 'unverified')}，"
                f"{item.get('authoritative_fact_count', 0)} 项 backend 验证事实。"
            )
    artifact_ids = list(
        dict.fromkeys(
            str(artifact_id)
            for item in evidence
            for artifact_id in item.get("artifact_ids", [])
        )
    )
    if artifact_ids:
        lines.append("已登记产物：" + "、".join(artifact_ids[:16]) + "。")
    if failures:
        lines.append("模型候选未发布原因：" + "；".join(failures[:4]) + "。")
    return "\n".join(lines)


def _bounded_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_strings(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:256] for item in value[:limit]]


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _dataset_state_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in (
            "n_obs",
            "n_vars",
            "expression_space",
            "expression_space_basis",
            "has_pca",
            "has_neighbors",
            "has_leiden",
            "cluster_ids",
            "quality_control_signature",
            "normalization_signature",
            "pca_signature",
            "clustering_signature",
        )
    }


def _marker_selection_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "statistical_input",
            "method",
            "adjusted_p_value_max",
            "min_log2_fold_change",
            "top_n_per_cluster",
            "all_clusters",
            "tested_clusters",
            "reported_clusters",
            "omitted_clusters",
            "selected_counts",
            "marker_count",
            "thresholds_strict",
        )
    }


def _compact_context_item(item: dict[str, Any]) -> dict[str, Any]:
    compact = dict(item)
    if compact.get("kind") == "cell_annotation":
        compact["cluster_summaries"] = []
        compact["cluster_summaries_truncated"] = True
    elif compact.get("kind") == "exploratory_result":
        compact["items"] = [
            {
                "artifact_id": result_item.get("artifact_id"),
                "kind": result_item.get("kind"),
                "verification_level": result_item.get("verification_level"),
                "facts": result_item.get("facts"),
            }
            for result_item in compact.get("items", [])
            if isinstance(result_item, Mapping)
            and result_item.get("verification_level") == "scientific"
        ][:32]
        compact["items_truncated"] = True
    return compact


def _latest_numeric_facts(evidence: list[dict[str, Any]]) -> dict[str, int]:
    facts: dict[str, int] = {}
    for item in evidence:
        kind = item.get("kind")
        if kind == "atomic_analysis":
            output = item.get("output_state")
            if isinstance(output, Mapping):
                clusters = output.get("cluster_ids")
                if isinstance(clusters, list) and clusters:
                    facts["cluster_count"] = len(clusters)
            marker = item.get("marker_selection")
            if isinstance(marker, Mapping):
                facts["marker_count"] = _nonnegative_int(marker.get("marker_count"))
        elif kind in {"cell_annotation", "marker_table_inspection"}:
            facts["cluster_count"] = _nonnegative_int(item.get("cluster_count"))
            if kind == "cell_annotation":
                facts["manual_review_count"] = _nonnegative_int(
                    item.get("manual_review_count")
                )
            else:
                facts["marker_count"] = _nonnegative_int(item.get("marker_count"))
        elif kind == "exploratory_result":
            for result_item in item.get("items", []):
                if (
                    not isinstance(result_item, Mapping)
                    or result_item.get("verification_level") != "scientific"
                ):
                    continue
                item_facts = result_item.get("facts")
                if not isinstance(item_facts, Mapping):
                    continue
                for fact_name in ("cluster_count", "marker_count"):
                    if fact_name in item_facts:
                        facts[fact_name] = _nonnegative_int(
                            item_facts.get(fact_name)
                        )
    return facts


def _claims_current_execution(text: str, terms: tuple[str, ...]) -> bool:
    return any(
        re.search(
            rf"(?:本次|刚刚|此次).{{0,12}}(?:执行|进行了|完成|重新).{{0,12}}{re.escape(term)}"
            rf"|(?:本次|刚刚|此次).{{0,12}}{re.escape(term)}.{{0,12}}(?:执行|进行了|完成|重新)",
            text,
            re.I,
        )
        for term in terms
    )


def _claims_current_reuse(text: str, terms: tuple[str, ...]) -> bool:
    return any(
        re.search(
            rf"(?:本次|此次).{{0,12}}(?:跳过|复用|沿用|未执行).{{0,12}}{re.escape(term)}"
            rf"|(?:本次|此次).{{0,12}}{re.escape(term)}.{{0,12}}(?:跳过|复用|沿用|未执行)",
            text,
            re.I,
        )
        for term in terms
    )


__all__ = [
    "deterministic_scientific_fallback",
    "project_scientific_evidence",
    "render_scientific_evidence_context",
    "scientific_evidence_from_state",
    "validate_scientific_final_response",
]
