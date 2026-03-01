import logging
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.core.prompt_manager import prompt_manager
from omnicell_agent.core.trace_logger import trace_logger

logger = logging.getLogger(__name__)

def run_planner(state: DataPipeline_State) -> dict:
    """
    planner 会通过理解用户的需求以及当前任务的上下文（如果后续有记忆或图谱，也可以在此处接入），
    生成一份高度技术化与模块化的生信代码规划。
    """
    logger.info("--- NODE: PLANNER ---")
    trace_logger.append_node_start("PLANNER")
    
    # 获取用户的 query
    user_query = state.get("messages", [])
    if not user_query:
        user_intent = "执行标准单细胞全流程预处理、降维、聚类并寻找 Marker 基因。"
    else:
        user_intent = user_query[-1].content
        
    system_prompt = prompt_manager.load_prompt("planner_system.txt")
    
    try:
        # 获取默认配置的大模型 (使用用户的 OneRouter 通道)
        llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.1)
        
        system_content = system_prompt
        human_content = f"用户的原始指令：\n{user_intent}"
        
        response = llm.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=human_content)
        ])
        
        raw_plan = response.content
        # 保存轨迹
        trace_logger.append_llm_interaction(
            system_prompt=system_content, 
            human_prompt=human_content, 
            llm_response=raw_plan, 
            role_name="Planner_LLM"
        )
        
        plan_text = raw_plan
        logger.debug(f"生成的计划：\n{plan_text}")
        
    except Exception as e:
        logger.error(f"Planner 执行失败: {e}")
        plan_text = f"Fallback Plan: 执行基础预处理与 Scanpy 标准工作流。Error: {e}"

    # 只返回差量更新 State
    task_context = state.get("task_context", {})
    task_context["plan"] = plan_text
    
    return {"task_context": task_context}
