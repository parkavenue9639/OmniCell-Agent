"""Independent bounded validation for exploratory output artifacts."""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Mapping
from typing import Any

import h5py
from PIL import Image

from omnicell_agent.schema.contract import MarkerTableContract

from .artifacts import ConversationArtifactStore
from .contracts import (
    ArtifactRef,
    ExploratoryArtifactEvidence,
    ExploratoryResultManifest,
    MarkerSelectionEvidence,
)


def build_exploratory_result_manifest(
    store: ConversationArtifactStore,
    artifacts: list[ArtifactRef],
    *,
    marker_contracts: Mapping[str, MarkerTableContract],
    source_dataset: ArtifactRef | None = None,
    acceptance_criterion: str,
) -> ExploratoryResultManifest:
    source_shape = _dataset_shape(store, source_dataset)
    items = [
        _validate_artifact(
            store,
            ref,
            marker_contract=marker_contracts.get(str(ref.artifact_id)),
            source_n_obs=source_shape[0] if source_shape is not None else None,
        )
        for ref in artifacts
    ]
    items = _reconcile_cluster_projections(items)
    authoritative_fact_count = sum(
        len(item.facts)
        for item in items
        if item.verification_level == "scientific"
    )
    unverified_count = sum(
        item.verification_level == "unverified"
        for item in items
    )
    goal_accepted, acceptance_checks = _validate_goal_acceptance(
        acceptance_criterion,
        items,
    )
    if goal_accepted and not unverified_count:
        status = "validated"
    elif items:
        status = "partial"
    else:
        status = "unverified"
    limitations: list[str] = []
    if not authoritative_fact_count:
        limitations.append(
            "当前产物只通过结构或文件边界校验，尚无可作为当前数据结论的科学级事实。"
        )
    if not goal_accepted:
        limitations.append(
            "当前产物没有通过本次类型化目标验收；局部已验证事实不能代表整体科学目标完成。"
        )
    if any(item.verification_level == "structural" for item in items):
        limitations.append(
            "图像、通用表格和文本只验证可读性与结构，不验证其科学解释。"
        )
    if unverified_count:
        limitations.append(
            f"{unverified_count} 个产物没有可用的独立 backend 校验器，只能作为草稿。"
        )
    return ExploratoryResultManifest(
        acceptance_criterion=acceptance_criterion,
        acceptance_checks=acceptance_checks,
        scientific_goal_status=status,
        items=items,
        authoritative_fact_count=authoritative_fact_count,
        limitations=limitations,
    )


