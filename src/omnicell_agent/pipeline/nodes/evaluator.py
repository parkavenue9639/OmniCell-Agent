import logging
import base64
import os
import re
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.core.config import ENABLE_VISION_EVAL, project_root
from omnicell_agent.core.prompt_manager import prompt_manager
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.core.trace_logger import trace_logger

logger = logging.getLogger(__name__)

class VisionEvalResult(BaseModel):
    """用于规范化包裹多模态视觉打分的 Pydantic Schema"""
    status: str = Field(..., description="评估状态，必须是 'success' 或 'error'")
    feedback: str = Field(..., description="评估反馈：若是 success 则做简短赞美；若是 error 必须详细描述图表缺陷，以及给 Programmer 建议的详细 Python 纠错重绘指导（例如需修改 scanpy 绘图参数）。")

def extract_image_path(stdout: str) -> str:
    # 匹配 Scanpy 的图像保存日志，如: saving figure to file /app/data/pcacurrent_pca.png
    match = re.search(r"saving figure to file\s+([^\s]+\.png)", stdout)
    if match:
        container_path = match.group(1)
        filename = os.path.basename(container_path)
        host_path = os.path.join(project_root, "data", filename)
        if os.path.exists(host_path):
            return host_path
    return ""

def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def run_evaluator(state: DataPipeline_State) -> dict:
    """
    Evaluator Node
    检查上游 Executor 执行在沙盒里的结果。
    如果遭遇执行报错：收集错误日志作为 feedback 退回。
    如果有图像产出：交给 Vision Node LLM 评判质量，如果不行照样打回。
    """
    logger.info("--- NODE: EVALUATOR ---")
    trace_logger.append_node_start("EVALUATOR")
    
    sandbox_result = state.get("sandbox_execution_result", {})
    
    # 1. 代码执行层拦截 (沙盒层错误)
    if sandbox_result.get("status") == "error":
        err_msg = sandbox_result.get('error') or sandbox_result.get('stderr', 'Unknown Error')
        feedback_msg = f"Sandbox Execution Failed! Traceback info:\n\n{err_msg}\nPlease fix your Python代码。"
        logger.warning(f"检测到 Sandbox 异常日志，拦截任务重返 Programmer: \n{err_msg}")
        return {
            "task_context": {"eval_record": {"status": "error", "feedback": feedback_msg}}
        }
    
    eval_record = {"status": "success", "feedback": "Execution runs cleanly without issues."}

    task_context = state.get("task_context", {})

    # 2. 聚类视觉评估维度的拦截 (Vision)
    # 若开启了多模态，则读取 UMAP 图像发送给大模型进行图文评估
    if ENABLE_VISION_EVAL:
        logger.info("开启了 Vision 视觉评审。正在搜寻沙盒产出视图...")
        img_path = extract_image_path(sandbox_result.get("stdout", ""))
        
        if img_path:
            logger.info(f"成功截获产出图像: {img_path}")
            base64_image = _encode_image(img_path)
            
            # 调取状态里的用户意图与计划
            user_msg = state.get("messages", [])[-1].content if state.get("messages") else "请评估该生信图表"
            plan = task_context.get("plan", "无特定规划")
            
            sys_content = prompt_manager.load_prompt("evaluator_vision_system.txt")
            human_content = prompt_manager.load_prompt(
                "evaluator_vision_human.txt", 
                user_messages_text=user_msg, 
                plan=plan
            )
            
            # 组装多模态图文输入
            message_content = [
                {"type": "text", "text": human_content},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                }
            ]
            
            vision_llm = LLMSelector.get_llm("onerouter:default", temperature=0.0)
            # 通过原生 Langchain 将模型转化为强制 Pydantic 输出对象
            structured_vision_llm = vision_llm.with_structured_output(VisionEvalResult)
            
            logger.info("正在将图像与指令发送至 Vision LLM 评估（采用 Pydantic 结构化约束）...")
            try:
                vision_res: VisionEvalResult = structured_vision_llm.invoke([
                    SystemMessage(content=sys_content),
                    HumanMessage(content=message_content)
                ])
                
                eval_status = vision_res.status
                feedback = vision_res.feedback
                
                logger.info(f"视觉评估通过状态: {vision_res.status} | 详情: {vision_res.feedback}")
                trace_logger.append_vision_evaluation(img_path, {"status": vision_res.status, "feedback": vision_res.feedback})
                trace_logger.append_llm_interaction(
                    system_prompt=sys_content,
                    human_prompt=human_content,
                    llm_response=str(vision_res),
                    role_name="Vision_Evaluator_LLM"
                )
                
                if eval_status != "success":
                    logger.warning(f"视觉评估未通过: \n{feedback}")
                    eval_record["status"] = "error"
                    eval_record["feedback"] = f"Vision Evaluator Feedback:\n{feedback}\nPlease fix your Python code to redraw the image to meet all constraints."
                    
                    retries = task_context.get("retry_count", 0)
                    task_context["retry_count"] = retries + 1
                else:
                    logger.info(f"视觉评估完美通过: \n{feedback}")
                        
            except Exception as e:
                logger.error(f"调用 Vision LLM 或触发由于 Pydantic 解析失败导致的阻断: {e}")
        else:
            logger.info("本次执行不涉及图片生成或未找到产出图，跳过视觉审阅。")

    task_context["eval_record"] = eval_record
    return {"task_context": task_context}
