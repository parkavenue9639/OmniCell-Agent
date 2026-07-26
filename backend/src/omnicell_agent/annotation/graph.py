import logging
from typing import Dict, Any, List

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from omnicell_agent.schema.state import (
    CellAnnotationState,
    ClusterAnnotationState,
)
from omnicell_agent.schema.contract import MarkerTableContract

from omnicell_agent.annotation.nodes.annotator import annotator_node
from omnicell_agent.annotation.nodes.validator import validator_node
from omnicell_agent.annotation.nodes.scorer import scorer_node
from omnicell_agent.annotation.nodes.boost import boost_node
from omnicell_agent.annotation.nodes.reporter import reporter_node
from omnicell_agent.annotation.nodes.consistency_reviewer import consistency_reviewer_node
from omnicell_agent.core.config import ENABLE_CONSISTENCY_REVIEWER, ENABLE_BOOST

logger = logging.getLogger(__name__)

TOP_N_MARKERS = 20


def distribute_clusters(state: CellAnnotationState) -> List[Send]:
    """
    细胞注释内部引擎的 Map-Reduce 分发逻辑。
    将一个涵盖所有聚类结果的宏大数据契约，精密切分为独立的细胞簇并发分支。
    """
    contract_path = state.get("contract_file_path", "")
    species = state.get("species", "Unknown")
    tissue = state.get("tissue", "Unknown")

    try:
        contract = MarkerTableContract.load_from_json(contract_path)
    except Exception as e:
        logger.error(f"无法读取特征合约 {contract_path}: {e}")
        return []

    cluster_markers = {}
    for marker in contract.markers:
        cluster_id = marker.cluster_id
        if cluster_id not in cluster_markers:
            cluster_markers[cluster_id] = []
        cluster_markers[cluster_id].append(marker)

    sends = []
    for cid, markers in cluster_markers.items():
        markers.sort(key=lambda x: x.p_val_adj)
        selected_markers = markers[:TOP_N_MARKERS]
        top_n = [m.gene_name for m in selected_markers]
        top_marker_evidence = [
            (
                f"{m.gene_name}: log2FC={m.log2FC:.3g}, "
                f"pct_in={m.pct_1:.3g}, pct_out={m.pct_2:.3g}, "
                f"delta_pct={(m.pct_1 - m.pct_2):.3g}, "
                f"p_adj={m.p_val_adj:.3g}"
            )
            for m in selected_markers
        ]

        child_state = ClusterAnnotationState(
            cluster_id=cid,
            species=species,
            tissue=tissue,
            top_n_markers=top_n,
            top_marker_evidence=top_marker_evidence,
            contract_file_path=contract_path,
            reasoning_messages=[],
            predictions={},
            quality_scores={},
            retry_count=0,
        )
        sends.append(Send("process_cluster", child_state))

    logger.info("细胞注释已并发派发 %s 个 cluster 任务", len(sends))
    return sends


def post_scorer_route(state: ClusterAnnotationState) -> str:
    """Boost 仅允许一次：低分且尚未 Boost 时进入 boost；否则结束微观图。
    当 ENABLE_BOOST=False 时，跳过 Boost 直接结束。
    """
    raw_cs = state.get("quality_scores", {}).get("cs_score", 0.0)
    try:
        cs_score = float(raw_cs)
    except (TypeError, ValueError):
        cs_score = 0.0
    retry_count = int(state.get("retry_count", 0) or 0)

    if cs_score >= 75.0:
        return "end"
    if not ENABLE_BOOST:
        return "end"
    if retry_count < 1:
        return "boost"
    return "end"


def build_single_cluster_graph():
    """微观图：单簇从打标、审核、打分到 Boost 后复审的闭环"""
    builder = StateGraph(ClusterAnnotationState)
    builder.add_node("annotator", annotator_node)
    builder.add_node("validator", validator_node)
    builder.add_node("scorer", scorer_node)
    builder.add_node("boost", boost_node)

    builder.add_edge(START, "annotator")
    builder.add_edge("annotator", "validator")
    builder.add_edge("validator", "scorer")
    builder.add_conditional_edges(
        "scorer",
        post_scorer_route,
        {
            "end": END,
            "boost": "boost",
        },
    )
    builder.add_edge("boost", "validator")

    return builder.compile()


single_cluster_app = build_single_cluster_graph()


def process_cluster_wrapper(state: ClusterAnnotationState) -> Dict[str, Any]:
    """包装器：调用微观图，并归并母状态关心的结果字典"""
    final_child = single_cluster_app.invoke(state)
    cid = final_child.get("cluster_id")
    preds = dict(final_child.get("predictions") or {})
    q = final_child.get("quality_scores") or {}
    preds["cs_score"] = float(q.get("cs_score", 0.0))
    preds["general_type"] = preds.get("general_type", "Unknown")
    try:
        preds["self_consistency_ok"] = float(q.get("self_consistency_ok", 1.0))
    except (TypeError, ValueError):
        preds["self_consistency_ok"] = 1.0

    cs = float(q.get("cs_score", 0.0))

    flags: List[str] = []
    try:
        if float(q.get("self_consistency_ok", 1.0)) < 0.5:
            flags.append("low_self_consistency")
    except (TypeError, ValueError):
        pass
    if bool(q.get("boost_applied", False)):
        flags.append("boosted")
    if (
        cs < 75.0
        or preds.get("sub_type") in {None, "", "Unknown"}
        or bool(q.get("annotation_failed", False))
    ):
        flags.append("needs_review")

    preds["flags"] = flags

    return {"cluster_annotations": {cid: preds}}


def build_annotation_graph():
    """组装细胞注释内部引擎。"""
    builder = StateGraph(CellAnnotationState)

    builder.add_node("process_cluster", process_cluster_wrapper)
    builder.add_node("consistency_reviewer", consistency_reviewer_node)
    builder.add_node("reporter", reporter_node)

    builder.add_conditional_edges(START, distribute_clusters, ["process_cluster"])

    builder.add_conditional_edges(
        "process_cluster",
        lambda _s: "consistency_reviewer" if ENABLE_CONSISTENCY_REVIEWER else "reporter",
        {
            "consistency_reviewer": "consistency_reviewer",
            "reporter": "reporter",
        },
    )
    builder.add_edge("consistency_reviewer", "reporter")
    builder.add_edge("reporter", END)

    return builder.compile()
