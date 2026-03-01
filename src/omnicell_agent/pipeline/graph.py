from langgraph.graph import StateGraph, END
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.pipeline.nodes.planner import run_planner
from omnicell_agent.pipeline.nodes.programmer import run_programmer
from omnicell_agent.pipeline.nodes.executor import run_executor
from omnicell_agent.pipeline.nodes.evaluator import run_evaluator
from omnicell_agent.core.trace_logger import trace_logger
import logging

logger = logging.getLogger(__name__)

# 最大重试次数
MAX_RETRIES = 3

def route_evaluation(state: DataPipeline_State):
    """
    Evaluator 后的条件路由判断
    """
    task_context = state.get("task_context", {})
    eval_record = task_context.get("eval_record", {})
    status = eval_record.get("status")
    
    if status == "success":
        logger.info("Graph A 路由: 评估通过，结束管线。")
        trace_logger.append_pipeline_end("SUCCESS", max_retries_hit=False)
        return END
        
    retries = task_context.get("retry_count", 0)
    if retries >= MAX_RETRIES:
        logger.error(f"Graph A 路由: 达到最大重试次数 ({MAX_RETRIES})，强行结束管线。")
        trace_logger.append_pipeline_end("ABORTED_MAX_RETRIES", max_retries_hit=True)
        return END
        
    logger.info(f"Graph A 路由: 代码执行失败，打回 Programmer 重写。当前重试次数: {retries + 1}/{MAX_RETRIES}")
    # 放行回 Programmer
    return "programmer"

def build_pipeline_graph():
    """
    组装 Sub-Graph A (Data Pipeline) 完整的有向无环图
    """
    workflow = StateGraph(DataPipeline_State)

    # 1. 注册节点
    workflow.add_node("planner", run_planner)
    workflow.add_node("programmer", run_programmer)
    workflow.add_node("executor", run_executor)
    workflow.add_node("evaluator", run_evaluator)

    # 2. 定义边 (Edges)
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "programmer")
    workflow.add_edge("programmer", "executor")
    workflow.add_edge("executor", "evaluator")

    # 3. 定义条件路由边 (Conditional Routing)
    workflow.add_conditional_edges(
        "evaluator",
        route_evaluation,
        {
            "programmer": "programmer",
            END: END
        }
    )

    # 编译执行图
    app = workflow.compile()
    return app
