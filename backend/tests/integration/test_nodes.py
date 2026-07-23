import logging
import os
import sys

import pytest

# 添加 src 到扫描路径以使用包名
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from langchain_core.messages import HumanMessage
from omnicell_agent.pipeline.nodes.planner import run_planner
from omnicell_agent.pipeline.nodes.programmer import run_programmer
from omnicell_agent.schema.state import DataPipeline_State

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("OMNICELL_RUN_LIVE_TESTS") != "1",
        reason="设置 OMNICELL_RUN_LIVE_TESTS=1 后运行真实 LLM 观察测试",
    ),
]

def run_single_test(test_name: str, user_message: str):
    logger.info(f"\n========== 启动测试: {test_name} ==========")
    mock_state: DataPipeline_State = {
        "raw_data_path": "/app/data/pbmc3k.h5ad",
        "marker_table_path": "/app/data/markers.csv",
        "messages": [HumanMessage(content=user_message)],
        "task_context": {},
        "plan_steps": [],
        "current_step_index": 0,
        "last_generated_code": "",
        "sandbox_execution_result": {}
    }

    planner_result = run_planner(mock_state)
    plan_steps = planner_result["plan_steps"]

    assert plan_steps
    assert all(step.get("instruction") for step in plan_steps)
    logger.info(f"✅ Planner 输出成功: \n{plan_steps}\n")

    mock_state.update(planner_result)
    programmer_result = run_programmer(mock_state)
    code = programmer_result.get("last_generated_code", "")

    assert code
    assert "Failed to generate code" not in code
    logger.info(f"✅ Programmer 输出成功！\n完整代码:\n{code}\n")


def test_pipeline_nodes():
    """
    独立验证 Planner 和 Programmer 能否成功通过加载外置的 txt Prompt 模块得到大模型回复。
    新增了专家级提示词对照测试！
    """
    # 1. 验证对于“全管线”的泛化召回
    run_single_test("全流程分析测试", "对这份10x单细胞数据进行标准的预处理和聚类降维分析，然后找出每个簇的 marker genes。")
    
    # 2. 验证局部/特定任务指令，是否能按规只做要求的事而不强行加戏
    run_single_test("局部意图精准探索测试", "我只想看一眼数据现在的样子，请帮我仅做一步 UMAP 可视化并保存图像。")
    
    logger.info("\n========== 全部节点与 Prompt Manager 专家对照验证通过! ==========")

if __name__ == "__main__":
    test_pipeline_nodes()
