import logging
import re
from langchain_core.messages import SystemMessage, HumanMessage
from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.schema.state import DataPipeline_State
from omnicell_agent.core.prompt_manager import prompt_manager

logger = logging.getLogger(__name__)

def extract_python_code(text: str) -> str:
    """提取 markdown 中的 python block"""
    match = re.search(r"```python\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()

def run_programmer(state: DataPipeline_State) -> dict:
    """
    Programmer Node
    读取规划与当前 DataPipeline 的文件配置，下发给大模型获得生信 Python 分析脚本片段。
    """
    logger.info("--- NODE: PROGRAMMER ---")
    
    plan = state.get("task_context", {}).get("plan", "No plan provided.")
    raw_data_path = state.get("raw_data_path", "/app/data/sample.h5ad")
    marker_table_path = state.get("marker_table_path", "/app/data/markers.json")
    
    try:
        # programmer 节点需要最强的代码生成能力 (使用用户的 OneRouter 通道)
        llm = LLMSelector.get_llm(model_name="onerouter:default", temperature=0.0)
        
        system_content = prompt_manager.load_prompt("programmer_system.txt", raw_data_path=raw_data_path, marker_table_path=marker_table_path)
        human_content = prompt_manager.load_prompt(
            "programmer_human.txt", 
            plan=plan, 
            raw_data_path=raw_data_path, 
            marker_table_path=marker_table_path
        )
        
        response = llm.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=human_content)
        ])
        
        raw_output = response.content
        refined_code = extract_python_code(raw_output)
        logger.debug(f"生成的可执行代码：\n{refined_code}")
        
    except Exception as e:
        logger.error(f"Programmer 执行失败: {e}")
        refined_code = "print('Failed to generate code due to LLM error.')"
        
    return {"last_generated_code": refined_code}
