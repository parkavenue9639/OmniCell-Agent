"""Shared hard validation for marker rows and their selection evidence."""

from __future__ import annotations

import math

from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import MarkerSelectionEvidence


def validate_marker_selection(
    marker_contract: MarkerTableContract,
    selection: MarkerSelectionEvidence,
) -> None:
    """Reject marker tables whose published rows violate selection evidence."""

    metadata_selection = marker_contract.metadata.get("selection")
    if metadata_selection != selection.model_dump(mode="json"):
        raise ValueError(
            "marker contract metadata 与 scientific evidence 不一致"
        )

    counts: dict[str, int] = {}
    reported = set(selection.reported_clusters)
    for marker in marker_contract.markers:
        if marker.cluster_id not in reported:
            raise ValueError("marker 行引用了未报告 cluster")
        if not all(
            math.isfinite(value)
            for value in (
                marker.p_val,
                marker.p_val_adj,
                marker.log2FC,
                marker.pct_1,
                marker.pct_2,
            )
        ):
            raise ValueError("marker 行包含非有限统计量")
        if not 0 <= marker.p_val <= 1 or not 0 <= marker.p_val_adj <= 1:
            raise ValueError("marker P 值超出 [0, 1]")
        if marker.p_val_adj >= selection.adjusted_p_value_max:
            raise ValueError("marker 行违反 adjusted p-value 阈值")
        if marker.log2FC <= selection.min_log2_fold_change:
            raise ValueError("marker 行违反 log2 fold-change 阈值")
        if not 0 <= marker.pct_1 <= 1 or not 0 <= marker.pct_2 <= 1:
            raise ValueError("marker 表达比例超出 [0, 1]")
        counts[marker.cluster_id] = counts.get(marker.cluster_id, 0) + 1
    if counts != selection.selected_counts:
        raise ValueError("marker 实际行数与 selected_counts 不一致")


__all__ = ["validate_marker_selection"]
