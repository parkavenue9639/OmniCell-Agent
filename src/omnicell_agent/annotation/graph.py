import logging
from typing import Dict, Any, List

from langgraph.graph import StateGraph, START, END
from langgraph.constants import Send

from omnicell_agent.schema.state import SubGraphB_State, Annotation_State
from omnicell_agent.schema.contract import MarkerTableContract

from omnicell_agent.annotation.nodes.annotator import annotator_node
from omnicell_agent.annotation.nodes.validator import validator_node
from omnicell_agent.annotation.nodes.scorer import scorer_node
from omnicell_agent.annotation.nodes.boost import boost_node
from omnicell_agent.annotation.nodes.reporter import reporter_node
from omnicell_agent.annotation.nodes.consistency_reviewer import consistency_reviewer_node

logger = logging.getLogger(__name__)

TOP_N_MARKERS = 20


def distribute_clusters(state: SubGraphB_State) -> List[Send]:
    """
    Sub-Graph B 的 Map-Reduce 起射器逻辑。
    将一个涵盖所有聚类结果的宏大数据契约，精密切分为独立的细胞簇并发分支。
    """
    contract_path = state.get("contract_file_path", "")
    species = state.get("species", "Human")
    tissue = state.get("tissue", "PBMC")

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
        top_n = [m.gene_name for m in markers[:TOP_N_MARKERS]]

        child_state = Annotation_State(
            cluster_id=cid,
            species=species,
            tissue=tissue,
            top_n_markers=top_n,
            contract_file_path=contract_path,
            reasoning_messages=[],
            predictions={},
            quality_scores={},
            retry_count=0,
        )
        sends.append(Send("process_cluster", child_state))

    logger.info(f"图 B 主干网已建立，成功并发派发 {len(sends)} 个寻址靶向任务！")
    return sends


def post_scorer_route(state: Annotation_State) -> str:
    """Boost 仅允许一次：低分且尚未 Boost 时进入 boost；否则结束微观图。"""
    raw_cs = state.get("quality_scores", {}).get("cs_score", 0.0)
    try:
        cs_score = float(raw_cs)
    except (TypeError, ValueError):
        cs_score = 0.0
    retry_count = int(state.get("retry_count", 0) or 0)

    if cs_score >= 75.0:
        return "end"
    if retry_count < 1:
        return "boost"
    return "end"


def build_single_cluster_graph():
    """微观图：单簇从打标、审核、打分到 Boost 后复审的闭环"""
    builder = StateGraph(Annotation_State)
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


def process_cluster_wrapper(state: Annotation_State) -> Dict[str, Any]:
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

    retry_count = int(final_child.get("retry_count", 0) or 0)
    cs = float(q.get("cs_score", 0.0))
    if retry_count >= 1 and cs < 75.0 and isinstance(preds.get("sub_type"), str):
        st = preds["sub_type"]
        if "(NeedsReview)" not in st:
            preds["sub_type"] = f"{st} (NeedsReview)"

    flags: List[str] = []
    try:
        if float(q.get("self_consistency_ok", 1.0)) < 0.5:
            flags.append("low_self_consistency")
    except (TypeError, ValueError):
        pass
    if isinstance(preds.get("sub_type"), str) and "(Boosted)" in preds["sub_type"]:
        flags.append("boosted")
    if isinstance(preds.get("sub_type"), str) and "(NeedsReview)" in preds["sub_type"]:
        flags.append("needs_review")

    preds["flags"] = flags

    return {"cluster_annotations": {cid: preds}}


def build_annotation_graph():
    """组装 Sub-Graph B：Map -> process_cluster -> consistency -> reporter"""
    builder = StateGraph(SubGraphB_State)

    builder.add_node("process_cluster", process_cluster_wrapper)
    builder.add_node("consistency_reviewer", consistency_reviewer_node)
    builder.add_node("reporter", reporter_node)

    builder.add_conditional_edges(START, distribute_clusters, ["process_cluster"])

    builder.add_edge("process_cluster", "consistency_reviewer")
    builder.add_edge("consistency_reviewer", "reporter")
    builder.add_edge("reporter", END)

    return builder.compile()
