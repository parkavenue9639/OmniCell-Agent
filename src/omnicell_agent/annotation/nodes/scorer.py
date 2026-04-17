import logging
from typing import Dict, Any

from omnicell_agent.schema.state import Annotation_State

logger = logging.getLogger(__name__)


def scorer_node(state: Annotation_State) -> Dict[str, Any]:
    """
    Sub-Graph B 并发节点: Scorer
    汇总 Annotator/Validator 结果，产出 0-100 的置信度得分 (cs_score)。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    quality_scores = state.get("quality_scores", {})
    predictions = state.get("predictions", {})
    sub_type = predictions.get("sub_type", "Unknown")

    logger.info(f"--- NODE: SCORER (Cluster {cluster_id}) ---")

    if not isinstance(quality_scores, dict):
        quality_scores = {}

    if sub_type == "Unknown" or (isinstance(sub_type, str) and sub_type.startswith("Error")):
        final_score = 0.0
    else:
        base_score = 100.0

        penalty = float(quality_scores.get("validator_penalty", 50))

        # 自一致性：三温度投票不一致时额外惩罚
        self_ok = quality_scores.get("self_consistency_ok", 1.0)
        try:
            self_ok_f = float(self_ok)
        except (TypeError, ValueError):
            self_ok_f = 1.0
        if self_ok_f < 0.5:
            base_score -= 15.0

        top_markers = state.get("top_n_markers", [])
        if len(top_markers) < 3:
            base_score -= 20.0
        elif len(top_markers) < 5:
            base_score -= 10.0

        final_score = max(0.0, base_score - penalty)

    logger.info(f"[Cluster {cluster_id}] 置信度打分完成 => {final_score}/100")

    new_scores = dict(quality_scores) if isinstance(quality_scores, dict) else {}
    new_scores["cs_score"] = final_score

    return {"quality_scores": new_scores}
