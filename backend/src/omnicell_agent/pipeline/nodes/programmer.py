import logging
import re
import os
from pathlib import PurePosixPath
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent import llm
from omnicell_agent.core.config import project_root
from omnicell_agent.recipes.catalog import (
    RecipeCatalogError,
    load_builtin_recipe_catalog,
)
from omnicell_agent.schema.state import ExploratoryAnalysisState
from omnicell_agent.core.prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

_HOST_DATA_DIR = str(project_root / "data")


def _to_sandbox_path(path: str) -> str:
    """将宿主机路径统一转换为容器内 /app/data/ 路径。"""
    if not path or path == "/app/data" or path.startswith("/app/data/"):
        return path
    abs_path = os.path.abspath(path)
    if os.path.commonpath((abs_path, _HOST_DATA_DIR)) == _HOST_DATA_DIR:
        rel = os.path.relpath(abs_path, _HOST_DATA_DIR)
        return f"/app/data/{rel}"
    if path.startswith("data/"):
        return "/app/data/" + path[5:]
    return path


def _require_sandbox_file_path(
    state: ExploratoryAnalysisState,
    field_name: str,
) -> str:
    """Resolve one explicit invocation path and reject absent/escaping state."""

    value = state.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"探索性分析缺少权威 {field_name}，未生成或执行代码"
        )
    sandbox_path = _to_sandbox_path(value.strip())
    parsed = PurePosixPath(sandbox_path)
    if (
        not sandbox_path.startswith("/app/data/")
        or ".." in parsed.parts
        or parsed.name in {"", ".", ".."}
    ):
        raise ValueError(
            f"探索性分析 {field_name} 不在当前 sandbox data 边界内"
        )
    return sandbox_path


def extract_python_code(text: str) -> str:
    """提取 markdown 中的 python block"""
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()

def run_programmer(state: ExploratoryAnalysisState) -> dict:
    """
    基于 Recipe-driven state machine 的 Programmer Node。
    仅执行 plan_steps[current_step_index] 指向的任务切片。
    """
    logger.info("--- NODE: PROGRAMMER (Cell-by-Cell) ---")
    plan_steps = state.get("plan_steps", [])
    current_index = state.get("current_step_index", 0)
    
    if current_index >= len(plan_steps):
        logger.warning(f"当前 step_index {current_index} 已超出队列长度 {len(plan_steps)}，视为流转结束 (安全锁)。")
        return {"last_generated_code": ""}
        
    current_step = plan_steps[current_index]
    step_type = current_step.get("step_type", "custom_code")
    feedback = state.get("task_context", {}).get("eval_record", {}).get("feedback", "")
    
    raw_data_path = _require_sandbox_file_path(state, "raw_data_path")
    marker_table_path = _require_sandbox_file_path(
        state,
        "marker_table_path",
    )
    artifact_output_root = marker_table_path.rsplit("/", 1)[0]
    
    refined_code = ""

    # 情景 1：确定性 Recipe 首次执行，直接读取受版本控制的脚本。
    if step_type == "recipe_call" and not feedback:
        recipe_name = current_step.get("recipe_name")
        try:
            definition = load_builtin_recipe_catalog().get(str(recipe_name))
            refined_code = definition.script_path.read_text(encoding="utf-8")
            logger.info("命中 Recipe [%s]，直接加载确定性脚本。", recipe_name)
        except (OSError, RecipeCatalogError) as e:
            logger.error("读取 Recipe [%s] 脚本失败: %s", recipe_name, e)
            raise RuntimeError(
                f"无法加载确定性 Recipe：{recipe_name}"
            ) from e
            
    # 情景 2：Custom Code 或 Recipe 在上一轮失败后需要模型修补。
    else:
        try:
            model = llm.get_llm_by_alias(
                llm.LLMRole.CODE_GENERATION,
                temperature=0.0,
            )
            system_content = prompt_manager.load_prompt(
                "programmer_system.txt",
                raw_data_path=raw_data_path,
                marker_table_path=marker_table_path,
                artifact_output_root=artifact_output_root,
            )
            
            if step_type == "custom_code":
                instruction = current_step.get("instruction", "无指令")
                bg_ctx = current_step.get("background_context", "无背景信息")
                human_content = (
                    f"当前单步指令：\n{instruction}\n\n"
                    f"必要科学背景与验收条件：\n{bg_ctx}"
                )
            else:
                recipe_name = current_step.get("recipe_name")
                human_content = (
                    f"确定性 Recipe `{recipe_name}` 的当前步骤执行失败。"
                    "根据下面的受控错误回执修复同一科学操作，不改变步骤目标或输出契约。"
                )
                
            if feedback:
                task_ctx = state.get("task_context", {})
                retries = task_ctx.get("retry_count", 0)
                failed_attempts = task_ctx.get("failed_attempts", [])
                
                logger.warning(
                    "向 Programmer 注入受控错误回执（当前步骤重试 %s）",
                    retries,
                )
                
                human_content += "\n\n本步骤的历史失败记录：\n"
                human_content += (
                    "综合修复所有仍相关的已知问题；保留已证实有效且符合当前步骤契约的修改。"
                    "不要执行计划外操作。\n"
                )
                
                for i, attempt in enumerate(failed_attempts):
                    human_content += f"\n--- 尝试 {i + 1} ---\n"
                    human_content += (
                        f"代码：\n```python\n{attempt.get('code', '')}\n```\n"
                    )
                    human_content += (
                        f"受控错误或图像反馈：\n{attempt.get('feedback', '')}\n"
                    )
                
                human_content += (
                    "\n输出综合修复后的当前步骤代码。不要解释，"
                    "只返回一对 python Markdown 代码块。\n"
                )
                
            response = model.invoke([
                SystemMessage(content=system_content),
                HumanMessage(content=human_content)
            ])
            raw_output = response.content
            
            refined_code = extract_python_code(raw_output)
            logger.debug(f"由 LLM 闭门生成/或修补的代码切片：\n{refined_code}")
            
        except Exception as e:
            logger.error("Programmer 生成或修补代码失败: %s", e)
            raise RuntimeError(
                "探索性分析代码生成失败，未执行当前科学步骤"
            ) from e
            
    return {"last_generated_code": refined_code}
