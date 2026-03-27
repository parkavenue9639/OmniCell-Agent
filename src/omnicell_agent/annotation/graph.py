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

logger = logging.getLogger(__name__)

def distribute_clusters(state: SubGraphB_State) -> List[Send]:
    """
    Sub-Graph B 的 Map-Reduce 起射器逻辑。
    将一个涵盖所有聚类结果的宏大数据契约，精密切分为几百个独立的细胞簇并发分支，
    然后使用 Send API 同步起爆所有 annotator_node 的异步推理。
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
        # Top 10 个作为初筛快速通过 LLM 处理
        markers.sort(key=lambda x: x.p_val_adj)
        top_n = [m.gene_name for m in markers[:10]]
        
        child_state = Annotation_State(
            cluster_id=cid,
            species=species,
            tissue=tissue,
            top_n_markers=top_n,
            contract_file_path=contract_path,
            reasoning_messages=[],
            predictions={},
            quality_scores={},
            retry_count=0
        )
        sends.append(Send("annotator", child_state))
        
    logger.info(f"图 B 主干网已建立，成功并发派发 {len(sends)} 个寻址靶向任务！")
    return sends

def validate_boost_condition(state: Annotation_State) -> str:
    """边缘路由：判断当前并发小支流是否需要触底捞回"""
    cs_score = state.get("quality_scores", {}).get("cs_score", 0.0)
    retry_count = state.get("retry_count", 0)
    
    # 门栏设定：低于 75 分即代表存在大模型幻觉模糊争议
    if cs_score < 75.0 and retry_count < 1:
        return "boost"
    return "pack_result"

def pack_result_node(state: Annotation_State) -> Dict[str, Any]:
    """
    微观并发流终点站：负责将单独 Cluster 的最终判断收集缩列，交送给上一层的 Reducer (update_annotation_dict)
    """
    cid = state.get("cluster_id")
    preds = state.get("predictions", {})
    preds["cs_score"] = state.get("quality_scores", {}).get("cs_score", 0.0)
    return {"cluster_annotations": {cid: preds}}

def build_annotation_graph():
    """组装并全态暴露 Sub-Graph B，面向大工程接线"""
    # LangGraph 对于 Send 映射的设计：母状态管理宏观，子节点接收微观载体
    builder = StateGraph(SubGraphB_State)
    
    # 注册所有的异步并发节
    builder.add_node("annotator", annotator_node)
    builder.add_node("validator", validator_node)
    builder.add_node("scorer", scorer_node)
    builder.add_node("boost", boost_node)
    
    # 【Map/并发】起射发令台
    builder.add_conditional_edges(START, distribute_clusters, ["annotator"])
    
    # 【串行】大模型审理链 (每个支流内各自跑)
    builder.add_edge("annotator", "validator")
    builder.add_edge("validator", "scorer")
    
    # 【条件增强】
    builder.add_conditional_edges("scorer", validate_boost_condition, {
        "boost": "boost",
        "pack_result": "pack_result"
    })
    
    # Boost 挽救失败或成功后直接装箱
    builder.add_edge("boost", "pack_result")
    
    # 注册装箱搬运工和总管 Reporter 节点
    builder.add_node("pack_result", pack_result_node)
    builder.add_node("reporter", reporter_node)
    
    # 【Reduce/归集】汇总图
    builder.add_edge("pack_result", "reporter")
    builder.add_edge("reporter", END)

    return builder.compile()
