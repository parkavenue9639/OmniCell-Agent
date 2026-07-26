import logging
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent import llm
from omnicell_agent.recipes.catalog import load_builtin_recipe_catalog
from omnicell_agent.schema.state import AnalysisPlan, ExploratoryAnalysisState
from omnicell_agent.core.prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

def _load_recipes_metadata() -> str:
    try:
        return load_builtin_recipe_catalog().planner_inventory()
    except Exception as e:
        logger.error("Failed to load Recipe metadata: %s", e)
        return "No valid recipes found."

def run_planner(state: ExploratoryAnalysisState) -> dict:
    """
    探索性分析内部 Planner 通过 Pydantic 输出 Recipe 或 custom code 队列。
    """
    logger.info("--- NODE: PLANNER (Recipe-Driven) ---")
    user_query = state.get("messages", [])
    if not user_query:
        raise ValueError("探索性分析缺少用户目标，未生成执行计划")
    user_intent = str(user_query[-1].content or "").strip()
    if not user_intent:
        raise ValueError("探索性分析的用户目标为空，未生成执行计划")
        
    system_prompt = prompt_manager.load_prompt("planner_system.txt")
    recipe_catalog = _load_recipes_metadata()
    
    try:
        model = llm.get_llm_by_alias(llm.LLMRole.FAST_ROUTER, temperature=0.1)
        # 强制结构化输出
        structured_llm = model.with_structured_output(AnalysisPlan)
        
        system_content = (
            f"{system_prompt}\n\n"
            "【已注册的确定性生信 Recipe】\n"
            f"{recipe_catalog}"
        )
        human_content = f"用户的原始指令：\n{user_intent}"
        
        # 获得 Pydantic 对象
        plan_obj = structured_llm.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=human_content)
        ])
        
        # Json化以落入 State 字典进行可序列化流转
        plan_steps = [step.model_dump() for step in plan_obj.steps]
        
        logger.debug(f"生成的结构化计划步长：{len(plan_steps)} 步")
        
    except Exception as e:
        logger.error("Planner 执行结构化分解失败: %s", e)
        raise RuntimeError(
            "探索性分析规划失败，未执行任何科学步骤"
        ) from e

    task_context = state.get("task_context", {})
    # 彻底弃用 "plan" 文本大盘保留, 而是移交 plan_steps 步进列
    
    return {
        "plan_steps": plan_steps,
        "current_step_index": 0,    # 始终重置步进计数器
        "task_context": task_context
    }
