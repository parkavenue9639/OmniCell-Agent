from langgraph.graph import StateGraph, END
from omnicell_agent.schema.state import ExploratoryAnalysisState
from omnicell_agent.pipeline.nodes.context_resolver import run_context_resolver
from omnicell_agent.pipeline.nodes.planner import run_planner
from omnicell_agent.pipeline.nodes.programmer import run_programmer
from omnicell_agent.pipeline.nodes.executor import run_executor
from omnicell_agent.pipeline.nodes.evaluator import run_evaluator
import logging

logger = logging.getLogger(__name__)

# 最大重试次数
MAX_RETRIES = 3

def route_analysis_evaluation(state: ExploratoryAnalysisState):
    """
    Evaluator 后的条件路由判断
    """
    task_context = state.get("task_context", {})
    eval_record = task_context.get("eval_record", {})
    status = eval_record.get("status")
    
    if status == "success":
        current_index = state.get("current_step_index", 0)
        plan_steps = state.get("plan_steps", [])
        if current_index >= len(plan_steps):
            logger.info("探索性分析路由：所有生信步骤已执行完成。")
            return END
        else:
            logger.info(
                "探索性分析路由：继续执行第 %s/%s 步。",
                current_index + 1,
                len(plan_steps),
            )
            return "programmer"
            
    retries = task_context.get("retry_count", 0)
    if retries >= MAX_RETRIES:
        logger.error("探索性分析达到最大修复次数 %s，终止执行。", MAX_RETRIES)
        return END
        
    logger.info(
        "探索性分析执行未通过评估，进入第 %s/%s 次修复。",
        retries + 1,
        MAX_RETRIES,
    )
    # 放行回 Programmer
    return "programmer"

def build_exploratory_analysis_engine():
    """
    组装探索性分析内部引擎。
    """
    workflow = StateGraph(ExploratoryAnalysisState)

    # 1. 注册节点
    workflow.add_node("context_resolver", run_context_resolver)
    workflow.add_node("planner", run_planner)
    workflow.add_node("programmer", run_programmer)
    workflow.add_node("executor", run_executor)
    workflow.add_node("evaluator", run_evaluator)

    # 2. 定义边 (Edges)
    # 首节点改为 context_resolver：从用户 prompt + h5ad 元数据推断语境，
    # 再将结果通过 task_context 传递给 Planner 及后续能力，
    # 消除对 CLI --species/--tissue 的依赖。
    workflow.set_entry_point("context_resolver")
    workflow.add_edge("context_resolver", "planner")
    workflow.add_edge("planner", "programmer")
    workflow.add_edge("programmer", "executor")
    workflow.add_edge("executor", "evaluator")

    # 3. 定义条件路由边 (Conditional Routing)
    workflow.add_conditional_edges(
        "evaluator",
        route_analysis_evaluation,
        {
            "programmer": "programmer",
            END: END
        }
    )

    # 编译执行图
    app = workflow.compile()
    return app
