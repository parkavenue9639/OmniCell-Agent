import logging
from collections import Counter
from typing import Dict, Any, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from pydantic import BaseModel, Field

from omnicell_agent.schema.state import ClusterAnnotationState
from omnicell_agent import llm
from omnicell_agent.core.config import ENABLE_SELF_CONSISTENCY

logger = logging.getLogger(__name__)

TEMPERATURES = (0.1, 0.4, 0.7)


class AnnotationOutput(BaseModel):
    """LLM 输出的有界证据评估与候选注释契约。"""

    reasoning_chain: str = Field(
        ...,
        min_length=1,
        max_length=2_000,
        description=(
            "简洁的证据评估摘要，说明主要支持、冲突和不确定性；"
            "不得输出隐藏的逐步思维链。"
        ),
    )
    general_type: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="证据支持的细胞大类；证据不足时使用 Unknown。",
    )
    sub_type: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "不超过 marker 证据分辨率的候选亚型；证据不足时使用更宽标签或 Unknown。"
        ),
    )
    marker_evidence: List[str] = Field(
        ...,
        max_length=30,
        description="逐条列出 marker panel 对候选标签的支持、冲突或非特异性证据。",
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


def annotator_node(state: ClusterAnnotationState) -> Dict[str, Any]:
    """
    细胞注释内部并发节点：Annotator。
    三温度自一致性投票 + marker 证据锚定。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    top_markers = state.get("top_n_markers", [])
    quantitative_markers = state.get("top_marker_evidence", [])
    species = state.get("species", "Unknown")
    tissue = state.get("tissue", "Unknown")

    logger.info(f"--- NODE: ANNOTATOR (Cluster {cluster_id}) ---")

    if not top_markers or not quantitative_markers:
        logger.warning(
            "[Cluster %s] 缺少完整 Marker 定量证据，无法鉴定。",
            cluster_id,
        )
        return {
            "predictions": {"general_type": "Unknown", "sub_type": "Unknown"},
            "quality_scores": {"annotation_failed": True},
            "reasoning_messages": [
                AIMessage(
                    content=(
                        "Annotation unavailable: the bounded quantitative marker "
                        "evidence is missing."
                    )
                )
            ],
        }

    system_prompt = (
        "You are a rigorous single-cell cluster annotator. "
        f"The supplied sample context is species={species}, tissue={tissue}; "
        "treat this context as supporting information, not proof of identity.\n"
        "Use only the provided differential-expression marker panel. Interpret adjusted significance, "
        "effect size, within-cluster expression, out-of-cluster expression, and delta_pct together; "
        "no single metric proves identity. Assess coherent lineage support, shared or non-specific "
        "markers, conflicting evidence, and whether the requested label granularity is justified. "
        "A missing marker in this top list is not definitive absence.\n"
        "Return a concise evidence assessment, marker_evidence entries that explicitly identify "
        "support or conflict, and candidate general_type/sub_type. Use a broader lineage or Unknown "
        "when the panel is insufficient, mixed, or compatible with multiple labels. Do not output "
        "hidden step-by-step reasoning and do not claim the label is verified."
    )

    user_prompt = (
        f"Bounded quantitative DE marker panel for Cluster {cluster_id}:\n"
        + "\n".join(f"- {marker}" for marker in quantitative_markers)
        + "\n\n"
        "Return evidence assessment, marker_evidence, general_type, and sub_type."
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
                    f"{vote_summary}\n\nEvidence assessment from majority label:\n"
                    f"{chosen.reasoning_chain}"
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
                f"**Evidence assessment**:\n{reasoning_merged}\n\n**Candidate annotation**:\n"
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
        logger.error(
            "[Cluster %s] 注释模型调用失败，本 cluster 返回 Unknown",
            cluster_id,
            exc_info=e,
        )
        return {
            "predictions": {
                "general_type": "Unknown",
                "sub_type": "Unknown",
                "reasoning_chain": "Annotation model did not return a valid evidence assessment.",
                "marker_evidence": [],
            },
            "quality_scores": {
                "self_consistency_ok": 0.0,
                "annotation_failed": True,
            },
            "reasoning_messages": [],
        }
