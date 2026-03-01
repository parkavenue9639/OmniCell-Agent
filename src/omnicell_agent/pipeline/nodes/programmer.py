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

def _load_dynamic_template(plan: str) -> str:
    """
    基于对规划命令关键词的正则探嗅，动态挂载专业级生信画图模板，
    作为坚实的打底盘对抗大模型随机发挥和审美降级。
    """
    tpl_dir = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "templates")
    matched_tpl = ""
    plan_lower = plan.lower()
    
    # 根据常发报错点制定挂载规则
    if "pca" in plan_lower or "umap" in plan_lower or "绘图" in plan_lower or "scatter" in plan_lower:
        tpl_path = os.path.join(tpl_dir, "pca_scatter.py.tpl")
        logger.info("检测到可视化任务意图，挂载预置画图防雷装甲模板 [pca_scatter.py.tpl]")
    elif "marker" in plan_lower or "差异基因" in plan_lower:
        tpl_path = os.path.join(tpl_dir, "marker_genes.py.tpl")
        logger.info("检测到求取 Marker 细胞的意图，挂载差异基因铁律结构模板 [marker_genes.py.tpl]")
    else:
        return ""
        
    try:
        with open(tpl_path, "r", encoding="utf-8") as f:
            matched_tpl = f.read()
            return f"\n\n【🎁 专属预置执行模板，请参考其数据格式流写代码】:\n{matched_tpl}\n"
    except Exception as e:
        logger.error(f"无法读取内建保护模板，已忽略: {e}")
        return ""

def run_programmer(state: DataPipeline_State) -> dict:
    """
    Programmer Node
    读取规划与当前 DataPipeline 的文件配置，下发给大模型获得生信 Python 分析脚本片段。
    """
    logger.info("--- NODE: PROGRAMMER ---")
    trace_logger.append_node_start("PROGRAMMER")
    
    plan = state.get("task_context", {}).get("plan", "No plan provided.")
    raw_data_path = state.get("raw_data_path", "/app/data/sample.h5ad")
    marker_table_path = state.get("marker_table_path", "/app/data/markers.json")
    
    try:
        # programmer 节点需要最强的代码生成能力 (使用用户的 OneRouter 通道)
        llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.0)
        
        # 动态组装特定生信模板
        code_template_injection = _load_dynamic_template(plan)
        
        system_content = prompt_manager.load_prompt("programmer_system.txt", raw_data_path=raw_data_path, marker_table_path=marker_table_path)
        human_content = prompt_manager.load_prompt(
            "programmer_human.txt", 
            plan=plan, 
            raw_data_path=raw_data_path, 
            marker_table_path=marker_table_path,
            code_template=code_template_injection
        )
        
        # 错误自愈环回路：如果有上游 Evaluator 抛回的三体代码异常，叠加在尾部供 LLM 修复
        feedback = state.get("task_context", {}).get("eval_record", {}).get("feedback", "")
        if feedback:
            logger.warning("正在向 LLM 注入上一次失败的反馈跟踪要求，进行自动纠错代码...")
            human_content += f"\n\n【⚠️ 上次执行环境报错阻断】:\n{feedback}\n请反思并提供一份修复掉此 bug 的最新 Python 代码。严禁解释，只需抛出代码即可。"
        
        response = llm.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=human_content)
        ])
        
        raw_output = response.content
        trace_logger.append_llm_interaction(
            system_prompt=system_content, 
            human_prompt=human_content, 
            llm_response=raw_output, 
            role_name="Programmer_LLM"
        )
        
        refined_code = extract_python_code(raw_output)
        logger.debug(f"生成的可执行代码：\n{refined_code}")
        
    except Exception as e:
        logger.error(f"Programmer 执行失败: {e}")
        refined_code = "print('Failed to generate code due to LLM error.')"
        
    return {"last_generated_code": refined_code}
