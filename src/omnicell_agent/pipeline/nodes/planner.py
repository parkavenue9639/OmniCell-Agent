import logging
import os
import re
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.schema.state import DataPipeline_State, AnalysisPlan
from omnicell_agent.core.prompt_manager import prompt_manager
from omnicell_agent.core.trace_logger import trace_logger

logger = logging.getLogger(__name__)

def _load_skills_metadata() -> str:
    skills_dir = "src/omnicell_agent/skills"
    if not os.path.exists(skills_dir):
        return "No registered skills."
    
    metadata_lines = []
    try:
        for skill_name in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, skill_name, "SKILL.md")
            if os.path.isfile(skill_path):
                with open(skill_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
                    if match:
                        frontmatter = match.group(1)
                        desc_match = re.search(r'^description:\s*(.*)', frontmatter, re.MULTILINE)
                        desc = desc_match.group(1).strip() if desc_match else "无描述"
                        metadata_lines.append(f"- 【{skill_name}】: {desc}")
    except Exception as e:
        logger.error(f"Failed to load skills metadata: {e}")
        
    if not metadata_lines:
        return "No valid skills found."
    return "\n".join(metadata_lines)

def run_planner(state: DataPipeline_State) -> dict:
    """
    基于 Skill-Driven 的 Planner 已经不再是单纯的文本产生器，而是通过 Pydantic 输出任务队列的数据机。
    """
    logger.info("--- NODE: PLANNER (Skill-Driven) ---")
    trace_logger.append_node_start("PLANNER (Skill-Driven)")
    
    user_query = state.get("messages", [])
    if not user_query:
        user_intent = "执行标准单细胞全流程预处理、降维、聚类并寻找 Marker 基因。"
    else:
        user_intent = user_query[-1].content
        
    system_prompt = prompt_manager.load_prompt("planner_system.txt")
    skills_catalog = _load_skills_metadata()
    
    try:
        llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.1)
        # 强制结构化输出
        structured_llm = llm.with_structured_output(AnalysisPlan)
        
        system_content = f"{system_prompt}\n\n【目前已注册的可用官方生信技能 (Skills)】\n{skills_catalog}"
        human_content = f"用户的原始指令：\n{user_intent}"
        
        # 获得 Pydantic 对象
        plan_obj = structured_llm.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=human_content)
        ])
        
        # Json化以落入 State 字典进行可序列化流转
        plan_steps = [step.model_dump() for step in plan_obj.steps]
        
        # 保存轨迹
        trace_logger.append_llm_interaction(
            system_prompt=system_content, 
            human_prompt=human_content, 
            llm_response=str(plan_steps), 
            role_name="Planner_LLM_Structured"
        )
        logger.debug(f"生成的结构化计划步长：{len(plan_steps)} 步")
        
    except Exception as e:
        logger.error(f"Planner 执行结构化分解失败: {e}")
        plan_steps = [{
            "step_type": "skill_call", 
            "skill_name": "pca_clustering",
            "instruction": None,
            "background_context": None
        }]

    task_context = state.get("task_context", {})
    # 彻底弃用 "plan" 文本大盘保留, 而是移交 plan_steps 步进列
    
    return {
        "plan_steps": plan_steps,
        "current_step_index": 0,    # 始终重置步进计数器
        "task_context": task_context
    }

