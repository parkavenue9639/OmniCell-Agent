import os
import logging
from typing import Dict, Any

from langchain_core.messages import HumanMessage, SystemMessage
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.core.config import project_root

logger = logging.getLogger(__name__)

def final_summarizer_node(state: dict) -> dict:
    """
    终极汇报节点：提取管线的中间链路痕迹与鉴定终端输出，并用大模型进行整合解读。
    将报告归档写盘。
    """
    logger.info("--- NODE: FINAL SUMMARIZER (全局业务洞察) ---")
    
    # 提取全阶段信息
    messages = state.get("messages", [])
    user_input = messages[0].content if messages else "未截获指令"
    
    plan_steps = state.get("plan_steps", [])
    if plan_steps:
        plan_desc = "\n".join([f"- 第 {i+1} 步 ({s.get('skill_name', 'custom')}): {s.get('instruction')}" for i, s in enumerate(plan_steps)])
    else:
        plan_desc = "未找到拆分的图 A 规划步骤"
        
    sandbox_result = state.get("sandbox_execution_result", {})
    sandbox_status = sandbox_result.get("status", "未执行")
    
    final_report = state.get("final_report", "最终图 B 细胞鉴定报告为空")
    
    # 组装 Prompt
    sys_prompt = (
        "你是 OmniCell-Agent 顶层的生物学总监兼汇报分析专家。\n"
        "我们的自动化系统（图 A 算力沙盒 + 图 B 大模型共识集群）刚刚完成了一项单细胞分析流水线。\n"
        "你需要根据以下信息，生成一份给科研人员的人类可读的全景观报告，核心涵盖：\n"
        "1. 这个任务最初要做什么？\n"
        "2. 我们系统底层做了怎样的自动规划拆解？结果如何？\n"
        "3. 直接展示生成的最终鉴定大图表（必须保留格式）。\n"
        "4. 基于这个表中鉴定出来的细胞群体（如浆细胞、肿瘤相关成纤维细胞等），进行 1-2 段极其专业的、面向人类读者的“生信鉴定报告解读”（比如这些群体共同预示着什么异质性状态？）。\n\n"
        "请使用极其优雅、专业的 Markdown 语言进行排版输出。"
    )
    
    human_prompt = f"""
【用户原始指令】
{user_input}

【图 A 核心拆解规划链路】
{plan_desc}
(代码端到端执行状态: {sandbox_status})

【图 B 终极鉴定报告矩阵】
{final_report}
"""
    
    try:
        model = LLMSelector.get_llm("onerouter:default", temperature=0.3)
        logger.info("正在调阅全局态进行大报告的生成汇编解读...")
        response = model.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=human_prompt)
        ])
        
        summary_content = response.content
        
        # 固化落盘
        output_path = os.path.join(project_root, "data", "omnicell_executive_summary.md")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(summary_content)
        
        logger.info(f"🎉 终极大报告生成成功并已归档至: {output_path}")
        return {"final_report": summary_content} # 覆盖全局的 final_report 作为图的最顶端回执
        
    except Exception as e:
        logger.error(f"最终总结报告生成过程出现严重错误: {e}")
        return {}