def _validate_artifact(
    store: ConversationArtifactStore,
    ref: ArtifactRef,
    *,
    marker_contract: MarkerTableContract | None,
    source_n_obs: int | None,
) -> ExploratoryArtifactEvidence:
    if marker_contract is not None:
        selection = marker_contract.metadata.get("selection")
        try:
            selection_evidence = MarkerSelectionEvidence.model_validate(selection)
        except Exception:
            selection_evidence = None
        declared_clusters = (
            selection_evidence.all_clusters
            if selection_evidence is not None
            else None
        )
        clusters = sorted(
            (
                {str(item) for item in declared_clusters}
                if isinstance(declared_clusters, list)
                else {
                    str(marker.cluster_id)
                    for marker in marker_contract.markers
                }
            ),
            key=_cluster_sort_key,
        )
        return ExploratoryArtifactEvidence(
            artifact_id=ref.artifact_id,
            kind="marker_table",
            verification_level=(
                "scientific"
                if selection_evidence is not None
                else "structural"
            ),
            checks=[
                "artifact_identity_verified",
                "marker_contract_valid",
                "marker_rows_nonempty",
                *(
                    [
                        "marker_selection_evidence_verified",
                        "cluster_projection_verified",
                    ]
                    if selection_evidence is not None
                    else ["marker_selection_evidence_missing"]
                ),
            ],
            facts={
                "marker_count": len(marker_contract.markers),
                "cluster_count": len(clusters),
                "cluster_ids": clusters,
            },
        )

    if ref.kind == "dataset":
        shape = _dataset_shape(store, ref)
        if shape is None:
            return _unverified(ref, "dataset_shape_unavailable")
        n_obs, n_vars = shape
        return ExploratoryArtifactEvidence(
            artifact_id=ref.artifact_id,
            kind=ref.kind,
            verification_level="scientific",
            checks=[
                "artifact_identity_verified",
                "h5ad_structure_valid",
                "dataset_shape_verified",
            ],
            facts={"n_obs": n_obs, "n_vars": n_vars},
        )

    if ref.kind == "image":
        try:
            with store.open_verified(ref, expected_kind="image") as handle:
                with Image.open(handle) as image:
                    image.verify()
                    width, height = image.size
                    image_format = str(image.format or "unknown")
        except (OSError, ValueError):
            return _unverified(ref, "image_decode_failed")
        return ExploratoryArtifactEvidence(
            artifact_id=ref.artifact_id,
            kind=ref.kind,
            verification_level="structural",
            checks=["artifact_identity_verified", "image_decode_verified"],
            facts={
                "width": width,
                "height": height,
                "format": image_format,
            },
        )

    if ref.media_type == "application/json" or ref.kind == "json":
        try:
            with store.open_verified(ref) as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return _unverified(ref, "json_decode_failed")
        records = _cluster_records(payload)
        if records is not None:
            scientific_facts = _cluster_summary_facts(
                records,
                source_n_obs=source_n_obs,
            )
            if scientific_facts is None:
                return _unverified(
                    ref,
                    "cluster_summary_invariant_failed",
                )
            return ExploratoryArtifactEvidence(
                artifact_id=ref.artifact_id,
                kind=ref.kind,
                verification_level="scientific",
                checks=[
                    "artifact_identity_verified",
                    "json_decode_verified",
                    "cluster_counts_verified",
                    "source_cell_count_reconciled",
                    *(
                        ["cluster_proportions_verified"]
                        if "proportion_sum" in scientific_facts
                        else []
                    ),
                ],
                facts=scientific_facts,
            )
        facts: dict[str, Any] = {
            "top_level_type": type(payload).__name__,
        }
        if isinstance(payload, (list, dict)):
            facts["item_count"] = len(payload)
        if isinstance(payload, dict):
            facts["top_level_keys"] = sorted(str(key) for key in payload)[:100]
        return ExploratoryArtifactEvidence(
            artifact_id=ref.artifact_id,
            kind=ref.kind,
            verification_level="structural",
            checks=["artifact_identity_verified", "json_decode_verified"],
            facts=facts,
        )

    if ref.media_type in {"text/csv", "text/tab-separated-values"}:
        delimiter = "\t" if ref.media_type == "text/tab-separated-values" else ","
        try:
            with store.open_verified(ref) as handle:
                wrapper = io.TextIOWrapper(handle, encoding="utf-8", newline="")
                reader = csv.DictReader(wrapper, delimiter=delimiter)
                if reader.fieldnames is None:
                    raise ValueError("table header missing")
                header = list(reader.fieldnames)
                records = [dict(row) for row in reader]
        except (
            OSError,
            UnicodeDecodeError,
            csv.Error,
            StopIteration,
            ValueError,
        ):
            return _unverified(ref, "table_decode_failed")
        scientific_facts = _cluster_summary_facts(
            records,
            source_n_obs=source_n_obs,
        )
        if scientific_facts is not None:
            return ExploratoryArtifactEvidence(
                artifact_id=ref.artifact_id,
                kind=ref.kind,
                verification_level="scientific",
                checks=[
                    "artifact_identity_verified",
                    "table_shape_verified",
                    "cluster_counts_verified",
                    "source_cell_count_reconciled",
                    *(
                        ["cluster_proportions_verified"]
                        if "proportion_sum" in scientific_facts
                        else []
                    ),
                ],
                facts=scientific_facts,
            )
        if _looks_like_cluster_summary(header):
            return _unverified(ref, "cluster_summary_invariant_failed")
        return ExploratoryArtifactEvidence(
            artifact_id=ref.artifact_id,
            kind=ref.kind,
            verification_level="structural",
            checks=["artifact_identity_verified", "table_shape_verified"],
            facts={
                "row_count": len(records),
                "column_count": len(header),
                "columns": header[:200],
            },
        )

    if ref.kind == "text" or (
        isinstance(ref.media_type, str) and ref.media_type.startswith("text/")
    ):
        try:
            with store.open_verified(ref) as handle:
                text = handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return _unverified(ref, "text_decode_failed")
        return ExploratoryArtifactEvidence(
            artifact_id=ref.artifact_id,
            kind=ref.kind,
            verification_level="structural",
            checks=["artifact_identity_verified", "utf8_decode_verified"],
            facts={
                "character_count": len(text),
                "line_count": len(text.splitlines()),
            },
        )

    return _unverified(ref, "no_independent_validator")


