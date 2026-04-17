import os
import sys
import logging
import argparse
import datetime
from typing import TypedDict, Annotated, List, Dict, Any
import operator

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

# Ensure the src directory is in sys.path
src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if src_root not in sys.path:
    sys.path.insert(0, src_root)

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))

from omnicell_agent.pipeline.graph import build_pipeline_graph
from omnicell_agent.annotation.graph import build_annotation_graph
from omnicell_agent.schema.state import update_annotation_dict

# 建立全局日志落盘机制
log_dir = os.path.join(project_root, "logs")
os.makedirs(log_dir, exist_ok=True)
current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(log_dir, f"e2e_run_{current_time}.log")

# 配置根记录器，执行双轨输出 (Console + File)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ==========================================
# 母系统集成状态树 (Unified Master State)
# 包含 Sub-Graph A 和 Sub-Graph B 所需的所有键值
# ==========================================
class OmniCell_Agent_State(TypedDict):
    # --- Sub-Graph A (Pipeline) ---
    raw_data_path: str
    marker_table_path: str
    messages: Annotated[List[BaseMessage], operator.add]
    task_context: Dict[str, Any]
    plan_steps: List[Dict[str, Any]]
    current_step_index: int
    last_generated_code: str
    sandbox_execution_result: Dict[str, Any]
    
    # --- Sub-Graph B (Annotation) ---
    contract_file_path: str
    species: str
    tissue: str
    cluster_annotations: Annotated[Dict[str, Any], update_annotation_dict]
    final_report: str


def bridge_state_node(state: OmniCell_Agent_State) -> Dict[str, Any]:
    """
    Sub-Graph A -> Sub-Graph B 的状态物理转换中继节点。
    1) 将沙盒路径 /app/data 映射回宿主机绝对路径。
    2) 将 Graph A 中 context_resolver 推断得到的 species/tissue 提升到母图顶层，
       供 Sub-Graph B 使用；若顶层已有显式 override 则保持原值不被覆盖。
    """
    sandbox_path = state.get("marker_table_path", "")
    host_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data"))
    filename = os.path.basename(sandbox_path)
    host_path = os.path.join(host_data_dir, filename)
    
    logger.info(f"--- BRIDGE: 载入特征契约源 [沙盒 {sandbox_path} -> 物理 {host_path}] ---")
    
    if not os.path.exists(host_path):
        logger.error(f"严重错误：沙盒疑似执行失败或无数据溢出。桥接节点无法寻址 {host_path}！")

    updates: Dict[str, Any] = {"contract_file_path": host_path}

    resolved = (state.get("task_context", {}) or {}).get("resolved_context") or {}
    current_species = (state.get("species") or "").strip()
    current_tissue = (state.get("tissue") or "").strip()

    if resolved:
        inferred_species = (resolved.get("species") or "").strip()
        inferred_tissue = (resolved.get("tissue") or "").strip()

        if not current_species and inferred_species:
            updates["species"] = inferred_species
        if not current_tissue and inferred_tissue:
            updates["tissue"] = inferred_tissue

        logger.info(
            "--- BRIDGE: 组织语境注入 [species=%s | tissue=%s | goal=%s] ---",
            updates.get("species", current_species or "Unknown"),
            updates.get("tissue", current_tissue or "Unknown"),
            resolved.get("goal_type", "general_annotation"),
        )
    else:
        logger.warning("--- BRIDGE: 未在 task_context 中发现 resolved_context，Graph B 将使用已有/默认 species/tissue ---")

    return updates


from omnicell_agent.pipeline.nodes.summarizer import final_summarizer_node

def build_master_graph():
    """将双子图利用 LangGraph 的 Native Subgraph 机制拼接汇总"""
    builder = StateGraph(OmniCell_Agent_State)
    
    # 抽取子图 CompiledGraph 作为节点
    app_a = build_pipeline_graph()
    app_b = build_annotation_graph()
    
    builder.add_node("pipeline_subgraph_a", app_a)
    builder.add_node("bridge_transition", bridge_state_node)
    builder.add_node("annotation_subgraph_b", app_b)
    builder.add_node("final_summarizer", final_summarizer_node)
    
    # 构建物理联通干线
    builder.add_edge(START, "pipeline_subgraph_a")
    builder.add_edge("pipeline_subgraph_a", "bridge_transition")
    builder.add_edge("bridge_transition", "annotation_subgraph_b")
    builder.add_edge("annotation_subgraph_b", "final_summarizer")
    builder.add_edge("final_summarizer", END)
    
    return builder.compile()


DEFAULT_SANDBOX_DATA_PATH = "/app/data/pbmc3k_raw.h5ad"
DEFAULT_SANDBOX_MARKERS_NAME = "markers.json"


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    CLI 只暴露两项用户级参数：
      --data        待分析的 .h5ad 数据路径（支持沙盒路径 /app/data/... 或宿主路径）
      --instruction 自然语言任务指令（传给图 A 的统管口令）

    其余原先作为 CLI 暴露的 species/tissue/out-markers 已内化为系统推断或约定默认值，
    保留为不在帮助主视图中的高级覆写项（--override-*），仅供调试与专家回归使用。
    """
    parser = argparse.ArgumentParser(
        description=(
            "OmniCell-Agent 端到端主入口。理想入参仅有：--data + --instruction。"
            "物种、组织等语境由 Graph A 的 ContextResolver 从 prompt 与 h5ad 元数据自动推断。"
        )
    )
    parser.add_argument(
        "--data",
        type=str,
        default=DEFAULT_SANDBOX_DATA_PATH,
        help="待分析 .h5ad 数据路径（默认: %(default)s）",
    )
    parser.add_argument(
        "--instruction",
        type=str,
        required=True,
        help="给 Agent 的自然语言任务指令",
    )

    advanced = parser.add_argument_group("高级覆写（一般不使用）")
    advanced.add_argument(
        "--override-species",
        type=str,
        default="",
        help=argparse.SUPPRESS,
    )
    advanced.add_argument(
        "--override-tissue",
        type=str,
        default="",
        help=argparse.SUPPRESS,
    )
    advanced.add_argument(
        "--override-out-markers",
        type=str,
        default=DEFAULT_SANDBOX_MARKERS_NAME,
        help=argparse.SUPPRESS,
    )
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()
    
    logger.info("="*80)
    logger.info("   🚀 OmniCell-Agent Native Subgraph E2E Execution Started   ")
    logger.info("="*80)
    
    master_app = build_master_graph()
    sandbox_marker_out = f"/app/data/{args.override_out_markers}"
    instruction_with_contract = (
        f"{args.instruction}\n\n"
        f"[SYSTEM INSTRUCTION: Please strictly ensure that the final step exports the "
        f"marker genes as a standardized JSON array to the path: {sandbox_marker_out}]"
    )
    
    # 构筑全局顶层初始化状态：species/tissue 默认为空，由 ContextResolver 推断后经 Bridge 注入。
    initial_state = OmniCell_Agent_State(
        raw_data_path=args.data,
        marker_table_path=sandbox_marker_out,
        messages=[HumanMessage(content=instruction_with_contract)],
        task_context={},
        plan_steps=[],
        current_step_index=0,
        last_generated_code="",
        sandbox_execution_result={},

        contract_file_path="",
        species=args.override_species,
        tissue=args.override_tissue,
        cluster_annotations={},
        final_report=""
    )
    
    try:
        final_state = master_app.invoke(initial_state)
        logger.info("\n========== 全局图收敛：端到端大穿透执行测试成功结束！ ==========")
    except Exception as e:
        logger.error(f"全局网络执行遭遇严重奔溃: {e}")
        raise