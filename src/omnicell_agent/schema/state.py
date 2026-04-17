import operator
from typing import Annotated, TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

class PlanStep(BaseModel):
    step_type: str = Field(..., description="指令类型：如果是已知官方库自带技能则填 'skill_call'；如果是需要让 Programmer 自己手搓的非标能力甚至未知需求则填 'custom_code'。")
    skill_name: Optional[str] = Field(None, description="如果 step_type 是 'skill_call'，必须填入命中技能的具体英文标识（从元数据列表中选择）。否则留空。")
    instruction: str = Field(..., description="给 Programmer / 或人类看的本步骤自然语言短口令。例如：'执行 PCA 并将结果绘制保存。'")
    background_context: Optional[str] = Field(None, description="如果是 custom_code，请尽可能提供一些由于没有技能脚本而导致的上下文缺失信息（如：建议他调用 scanpy 的什么函数、需要关注什么格式等防爆补充）。")

class AnalysisPlan(BaseModel):
    steps: List[PlanStep] = Field(..., description="解析重组后的拆分执行指令列表，按细胞步进式执行流顺序排列。")


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
    
    # Skill-Driven Pipeline 的循环步游标引擎
    plan_steps: List[Dict[str, Any]]   # 从 Planner 拿到并转化后的 Pydantic Dict 队列
    current_step_index: int            # 当前进行到了第几步
    
    # 代码与沙盒执行隔离记录回执
    last_generated_code: str
    sandbox_execution_result: Dict[str, Any]  # e.g., {"status": "success", "stdout": "...", "stderr": ""}



# ==============================================================================
# Sub-Graph B: Deep Annotation State
# ==============================================================================

# 以下为单一 Cluster 被 Send API 派发出去后的微观状态
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
    predictions: Dict[str, Any]       # e.g. general_type/sub_type/reasoning_chain/marker_evidence 等
    quality_scores: Dict[str, Any]  # validator_penalty / cs_score / self_consistency_ok 等
    
    # 循环防护：由于存在如果低分可能打回重新发问 Boost 补图，这个标志可以规避死循环
    retry_count: int


# 以下为子图 B 作为整体被外部调用时的宏观状态
def update_annotation_dict(existing: Dict[str, Any], new_updates: Dict[str, Any]) -> Dict[str, Any]:
    """自定义的状态归并策略：用于将各并发簇的打标结果安全合并到总字典"""
    merged = existing.copy() if existing else {}
    merged.update(new_updates)
    return merged

class SubGraphB_State(TypedDict):
    """
    Sub-Graph B 的主状态树。
    接收总档并负责生发单细胞簇鉴定任务。
    """
    # 顶层入口配置
    contract_file_path: str
    species: str
    tissue: str
    
    # 这里用于归集所有底层 Annotation_State 散播出去后最终收敛返回的细胞身份，
    # 键为 cluster_id，值为具体的 annotation string 等组合。
    cluster_annotations: Annotated[Dict[str, Any], update_annotation_dict]
    
    # 总成阶段报告生成
    final_report: str