def _unverified(
    ref: ArtifactRef,
    check: str,
) -> ExploratoryArtifactEvidence:
    return ExploratoryArtifactEvidence(
        artifact_id=ref.artifact_id,
        kind=ref.kind,
        verification_level="unverified",
        checks=["artifact_identity_verified", check],
        facts={},
    )


def _h5ad_shape(handle: h5py.File) -> tuple[int, int]:
    matrix = handle["X"]
    if hasattr(matrix, "shape") and len(matrix.shape) == 2:
        return int(matrix.shape[0]), int(matrix.shape[1])
    shape = matrix.attrs.get("shape")
    if shape is None or len(shape) != 2:
        raise ValueError("h5ad X 缺少二维 shape")
    return int(shape[0]), int(shape[1])


def _dataset_shape(
    store: ConversationArtifactStore,
    ref: ArtifactRef | None,
) -> tuple[int, int] | None:
    if ref is None or ref.kind != "dataset":
        return None
    try:
        with store.open_verified(ref, expected_kind="dataset") as handle:
            with h5py.File(handle, "r") as h5ad:
                return _h5ad_shape(h5ad)
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _cluster_records(payload: Any) -> list[dict[str, Any]] | None:
    candidate = payload
    if isinstance(payload, Mapping):
        candidate = payload.get("clusters")
    if not isinstance(candidate, list) or not candidate:
        return None
    if not all(isinstance(item, Mapping) for item in candidate):
        return None
    records = [dict(item) for item in candidate]
    return records if _looks_like_cluster_summary(records[0]) else None


def _looks_like_cluster_summary(
    value: Mapping[str, Any] | list[str],
) -> bool:
    keys = {
        str(key).strip().lower()
        for key in (value.keys() if isinstance(value, Mapping) else value)
    }
    return bool(keys & {"cluster", "cluster_id"}) and bool(
        keys & {"count", "cell_count", "n_cells"}
    )


def _cluster_summary_facts(
    records: list[dict[str, Any]],
    *,
    source_n_obs: int | None,
) -> dict[str, Any] | None:
    if not records or source_n_obs is None:
        return None
    first_keys = {str(key).strip().lower(): str(key) for key in records[0]}
    cluster_key = next(
        (
            first_keys[key]
            for key in ("cluster_id", "cluster")
            if key in first_keys
        ),
        None,
    )
    count_key = next(
        (
            first_keys[key]
            for key in ("n_cells", "cell_count", "count")
            if key in first_keys
        ),
        None,
    )
    proportion_key = next(
        (
            first_keys[key]
            for key in ("proportion", "fraction")
            if key in first_keys
        ),
        None,
    )
    if cluster_key is None or count_key is None:
        return None
    cluster_ids: list[str] = []
    counts: list[int] = []
    proportions: list[float] = []
    try:
        for record in records:
            cluster_id = str(record[cluster_key]).strip()
            raw_count = float(record[count_key])
            count = int(raw_count)
            if (
                not cluster_id
                or not math.isfinite(raw_count)
                or raw_count != count
                or count < 0
            ):
                return None
            cluster_ids.append(cluster_id)
            counts.append(count)
            if proportion_key is not None:
                proportion = float(record[proportion_key])
                if not math.isfinite(proportion) or not 0 <= proportion <= 1:
                    return None
                proportions.append(proportion)
    except (KeyError, TypeError, ValueError):
        return None
    if len(cluster_ids) != len(set(cluster_ids)):
        return None
    total_count = sum(counts)
    if total_count != source_n_obs:
        return None
    facts: dict[str, Any] = {
        "cluster_count": len(cluster_ids),
        "cluster_ids": sorted(cluster_ids, key=_cluster_sort_key),
        "cluster_cell_counts": {
            cluster_id: count
            for cluster_id, count in sorted(
                zip(cluster_ids, counts, strict=True),
                key=lambda item: _cluster_sort_key(item[0]),
            )
        },
        "total_cell_count": total_count,
        "source_n_obs": source_n_obs,
    }
    if proportion_key is not None:
        proportion_sum = sum(proportions)
        if not math.isclose(proportion_sum, 1.0, abs_tol=1e-6):
            return None
        if any(
            not math.isclose(
                proportion,
                count / total_count if total_count else 0.0,
                abs_tol=1e-6,
            )
            for count, proportion in zip(counts, proportions, strict=True)
        ):
            return None
        facts["proportion_sum"] = proportion_sum
        facts["cluster_proportions"] = {
            cluster_id: proportion
            for cluster_id, proportion in sorted(
                zip(cluster_ids, proportions, strict=True),
                key=lambda item: _cluster_sort_key(item[0]),
            )
        }
    return facts


