import logging
from typing import Dict, Any
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from pydantic import BaseModel, Field

from omnicell_agent.schema.state import Annotation_State
from omnicell_agent.core.llm_client import OmniCellLLM

logger = logging.getLogger(__name__)

class AnnotationOutput(BaseModel):
    """强制 LLM 输出的分步推理与鉴定结果契约"""
    reasoning_chain: str = Field(..., description="根据提供的Top Marker基因进行逐步思维链推理（Chain-of-Thought）。首先分析这些基因在哪些功能大类中富集，然后再缩小到具体的细胞亚群。")
    general_type: str = Field(..., description="预测的细胞大类（例如：Immune cells, Epithelial cells, Stromal cells 等）")
    sub_type: str = Field(..., description="极其精确的细胞亚型名字（例如：CD14+ Monocytes, CD8+ T cells 等）")

def annotator_node(state: Annotation_State) -> Dict[str, Any]:
    """
    Sub-Graph B 并发节点: Annotator 
    承接由总图散发下来的单独 Cluster 数据，调用底层大模型进行细胞身份标定。
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
            "reasoning_messages": [AIMessage(content="Error: No marker genes provided for this cluster.")]
        }

    # 1. 组装 CASSIA 风格的 Prompt
    system_prompt = (
        "You are an expert single-cell biologist and a rigorous cell type annotator. "
        f"Your task is to annotate a specific cell cluster from a {species} {tissue} sample.\n"
        "You will be provided with the top differentially expressed marker genes for this cluster.\n"
        "Please follow the Chain-of-Thought (CoT) framework rigorously:\n"
        "1. Observe the markers and identify broad functional signatures (e.g., immune vs. non-immune markers).\n"
        "2. Identify specific celllineage markers if available.\n"
        "3. Provide the most probable 'general_type' and 'sub_type'."
    )
    
    user_prompt = f"Top Marker Genes for Cluster {cluster_id}:\n{', '.join(top_markers)}\n\nPlease provide your reasoning and final cell type prediction."
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
    ]
    
    # 2. 调用模型 (启用结构化输出)
    llm_client = OmniCellLLM(temperature=0.1) # 极低温度以保障学术推理稳定性
    structured_llm = llm_client.model.with_structured_output(AnnotationOutput)
    
    try:
        logger.info(f"[Cluster {cluster_id}] 正在拉起 LLM 分析 {len(top_markers)} 个 Marker...")
        result: AnnotationOutput = structured_llm.invoke(messages)
        
        logger.info(f"[Cluster {cluster_id}] 鉴定完毕 -> {result.sub_type} (大类: {result.general_type})")
        
        # 将大模型发散出的宝贵 CoT 思考落盘追进状态流
        ai_response = AIMessage(content=f"**Reasoning Chain**:\n{result.reasoning_chain}\n\n**Decision**:\nGeneral Type: {result.general_type}\nSub Type: {result.sub_type}")
        
        return {
            "predictions": {
                "general_type": result.general_type,
                "sub_type": result.sub_type
            },
            "reasoning_messages": [HumanMessage(content=user_prompt), ai_response]
        }
        
    except Exception as e:
        logger.error(f"[Cluster {cluster_id}] 鉴定过程中发生阻断级异常: {e}")
        return {
            "predictions": {"general_type": "Error", "sub_type": f"Error: {e}"},
            "reasoning_messages": []
        }
