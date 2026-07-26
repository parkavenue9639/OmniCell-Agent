import operator
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, model_validator

from omnicell_agent.recipes.catalog import (
    RecipeCatalogError,
    load_builtin_recipe_catalog,
)


class PlanStep(BaseModel):
    step_type: Literal["recipe_call", "custom_code"] = Field(
        ...,
        description=(
            "已验证的确定性脚本使用 recipe_call；"
            "只有标准 Recipe 无法覆盖的非标需求使用 custom_code。"
        ),
    )
    recipe_name: Optional[str] = Field(
        None,
        description=(
            "step_type 为 recipe_call 时填写已注册 Recipe 标识，否则留空。"
        ),
    )
    instruction: str = Field(..., description="给 Programmer / 或人类看的本步骤自然语言短口令。例如：'执行 PCA 并将结果绘制保存。'")
    background_context: Optional[str] = Field(
        None,
        description=(
            "custom_code 的必要科学背景，例如建议使用的 scanpy API 和输出格式。"
        ),
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        max_length=16,
        description=(
            "recipe_call 的类型化参数；省略时由 Recipe Registry 注入确定性默认值。"
        ),
    )

    @model_validator(mode="after")
    def validate_recipe_selection(self) -> "PlanStep":
        if self.step_type == "recipe_call" and not self.recipe_name:
            raise ValueError("recipe_call 必须指定 recipe_name")
        if self.step_type == "custom_code" and self.recipe_name is not None:
            raise ValueError("custom_code 不能指定 recipe_name")
        if self.step_type == "custom_code" and self.parameters:
            raise ValueError("custom_code 不能携带 Recipe 参数")
        if self.recipe_name:
            try:
                definition = load_builtin_recipe_catalog().get(
                    self.recipe_name
                )
                self.parameters = definition.normalize_parameters(
                    self.parameters
                )
            except RecipeCatalogError as exc:
                raise ValueError(str(exc)) from exc
        return self

class AnalysisPlan(BaseModel):
    steps: List[PlanStep] = Field(
        ...,
        min_length=1,
        max_length=12,
        description="完成当前探索性目标所需的最少执行步骤，按依赖顺序排列。",
    )


# ==============================================================================
# 探索性分析内部状态
# ==============================================================================
class ExploratoryAnalysisState(TypedDict):
    """
    探索性分析内部的数据到代码生成状态。
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
    
    # Recipe-driven 内部执行游标
    plan_steps: List[Dict[str, Any]]   # 从 Planner 拿到并转化后的 Pydantic Dict 队列
    current_step_index: int            # 当前进行到了第几步
    
    # 代码与沙盒执行隔离记录回执
    last_generated_code: str
    sandbox_execution_result: Dict[str, Any]  # e.g., {"status": "success", "stdout": "...", "stderr": ""}



# ==============================================================================
# 细胞类型注释内部状态
# ==============================================================================

# 以下为单一 Cluster 被 Send API 派发出去后的微观状态
class ClusterAnnotationState(TypedDict):
    """
    处理单一 cluster 注释的细粒度流转状态。
    支持在最高并发场景下各自独立运作。
    """
    # 单独标识符
    cluster_id: str
    species: str
    tissue: str
    
    # 从契约层映射过来的本细胞簇指纹：
    # 第一轮同时保留有界基因名列表和对应定量 DE 证据，避免只凭名称给出高分；
    # 并保留 contract_file_path，以便 Boost 节点按需查询更宽的证据集合。
    top_n_markers: List[str]
    top_marker_evidence: List[str]
    contract_file_path: str           
    
    # 核心推理思维与轨迹记录
    reasoning_messages: Annotated[List[BaseMessage], operator.add]
    
    # 阶段性评判产出标定 (支持多种维度计分与类型分支的自由扩展)
    predictions: Dict[str, Any]       # e.g. general_type/sub_type/reasoning_chain/marker_evidence 等
    quality_scores: Dict[str, Any]  # validator_penalty / cs_score / self_consistency_ok 等
    
    # 循环防护：由于存在如果低分可能打回重新发问 Boost 补图，这个标志可以规避死循环
    retry_count: int


# 注释复合能力的聚合状态
def update_annotation_dict(existing: Dict[str, Any], new_updates: Dict[str, Any]) -> Dict[str, Any]:
    """自定义的状态归并策略：用于将各并发簇的打标结果安全合并到总字典"""
    merged = existing.copy() if existing else {}
    merged.update(new_updates)
    return merged

class CellAnnotationState(TypedDict):
    """
    细胞类型注释内部引擎的主状态。
    接收总档并负责生发单细胞簇鉴定任务。
    """
    # 顶层入口配置
    contract_file_path: str
    species: str
    tissue: str
    
    # 这里用于归集所有底层 ClusterAnnotationState 并发返回的细胞身份，
    # 键为 cluster_id，值为具体的 annotation string 等组合。
    cluster_annotations: Annotated[Dict[str, Any], update_annotation_dict]
    
    # 总成阶段报告生成
    final_report: str
