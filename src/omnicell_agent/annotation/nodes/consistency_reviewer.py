import logging
from collections import Counter
from typing import Any, Dict

from omnicell_agent.schema.state import SubGraphB_State

logger = logging.getLogger(__name__)


def consistency_reviewer_node(state: SubGraphB_State) -> Dict[str, Any]:
    """
    Reducer 后置：跨簇统计 general_type 分布，将独苗少数派在主导谱系占比高时标为 cross_cluster_outlier 并压分。
    不依赖任何组织白名单，仅样本内统计。
    """
    logger.info("--- NODE: CONSISTENCY_REVIEWER (cross-cluster) ---")

    annotations = state.get("cluster_annotations") or {}
    if not annotations:
        return {}

    type_counts = Counter(
        str((ann.get("general_type") or "Unknown")).strip() for ann in annotations.values()
    )
    total = len(annotations)
    if total == 0:
        return {}

    dominant_type, dominant_count = type_counts.most_common(1)[0]
    dominant_ratio = dominant_count / total

    updated: Dict[str, Any] = {}
    for cid, ann in annotations.items():
        ann = dict(ann)
        flags = list(ann.get("flags") or [])
        gt = str((ann.get("general_type") or "Unknown")).strip()

        if dominant_ratio >= 0.7 and gt != dominant_type and type_counts.get(gt, 0) == 1:
            if "cross_cluster_outlier" not in flags:
                flags.append("cross_cluster_outlier")
            try:
                cs = float(ann.get("cs_score", 0.0))
            except (TypeError, ValueError):
                cs = 0.0
            ann["cs_score"] = min(cs, 60.0)

        ann["flags"] = flags
        updated[cid] = ann

    logger.info(
        f"Consistency review: dominant={dominant_type} ({dominant_ratio:.2%}), "
        f"clusters={total}"
    )
    return {"cluster_annotations": updated}
