import logging
from typing import Dict, Any

from omnicell_agent.schema.state import Annotation_State

logger = logging.getLogger(__name__)

def scorer_node(state: Annotation_State) -> Dict[str, Any]:
    """
    Sub-Graph B 并发节点: Scorer
    汇总之前的推理成果和 Validator 的惩罚决定，产出最终 0-100 的置信度得分 (Confidence Score)。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    quality_scores = state.get("quality_scores", {})
    predictions = state.get("predictions", {})
    sub_type = predictions.get("sub_type", "Unknown")
    
    logger.info(f"--- NODE: SCORER (Cluster {cluster_id}) ---")
    
    # 极权处理
    if sub_type == "Unknown" or sub_type.startswith("Error"):
        final_score = 0.0
    else:
        # 基础分 100 分
        base_score = 100.0
        
        # 1. Validator 扣分 (-0 到 -50)
        penalty = quality_scores.get("validator_penalty", 50)  # 如果没有获取到惩罚则视为最高惩罚
        
        # 2. 启发式：如果给出的 Marker 数量太少，则天然置信度打折
        top_markers = state.get("top_n_markers", [])
        if len(top_markers) < 3:
            base_score -= 20   # 证据过少
        elif len(top_markers) < 5:
            base_score -= 10
            
        final_score = max(0.0, base_score - penalty)
        
    logger.info(f"[Cluster {cluster_id}] 置信度打分完成 => {final_score}/100")
    
    # 将得出的 cs_score 放进状态流，供之后的 Boost 节点和 Reporter 使用
    new_scores = quality_scores.copy()
    new_scores["cs_score"] = final_score
    
    return {"quality_scores": new_scores}
