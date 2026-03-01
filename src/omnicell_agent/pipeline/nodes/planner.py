import logging
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.core.prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

def run_planner(state: DataPipeline_State) -> dict:
    """
    Planner Node
    解析自然语言指令，规划出具体需要执行的单细胞分析步骤。
    对于基础请求，将其拆解成合理的生信流水段返回。
    """
    logger.info("--- NODE: PLANNER ---")
    
    # 抽取历史最新沟通意图
    messages = state.get("messages", [])
    if not messages:
        user_intent = "执行标准单细胞全流程预处理、降维、聚类并寻找 Marker 基因。"
    else:
        user_intent = messages[-1].content
        
    system_prompt = prompt_manager.load_prompt("planner_system.txt")
    
    try:
        # 获取默认配置的大模型 (使用用户的 OneRouter 通道)
        llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.1)
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"用户的原始指令：\n{user_intent}")
        ])
        
        plan_text = response.content
        logger.debug(f"生成的计划：\n{plan_text}")
        
    except Exception as e:
        logger.error(f"Planner 执行失败: {e}")
        plan_text = f"Fallback Plan: 执行基础预处理与 Scanpy 标准工作流。Error: {e}"

    # 只返回差量更新 State
    task_context = state.get("task_context", {})
    task_context["plan"] = plan_text
    
    return {"task_context": task_context}
