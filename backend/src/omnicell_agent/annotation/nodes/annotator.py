import logging
from collections import Counter
from typing import Dict, Any, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from pydantic import BaseModel, Field

from omnicell_agent.schema.state import Annotation_State
from omnicell_agent import llm
from omnicell_agent.core.config import ENABLE_SELF_CONSISTENCY

logger = logging.getLogger(__name__)

TEMPERATURES = (0.1, 0.4, 0.7)


class AnnotationOutput(BaseModel):
    """强制 LLM 输出的分步推理与鉴定结果契约"""

    reasoning_chain: str = Field(
        ...,
        description="根据提供的Top Marker基因进行逐步思维链推理（Chain-of-Thought）。",
    )
    general_type: str = Field(
        ...,
        description="预测的细胞大类（例如：Immune cells, Epithelial cells, Stromal cells 等）",
    )
    sub_type: str = Field(
        ...,
        description="极其精确的细胞亚型名字（例如：CD14+ Monocytes, CD8+ T cells 等）",
    )
    marker_evidence: List[str] = Field(
        ...,
        description="逐条列出 marker 如何支持或矛盾于所选 sub_type，例如 'CD3D -> T cell lineage'",
    )


def _normalize_vote_label(s: str) -> str:
    return (s or "").strip().lower()


def _majority_pick(results: List[AnnotationOutput]) -> Tuple[AnnotationOutput, bool]:
    """返回多数票对应的完整结果；若无法唯一多数则取第一个多数成员。self_consistency_ok=False 表示三次 sub_type 不一致。"""
    labels = [_normalize_vote_label(r.sub_type) for r in results]
    counts = Counter(labels)
    most_common = counts.most_common()
    if len(most_common) >= 2 and most_common[0][1] == most_common[1][1]:
        # 平局：例如 1-1-1
        unanimous = len(set(labels)) == 1
        return results[0], unanimous
    winner_label = most_common[0][0]
    for r in results:
        if _normalize_vote_label(r.sub_type) == winner_label:
            return r, True
    return results[0], True


def _run_single_annotation(
    messages: list, temperature: float
) -> AnnotationOutput:
    model = llm.get_llm_by_alias(llm.LLMRole.ANNOTATION, temperature=temperature)
    structured_llm = model.with_structured_output(AnnotationOutput)
    return structured_llm.invoke(messages)


def annotator_node(state: Annotation_State) -> Dict[str, Any]:
    """
    Sub-Graph B 并发节点: Annotator
    三温度自一致性投票 + marker 证据锚定。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    top_markers = state.get("top_n_markers", [])
    species = state.get("species", "Human")
    tissue = state.get("tissue", "PBMC")

    logger.info(f"--- NODE: ANNOTATOR (Cluster {cluster_id}) ---")

    if not top_markers:
        logger.warning(f"[Cluster {cluster_id}] 缺少 Marker 基因输入，无法鉴定。")
        return {
            "predictions": {"general_type": "Unknown", "sub_type": "Unknown"},
            "reasoning_messages": [
                AIMessage(content="Error: No marker genes provided for this cluster.")
            ],
        }

    system_prompt = (
        "You are an expert single-cell biologist and a rigorous cell type annotator. "
        f"Your task is to annotate a specific cell cluster from a {species} {tissue} sample.\n"
        "You will be provided with the top differentially expressed marker genes for this cluster.\n"
        "Follow Chain-of-Thought (CoT):\n"
        "1. Observe markers and broad functional signatures (immune vs non-immune, etc.).\n"
        "2. Identify lineage markers.\n"
        "3. For your chosen general_type and sub_type, list marker_evidence: for EACH relevant marker, "
        "state how it supports or contradicts your choice (one short string per marker or small group).\n"
        "4. Note any markers that conflict with your final label.\n"
        "Provide the most probable general_type and sub_type consistent with the tissue context."
    )

    user_prompt = (
        f"Top Marker Genes for Cluster {cluster_id}:\n{', '.join(top_markers)}\n\n"
        "Provide reasoning, marker_evidence, general_type, and sub_type."
    )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        if not ENABLE_SELF_CONSISTENCY:
            logger.info(f"[Cluster {cluster_id}] 单轮标注 (ENABLE_SELF_CONSISTENCY=0)")
            chosen = _run_single_annotation(messages, 0.1)
            reasoning_merged = chosen.reasoning_chain
            self_ok = True
        else:
            logger.info(
                f"[Cluster {cluster_id}] 正在拉起 LLM 自一致性投票 ({len(TEMPERATURES)} 次)..."
            )
            results: List[AnnotationOutput] = []
            for temp in TEMPERATURES:
                results.append(_run_single_annotation(messages, temp))

            labels = [_normalize_vote_label(r.sub_type) for r in results]
            unique_labels = set(labels)
            unanimous = len(unique_labels) == 1
            chosen, _ = _majority_pick(results)

            if not unanimous:
                vote_summary = (
                    f"[Vote 0.1] {results[0].sub_type} | [Vote 0.4] {results[1].sub_type} | "
                    f"[Vote 0.7] {results[2].sub_type}. Majority: {chosen.sub_type}."
                )
                reasoning_merged = (
                    f"{vote_summary}\n\n--- Merged reasoning (majority pick) ---\n{chosen.reasoning_chain}"
                )
            else:
                reasoning_merged = chosen.reasoning_chain

            self_ok = unanimous or (
                Counter(labels).most_common(1)[0][1] >= 2
            )  # 至少 2/3 一致视为可接受
            if len(unique_labels) == 3:
                self_ok = False

        ai_response = AIMessage(
            content=(
                f"**Reasoning Chain**:\n{reasoning_merged}\n\n**Decision**:\n"
                f"General Type: {chosen.general_type}\nSub Type: {chosen.sub_type}\n"
                f"**Marker evidence**:\n"
                + "\n".join(f"- {m}" for m in chosen.marker_evidence)
            )
        )

        quality = state.get("quality_scores", {}).copy()
        quality["self_consistency_ok"] = 1.0 if self_ok else 0.0

        return {
            "predictions": {
                "general_type": chosen.general_type,
                "sub_type": chosen.sub_type,
                "reasoning_chain": reasoning_merged,
                "marker_evidence": chosen.marker_evidence,
            },
            "quality_scores": quality,
            "reasoning_messages": [HumanMessage(content=user_prompt), ai_response],
        }

    except Exception as e:
        logger.error(f"[Cluster {cluster_id}] 鉴定过程中发生阻断级异常: {e}")
        return {
            "predictions": {"general_type": "Error", "sub_type": f"Error: {e}"},
            "reasoning_messages": [],
        }
