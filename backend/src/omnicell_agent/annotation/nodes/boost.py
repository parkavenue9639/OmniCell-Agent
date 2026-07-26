import logging
from typing import Dict, Any, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from omnicell_agent.schema.state import ClusterAnnotationState
from omnicell_agent.schema.contract import MarkerTableContract
from omnicell_agent import llm
from omnicell_agent.annotation.nodes.annotator import AnnotationOutput

logger = logging.getLogger(__name__)


def _extract_validator_critique(reasoning_messages: List[BaseMessage]) -> str:
    """从最近一轮 Validator 的 AIMessage 中提取复审意见，供 Boost 针对性纠偏。"""
    for msg in reversed(reasoning_messages or []):
        content = getattr(msg, "content", "") or ""
        if "Validator Critique" in content or "Penalty Deducted" in content:
            return content.strip()
    return ""


def boost_node(state: ClusterAnnotationState) -> Dict[str, Any]:
    """
    细胞注释内部并发节点：Boost（条件增强纠偏）。
    当 cs_score 过低且尚未用尽 Boost 次数时唤醒；输出需经后续 Validator+Scorer 重算分数（不再硬编码高分）。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    species = state.get("species", "Unknown")
    tissue = state.get("tissue", "Unknown")
    contract_path = state.get("contract_file_path", "")
    retry_count = state.get("retry_count", 0)

    logger.info(f"--- NODE: BOOST (Cluster {cluster_id}, Retry {retry_count+1}) ---")

    if retry_count >= 1:
        logger.warning(f"[Cluster {cluster_id}] Boost 挽救次数触顶，必须强制输出当前结果。")
        return {"retry_count": retry_count + 1}

    try:
        contract = MarkerTableContract.load_from_json(contract_path)
        cluster_markers = [m for m in contract.markers if m.cluster_id == str(cluster_id)]

        cluster_markers.sort(key=lambda x: x.p_val_adj)
        deep_markers_info = [
            (
                f"{m.gene_name}(log2FC={m.log2FC:.2f}, "
                f"pct.1={m.pct_1:.2f}, pct.2={m.pct_2:.2f}, "
                f"p_adj={m.p_val_adj:.3g})"
            )
            for m in cluster_markers[:50]
        ]

        critique = _extract_validator_critique(list(state.get("reasoning_messages") or []))
        critique_block = (
            critique
            if critique
            else "(No previous validator critique was available; reassess from the marker evidence.)"
        )

        system_prompt = (
            "You are re-evaluating a provisional single-cell cluster annotation after a low evidence score. "
            f"The supplied context is species={species}, tissue={tissue}; use it as context, not proof.\n\n"
            f"Previous independent critique:\n{critique_block}\n\n"
            "Use the expanded marker table with effect size, within-cluster expression, out-of-cluster "
            "expression, and adjusted significance. Reassess lineage support, specificity, conflicts, "
            "alternative labels, mixed/doublet/state signals, and whether a broader label or Unknown is "
            "more defensible. Address the critique without forcing a different label. Return a concise "
            "evidence assessment, marker_evidence, general_type, and sub_type; do not output hidden "
            "step-by-step reasoning or claim verification."
        )

        user_prompt = (
            f"Deep Diagnostic Markers for Cluster {cluster_id}:\n"
            f"{', '.join(deep_markers_info)}\n\n"
            "Return the reassessed candidate annotation and bounded marker_evidence."
        )

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

        model = llm.get_llm_by_alias(llm.LLMRole.ANNOTATION, temperature=0.3)
        structured_llm = model.with_structured_output(AnnotationOutput)

        logger.info(
            f"[Cluster {cluster_id}] 正在使用 {len(deep_markers_info)} 个 marker 重新评估..."
        )
        result: AnnotationOutput = structured_llm.invoke(messages)

        logger.info(f"[Cluster {cluster_id}] Boost 重鉴定成功 -> {result.sub_type}")

        ai_response = AIMessage(
            content=(
                f"**Reassessment evidence**:\n{result.reasoning_chain}\n\n"
                f"**Revised candidate annotation**:\n"
                f"General Type: {result.general_type}\nSub Type: {result.sub_type}\n"
                f"**Marker evidence**:\n"
                + "\n".join(f"- {m}" for m in result.marker_evidence)
            )
        )

        prev_scores = state.get("quality_scores", {})
        new_scores = dict(prev_scores) if isinstance(prev_scores, dict) else {}
        # 分数交由后续 Validator / Scorer 重算，禁止在此处注入虚高 cs_score
        if "cs_score" in new_scores:
            del new_scores["cs_score"]
        new_scores["boost_applied"] = True

        return {
            "predictions": {
                "general_type": result.general_type,
                "sub_type": result.sub_type,
                "reasoning_chain": result.reasoning_chain,
                "marker_evidence": result.marker_evidence,
            },
            "quality_scores": new_scores,
            "reasoning_messages": [HumanMessage(content=user_prompt), ai_response],
            "retry_count": retry_count + 1,
        }

    except Exception as e:
        logger.error(
            "[Cluster %s] marker 再评估失败",
            cluster_id,
            exc_info=e,
        )
        return {"retry_count": retry_count + 1}
