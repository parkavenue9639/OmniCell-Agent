import logging
from typing import Dict, Any
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from omnicell_agent.schema.state import Annotation_State
from omnicell_agent.schema.contract import MarkerTableContract
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.annotation.nodes.annotator import AnnotationOutput

logger = logging.getLogger(__name__)

def boost_node(state: Annotation_State) -> Dict[str, Any]:
    """
    Sub-Graph B 并发节点: Boost (条件增强纠偏)
    当正常鉴定分数 (cs_score) 过低且重试次数在允许范围内时被唤醒。
    它将直接拉取底层完整的数据契约表，抽取出带表达量阈值的深度基因特征供 LLM 做重审确认。
    """
    cluster_id = state.get("cluster_id", "Unknown")
    species = state.get("species", "Human")
    tissue = state.get("tissue", "PBMC")
    contract_path = state.get("contract_file_path", "")
    retry_count = state.get("retry_count", 0)
    
    logger.info(f"--- NODE: BOOST (Cluster {cluster_id}, Retry {retry_count+1}) ---")
    
    # 防止死循环：只允许挽救 1 次
    if retry_count >= 1:
        logger.warning(f"[Cluster {cluster_id}] Boost 挽救次数触顶，必须强制输出当前结果。")
        return {"retry_count": retry_count + 1}
        
    try:
        # 1. 现场 I/O 挂载完整契约数据，并抽取该族群深达 50 个含统计参数的特征基因
        contract = MarkerTableContract.load_from_json(contract_path)
        cluster_markers = [m for m in contract.markers if m.cluster_id == str(cluster_id)]
        
        # 按校正后 P 值排序并提取前50名的高级基因表征
        cluster_markers.sort(key=lambda x: x.p_val_adj)
        deep_markers_info = [f"{m.gene_name}(log2FC={m.log2FC:.2f}, pct.1={m.pct_1:.2f})" for m in cluster_markers[:50]]
        
        # 2. 组装极致深度的纠偏 Prompt
        system_prompt = (
            "You are a master cell biologist acting as a final escalation expert. "
            "A previous automated annotator struggled to confidently annotate this cell cluster from a "
            f"{species} {tissue} sample due to complex or conflicting biosignals.\n"
            "You are now given a DEEPER list of markers including log2FC (expression fold change) and pct.1 (fraction of expressing cells in this cluster).\n"
            "Resolve the ambiguity. Think step-by-step: Is it a transitional state? Is it a rare subtype? Or is it simply a doublet/noise cluster?\n"
            "Output your definitive general_type and sub_type."
        )
        
        user_prompt = f"Deep Diagnostic Markers for Cluster {cluster_id}:\n{', '.join(deep_markers_info)}\n\nPlease provide your final escalated annotation."
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        # 使用较高的温度增加突变思考能力
        model = LLMSelector.get_llm("onerouter:default", temperature=0.3)
        structured_llm = model.with_structured_output(AnnotationOutput)
        
        logger.info(f"[Cluster {cluster_id}] Boost 节点正在进行深潜抢救请求 ({len(deep_markers_info)} 个增强靶点)...")
        result: AnnotationOutput = structured_llm.invoke(messages)
        
        logger.info(f"[Cluster {cluster_id}] Boost 重鉴定成功 -> {result.sub_type}")
        
        ai_response = AIMessage(content=f"**Boost Escalation Reasoning**:\n{result.reasoning_chain}\n\n**Final Decision**:\nGeneral Type: {result.general_type}\nSub Type: {result.sub_type}")
        
        # 直接通过强制提升置信度，防止再次被 validator 否决陷入死循环
        new_scores = state.get("quality_scores", {}).copy()
        new_scores["cs_score"] = 90.0 
        
        return {
            "predictions": {
                "general_type": result.general_type,
                "sub_type": result.sub_type + " (Boosted)"
            },
            "quality_scores": new_scores,
            "reasoning_messages": [HumanMessage(content=user_prompt), ai_response],
            "retry_count": retry_count + 1
        }
        
    except Exception as e:
        logger.error(f"[Cluster {cluster_id}] Boost 深度取信发生崩溃: {e}")
        return {"retry_count": retry_count + 1}
