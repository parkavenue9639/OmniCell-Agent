import operator
from typing import Annotated, TypedDict, List, Dict, Any
from langchain_core.messages import BaseMessage


# ==============================================================================
# Sub-Graph A: Data Pipeline State
# ==============================================================================
class DataPipeline_State(TypedDict):
    """
    Sub-Graph A 专门负责数据到代码生成的全链路串流。
    核心原则：不要在 State 内存中存储 .h5ad 及衍生的任何 AnnData 等重度矩阵对象，
    仅存储文件路径。
    """
    # 核心资产指针
    raw_data_path: str                 # 目标 .h5ad 本地路径
    marker_table_path: str             # 预期的 / 生成的 Marker JSON 契约导出路径
    
    # 交互与推理堆栈：利用 LangGraph 的标准的对话堆叠器 (Add Reducer)
    messages: Annotated[List[BaseMessage], operator.add]
    
    # 动态控制与沙盘流转级上下文
    # 为了避免后续如果扩展算法导致需要增加诸如 "n_pca", "resolution" 等导致形参爆炸, 统一塞入此槽位
    task_context: Dict[str, Any]       
    
    # 代码与沙盒执行隔离记录回执
    last_generated_code: str
    sandbox_execution_result: Dict[str, Any]  # e.g., {"status": "success", "stdout": "...", "stderr": ""}



# ==============================================================================
# Sub-Graph B: Deep Annotation State
# ==============================================================================
class Annotation_State(TypedDict):
    """
    Sub-Graph B 当中处理**单一簇(cluster)**的细粒度流转状态。
    支持在最高并发场景下各自独立运作。
    """
    # 单独标识符
    cluster_id: str
    species: str
    tissue: str
    
    # 从契约层映射过来的本细胞簇指纹：
    # 尽量不加载所有的 marker(如数万行)，而是挂载 top_n_markers(list) 加速第一轮 LLM 的 token 理解，
    # 并保留 contract_file_path，以便在 Boost 节点需要查询全谱系时供节点现场 I/O 获取。
    top_n_markers: List[str]          
    contract_file_path: str           
    
    # 核心推理思维与轨迹记录
    reasoning_messages: Annotated[List[BaseMessage], operator.add]
    
    # 阶段性评判产出标定 (支持多种维度计分与类型分支的自由扩展)
    predictions: Dict[str, str]       # e.g., {"general_type": "T cell", "sub_type": "CD4+ T cell"}
    quality_scores: Dict[str, float]  # e.g., {"overall": 85.0, "hallmark_consistency": 90.0}
    
    # 循环防护：由于存在如果低分可能打回重新发问 Boost 补图，这个标志可以规避死循环
    retry_count: int
