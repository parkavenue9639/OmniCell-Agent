import logging
from typing import Dict, Any

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from pydantic import BaseModel, Field

from omnicell_agent.schema.state import Annotation_State
from omnicell_agent.core.llm_client import LLMSelector

logger = logging.getLogger(__name__)


class ValidatorOutput(BaseModel):
    """Validator 交叉审查报告的数据契约"""

    is_supported: bool = Field(
        ...,
        description="输入的这批 Marker 是否从生物学机理上严格且独有地支持刚鉴定出的该细胞亚群？",
    )
    confidence_penalty: int = Field(
        ...,
        description="根据支持度开出的惩罚扣分，基于 0 到 50。0代表证据确凿，50代表彻头彻尾的指鹿为马/大模型幻觉。",
    )
    critique: str = Field(
        ...,
        description="用一段话简明扼要地给出你的复审红蓝对抗意见，哪里不合理？",
    )


def validator_node(state: Annotation_State) -> Dict[str, Any]:
    """
    Sub-Graph B 并发节点: Validator
    接手 Annotator/Boost 产生的预测结果，注入物种与组织语境，执行红蓝对抗复核。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    top_markers = state.get("top_n_markers", [])
    predictions = state.get("predictions", {})
    sub_type = predictions.get("sub_type", "Unknown")
    species = (state.get("species") or "Human").strip() or "Human"
    tissue = (state.get("tissue") or "Unknown").strip() or "Unknown"
    annotator_reasoning = (predictions.get("reasoning_chain") or "").strip()
    marker_evidence = predictions.get("marker_evidence") or []
    if isinstance(marker_evidence, list):
        me_text = "\n".join(f"- {m}" for m in marker_evidence)
    else:
        me_text = str(marker_evidence)

    logger.info(f"--- NODE: VALIDATOR (Cluster {cluster_id}) ---")

    if not top_markers or sub_type == "Unknown" or sub_type.startswith("Error"):
        logger.warning(f"[Cluster {cluster_id}] 无有效鉴定结果可供审计，给出顶额惩罚。")
        return {"quality_scores": {"validator_penalty": 50}}

    system_prompt = (
        "You are an independent, highly critical peer reviewer for single-cell annotations. "
        f"The sample is described as **{species}** / **{tissue}**. "
        "Evaluate whether the proposed cell type is biologically plausible given BOTH the marker evidence "
        "AND the tissue context. "
        "Check:\n"
        "1. Do these DE markers uniquely and robustly support this cell type?\n"
        "2. Is this cell type ordinarily expected in this tissue? If not, is there extraordinary marker "
        "evidence that would justify a rare or unexpected population?\n"
        "3. Review the annotator's reasoning and marker_evidence for logical gaps or tissue-context mismatch.\n"
        "Be extremely harsh on tissue-context mismatches without strong marker support. "
        "Deduct confidence_penalty (0-50) for weak evidence, shared lineages, or clear hallucinations."
    )

    user_prompt = (
        f"Sample context: {species} | {tissue}\n"
        f"Top DE markers: {', '.join(top_markers)}\n"
        f"Proposed cell type (sub_type): {sub_type}\n"
        f"Annotator reasoning chain:\n{annotator_reasoning or '(not provided)'}\n"
        f"Annotator marker_evidence:\n{me_text or '(not provided)'}\n\n"
        "Critically evaluate this annotation."
    )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    model = LLMSelector.get_llm("onerouter:default", temperature=0.0)
    structured_llm = model.with_structured_output(ValidatorOutput)

    try:
        logger.info(f"[Cluster {cluster_id}] 正在进行同行大模型交叉纠错审计...")
        result: ValidatorOutput = structured_llm.invoke(messages)

        logger.info(f"[Cluster {cluster_id}] Validator 审计完成. 惩罚分: -{result.confidence_penalty}")

        ai_response = AIMessage(
            content=(
                f"**Validator Critique**:\n{result.critique}\nPenalty Deducted: {result.confidence_penalty}"
            )
        )

        existing_scores = state.get("quality_scores", {})
        new_scores = dict(existing_scores) if isinstance(existing_scores, dict) else {}
        new_scores["validator_penalty"] = result.confidence_penalty

        return {
            "quality_scores": new_scores,
            "reasoning_messages": [HumanMessage(content=user_prompt), ai_response],
        }
    except Exception as e:
        logger.error(f"[Cluster {cluster_id}] Validator 运行崩溃: {e}")
        return {"quality_scores": {"validator_penalty": 25}}
