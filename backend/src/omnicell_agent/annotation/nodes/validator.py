import logging
from typing import Dict, Any

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from pydantic import BaseModel, Field

from omnicell_agent.schema.state import ClusterAnnotationState
from omnicell_agent import llm

logger = logging.getLogger(__name__)


class ValidatorOutput(BaseModel):
    """Validator 交叉审查报告的数据契约"""

    is_supported: bool = Field(
        ...,
        description="当前 marker panel 是否与候选标签相容且没有未解决的关键冲突。",
    )
    confidence_penalty: int = Field(
        ...,
        ge=0,
        le=50,
        description="启发式证据扣分（0-50）；越高表示支持越弱、冲突越大或标签粒度越过度。",
    )
    critique: str = Field(
        ...,
        min_length=1,
        max_length=2_000,
        description="简要说明主要支持、冲突、替代解释和需要人工复核的原因。",
    )


def validator_node(state: ClusterAnnotationState) -> Dict[str, Any]:
    """
    细胞注释内部并发节点：Validator。
    接手 Annotator/Boost 产生的预测结果，注入物种与组织语境，执行红蓝对抗复核。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    top_markers = state.get("top_n_markers", [])
    quantitative_markers = state.get("top_marker_evidence", [])
    predictions = state.get("predictions", {})
    sub_type = predictions.get("sub_type", "Unknown")
    species = (state.get("species") or "Unknown").strip() or "Unknown"
    tissue = (state.get("tissue") or "Unknown").strip() or "Unknown"
    annotator_reasoning = (predictions.get("reasoning_chain") or "").strip()
    marker_evidence = predictions.get("marker_evidence") or []
    if isinstance(marker_evidence, list):
        me_text = "\n".join(f"- {m}" for m in marker_evidence)
    else:
        me_text = str(marker_evidence)

    logger.info(f"--- NODE: VALIDATOR (Cluster {cluster_id}) ---")

    if (
        not top_markers
        or not quantitative_markers
        or sub_type == "Unknown"
        or sub_type.startswith("Error")
    ):
        logger.warning(f"[Cluster {cluster_id}] 无有效鉴定结果可供审计，给出顶额惩罚。")
        existing_scores = state.get("quality_scores", {})
        new_scores = (
            dict(existing_scores)
            if isinstance(existing_scores, dict)
            else {}
        )
        new_scores.update(
            {
                "validator_penalty": 50,
                "validator_supported": False,
                "validator_failed": False,
            }
        )
        return {
            "quality_scores": new_scores
        }

    system_prompt = (
        "You are an independent reviewer of a provisional single-cell cluster annotation. "
        f"The sample is described as **{species}** / **{tissue}**. "
        "Evaluate compatibility between the proposed label and the supplied quantitative DE marker "
        "panel. Interpret adjusted significance, effect size, within-cluster expression, "
        "out-of-cluster expression, and delta_pct together; no single metric proves identity. "
        "Check coherent positive evidence, conflicting or non-specific markers, plausible alternative "
        "labels, mixed/doublet/state signals, and whether subtype precision exceeds the evidence. "
        "Use tissue and species as context, not as a whitelist: an unexpected population requires stronger "
        "marker evidence but must not be rejected from prior expectation alone. Top-marker omission is not "
        "proof of true absence. Return an evidence-based critique and a 0-50 heuristic penalty; do not "
        "describe the result as uniquely proven, calibrated confidence, or verified identity."
    )

    user_prompt = (
        f"Sample context: {species} | {tissue}\n"
        "Bounded quantitative DE marker panel:\n"
        + "\n".join(f"- {marker}" for marker in quantitative_markers)
        + "\n"
        f"Proposed cell type (sub_type): {sub_type}\n"
        f"Annotator evidence assessment:\n{annotator_reasoning or '(not provided)'}\n"
        f"Annotator marker_evidence:\n{me_text or '(not provided)'}\n\n"
        "Critically evaluate this annotation."
    )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    model = llm.get_llm_by_alias(llm.LLMRole.VALIDATION, temperature=0.0)
    structured_llm = model.with_structured_output(ValidatorOutput)

    try:
        logger.info(f"[Cluster {cluster_id}] 正在进行独立证据复核...")
        result: ValidatorOutput = structured_llm.invoke(messages)

        logger.info(
            f"[Cluster {cluster_id}] Validator 复核完成. 证据扣分: "
            f"{result.confidence_penalty}"
        )

        ai_response = AIMessage(
            content=(
                f"**Validator Critique**:\n{result.critique}\n"
                f"Evidence Penalty: {result.confidence_penalty}"
            )
        )

        existing_scores = state.get("quality_scores", {})
        new_scores = dict(existing_scores) if isinstance(existing_scores, dict) else {}
        new_scores["validator_penalty"] = (
            result.confidence_penalty
            if result.is_supported
            else 50
        )
        new_scores["validator_supported"] = result.is_supported
        new_scores["validator_failed"] = False

        return {
            "quality_scores": new_scores,
            "reasoning_messages": [HumanMessage(content=user_prompt), ai_response],
        }
    except Exception as e:
        logger.error(f"[Cluster {cluster_id}] Validator 运行崩溃: {e}")
        existing_scores = state.get("quality_scores", {})
        new_scores = (
            dict(existing_scores)
            if isinstance(existing_scores, dict)
            else {}
        )
        new_scores.update(
            {
                "validator_penalty": 50,
                "validator_supported": False,
                "validator_failed": True,
            }
        )
        return {"quality_scores": new_scores}