def _reconcile_cluster_projections(
    items: list[ExploratoryArtifactEvidence],
) -> list[ExploratoryArtifactEvidence]:
    projected = [
        item
        for item in items
        if item.verification_level == "scientific"
        and isinstance(item.facts.get("cluster_ids"), list)
    ]
    if len(projected) < 2:
        return items
    cluster_sets = [
        set(item.facts.get("cluster_ids") or [])
        for item in projected
    ]
    cluster_ids_match = all(
        cluster_ids == cluster_sets[0]
        for cluster_ids in cluster_sets[1:]
    )
    count_maps = [
        item.facts["cluster_cell_counts"]
        for item in projected
        if isinstance(item.facts.get("cluster_cell_counts"), Mapping)
    ]
    counts_match = len(count_maps) < 2 or all(
        dict(counts) == dict(count_maps[0])
        for counts in count_maps[1:]
    )
    proportion_maps = [
        item.facts["cluster_proportions"]
        for item in projected
        if isinstance(item.facts.get("cluster_proportions"), Mapping)
    ]
    proportions_match = len(proportion_maps) < 2 or all(
        _proportion_maps_match(proportions, proportion_maps[0])
        for proportions in proportion_maps[1:]
    )
    if cluster_ids_match and counts_match and proportions_match:
        return [
            (
                item.model_copy(
                    update={
                        "checks": [
                            *item.checks,
                            "cross_artifact_cluster_projection_verified",
                        ]
                    }
                )
                if isinstance(item.facts.get("cluster_ids"), list)
                else item
            )
            for item in items
        ]
    return [
        (
            item.model_copy(
                update={
                    "verification_level": "unverified",
                    "checks": [
                        *item.checks,
                        "cross_artifact_cluster_projection_mismatch",
                    ],
                    "facts": {},
                }
            )
            if isinstance(item.facts.get("cluster_ids"), list)
            else item
        )
        for item in items
    ]


def _proportion_maps_match(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    if set(left) != set(right):
        return False
    try:
        return all(
            math.isclose(
                float(left[key]),
                float(right[key]),
                abs_tol=1e-6,
            )
            for key in left
        )
    except (TypeError, ValueError):
        return False


def _validate_goal_acceptance(
    acceptance_criterion: str,
    items: list[ExploratoryArtifactEvidence],
) -> tuple[bool, list[str]]:
    scientific_items = [
        item
        for item in items
        if item.verification_level == "scientific"
    ]
    matched = False
    validator = ""
    if acceptance_criterion == "marker_table":
        matched = any(item.kind == "marker_table" for item in scientific_items)
        validator = "marker_table_goal_validator"
    elif acceptance_criterion == "cluster_summary":
        matched = any(
            isinstance(item.facts.get("cluster_cell_counts"), Mapping)
            for item in scientific_items
        )
        validator = "cluster_summary_goal_validator"
    elif acceptance_criterion == "dataset_shape":
        matched = any(
            item.kind == "dataset"
            and "dataset_shape_verified" in item.checks
            for item in scientific_items
        )
        validator = "dataset_shape_goal_validator"
    elif acceptance_criterion == "other":
        return False, ["goal_acceptance_unavailable"]
    else:
        return False, ["goal_acceptance_criterion_invalid"]
    return (
        (True, [validator, "goal_acceptance_verified"])
        if matched
        else (False, [validator, "goal_acceptance_failed"])
    )


def _cluster_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


__all__ = ["build_exploratory_result_manifest"]
