import logging
import os
import sys

# 添加 src 到扫描路径以使用包名
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from langchain_core.messages import HumanMessage
from omnicell_agent.pipeline.nodes.planner import run_planner
from omnicell_agent.pipeline.nodes.programmer import run_programmer
from omnicell_agent.schema.state import DataPipeline_State

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_pipeline_nodes():
    """
    独立验证 Planner 和 Programmer 能否成功通过加载外置的 txt Prompt 模块得到大模型回复。
    """
    logger.info("========== [1] 启动 Planner 验证 ==========")
    mock_state: DataPipeline_State = {
        "raw_data_path": "/app/data/pbmc3k.h5ad",
        "marker_table_path": "/app/data/markers.csv",
        "messages": [HumanMessage(content="对这份10x单细胞数据进行标准的预处理和聚类降维分析，然后找出每个簇的 marker genes。")],
        "task_context": {},
        "last_generated_code": "",
        "sandbox_execution_result": {}
    }
    
    try:
        planner_result = run_planner(mock_state)
        # 将差量更新回写到 mock_state 中
        mock_state["task_context"].update(planner_result.get("task_context", {}))
        
        plan = mock_state["task_context"].get("plan", "")
        if not plan:
            raise ValueError("Planner 没能返回任何计划规划！")
            
        logger.info(f"✅ Planner 输出成功: \n{plan}\n")
        
    except Exception as e:
        logger.error(f"❌ Planner 节点联调失败: {e}")
        return

    logger.info("========== [2] 启动 Programmer 验证 ==========")
    try:
        programmer_result = run_programmer(mock_state)
        code = programmer_result.get("last_generated_code", "")
        
        if not code or "Failed to generate code" in code:
            raise ValueError("Programmer 没能生成出合法代码！检查 LLM 或 Prompt Template！")
            
        logger.info(f"✅ Programmer 输出成功！\n生成的 Python 长度: {len(code)}\n完整代码:\n{code}\n")
    except Exception as e:
        logger.error(f"❌ Programmer 节点联调失败: {e}")
        return
        
    logger.info("========== [3] 全部节点与 Prompt Manager 端到端验证通过! ==========")

if __name__ == "__main__":
    test_pipeline_nodes()
