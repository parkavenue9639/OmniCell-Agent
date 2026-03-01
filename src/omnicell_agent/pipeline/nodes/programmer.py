import logging
import re
import os
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.core.prompt_manager import prompt_manager
from omnicell_agent.core.trace_logger import trace_logger

logger = logging.getLogger(__name__)

def extract_python_code(text: str) -> str:
    """提取 markdown 中的 python block"""
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()

def run_programmer(state: DataPipeline_State) -> dict:
    """
    基于 Skill-Driven State Machine 的 Programmer Node
    仅执行 plan_steps[current_step_index] 指向的任务切片。
    """
    logger.info("--- NODE: PROGRAMMER (Cell-by-Cell) ---")
    trace_logger.append_node_start("PROGRAMMER (Cell-by-Cell)")
    
    plan_steps = state.get("plan_steps", [])
    current_index = state.get("current_step_index", 0)
    
    if current_index >= len(plan_steps):
        logger.warning(f"当前 step_index {current_index} 已超出队列长度 {len(plan_steps)}，视为流转结束 (安全锁)。")
        return {"last_generated_code": ""}
        
    current_step = plan_steps[current_index]
    step_type = current_step.get("step_type", "custom_code")
    feedback = state.get("task_context", {}).get("eval_record", {}).get("feedback", "")
    
    raw_data_path = state.get("raw_data_path", "/app/data/sample.h5ad")
    marker_table_path = state.get("marker_table_path", "/app/data/markers.json")
    
    refined_code = ""

    # 情景 1: 如果是 Skill 并且没有发生报错打回 (0 Token)
    if step_type == "skill_call" and not feedback:
        skill_name = current_step.get("skill_name")
        skill_script_path = os.path.join(os.path.dirname(__file__), "..", "..", "skills", skill_name, "scripts", "execute.py")
        if os.path.exists(skill_script_path):
            try:
                with open(skill_script_path, "r", encoding="utf-8") as f:
                    refined_code = f.read()
                logger.info(f"⚡️ 命中 Skill [{skill_name}]，已零 Token 物理提取执行代码。")
            except Exception as e:
                logger.error(f"读取 Skill [{skill_name}] 脚本失败: {e}")
                refined_code = "print('Skill Script Load Error')"
        else:
            logger.error(f"设定的 Skill [{skill_name}] 对应脚本路径不存在！")
            refined_code = "print('Skill Script Missing')"
            
    # 情景 2: Custom Code 或者 Skill 在上一轮报错被打回了，需要大模型修补
    else:
        try:
            llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.0)
            system_content = prompt_manager.load_prompt("programmer_system.txt", raw_data_path=raw_data_path, marker_table_path=marker_table_path)
            
            if step_type == "custom_code":
                instruction = current_step.get("instruction", "无指令")
                bg_ctx = current_step.get("background_context", "无背景信息")
                human_content = f"【单步自定义指令】:\n{instruction}\n\n【全局上下文语义喂补 (防代码断层)】:\n{bg_ctx}"
            else:
                # Skill 代码出错了需要修补
                skill_name = current_step.get("skill_name")
                human_content = f"【紧急修复靶向步长】原先调用的官方 Skill [{skill_name}] 发生报错，请根据后续 Sandbox 的抛出异常推断故障环境，并写出同功能且不报错的代码替代。"
                
            if feedback:
                task_ctx = state.get("task_context", {})
                retries = task_ctx.get("retry_count", 0)
                failed_attempts = task_ctx.get("failed_attempts", [])
                
                logger.warning(f"正在向 Programmer LLM 注入本地回执 (当前单步失败重试: {retries})，执行靶向环境累积除错...")
                
                human_content += "\n\n【⚠️ 历史错误与重试记录】:\n"
                human_content += "您在本步骤的之前几次执行尝试中遇到了以下报错。请务必吸取教训并在本轮代码中**累积修复**，千万不要在修复新错误时退化、覆盖或遗忘之前的修补方案 (Amnesia Regression)！\n"
                
                for i, attempt in enumerate(failed_attempts):
                    human_content += f"\n--- [第 {i+1} 次尝试] ---\n"
                    human_content += f"**您写的故障代码**:\n```python\n{attempt.get('code', '')}\n```\n"
                    human_content += f"**沙盒报错/视觉驳回诊断**:\n{attempt.get('feedback', '')}\n"
                
                human_content += "\n请反思整个失败的演进轨迹，并提供一份综合修复所有已知 bug 的最终可用 Python 代码！\n重点规则：**务必保留前几轮中成功验证过滤过异常的逻辑修补，保证不发生 Regression 退化**。严禁解释，只需输出被 markdown python 框包裹的代码即可。"
                
            response = llm.invoke([
                SystemMessage(content=system_content),
                HumanMessage(content=human_content)
            ])
            raw_output = response.content
            
            trace_logger.append_llm_interaction(
                system_prompt=system_content, 
                human_prompt=human_content, 
                llm_response=raw_output, 
                role_name="Programmer_LLM_Repair_or_Custom"
            )
            refined_code = extract_python_code(raw_output)
            logger.debug(f"由 LLM 闭门生成/或修补的代码切片：\n{refined_code}")
            
        except Exception as e:
            logger.error(f"Programmer 生成/修补代码执行失败: {e}")
            refined_code = "print('Failed to generate code due to Programmer LLM error.')"
            
    return {"last_generated_code": refined_code}
