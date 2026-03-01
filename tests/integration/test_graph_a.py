import logging
import os
import sys

# 添加 src 到扫描路径以使用包名
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from langchain_core.messages import HumanMessage
from omnicell_agent.pipeline.graph import build_pipeline_graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_subgraph_a_execution():
    """
    通过运行构筑好的 LangGraph 测试整条子管线引擎是否协同连贯。
    涵盖：规划、改写生成 (防爆机制)、丢入真实的 Jupyter Sandbox 执行、以及最终的审核退出机制。
    """
    logger.info("========== 开始基于 LangGraph 子图 A 端到端联测 ==========")
    
    app = build_pipeline_graph()
    
    # 构建包含错误诱导或者能够真实全自动运行的指令
    # 我们先测试单步绘图，确保能通过全链路而不中断。
    init_state = {
        "raw_data_path": "/app/data/pbmc3k_raw.h5ad",
        "marker_table_path": "/app/data/markers.csv",
        "messages": [HumanMessage(content="执行一个复杂的全组学生信分析大穿透：请首先对原始数据进行严格质量控制与过滤；然后执行归一化；接着寻高变基因并使用 PCA 降维；对降维结果直接进行聚类；聚类出结果后利用常见的 Marker 库自动标记粗粒度的细胞群分类；顺便计算所有聚类簇的核心 Marker 基因并导出为表格文件；最后请给我画一张好看的 PCA 散点图并标上聚类颜色，存为 current_pca.png。")],
        "task_context": {},
        "plan_steps": [],
        "current_step_index": 0,
        "last_generated_code": "",
        "sandbox_execution_result": {}
    }
    
    try:
        final_state = app.invoke(init_state)
        
        logger.info("\n========== 全链路图 A 协同执行完毕！ ==========")
        logger.info(f"最终节点反馈信息: {final_state['task_context'].get('eval_record')}")
        logger.info(f"最后由 Programmer 尝试生成的代码:\n{final_state['last_generated_code']}")
        logger.info(f"底层沙盒环境的原始内容回执:\n{final_state['sandbox_execution_result']}")
        
    except Exception as e:
        logger.error(f"❌ 图执行引擎遭遇致命阻断: {e}")

if __name__ == "__main__":
    test_subgraph_a_execution()
