import os
import sys

# 添加 src 到扫描路径以使用包名
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from omnicell_agent.core.llm_client import LLMSelector
from omnicell_agent.core.logger import logger
from omnicell_agent.core.config import DEFAULT_ONEROUTER_MODEL

def test_llm_factory():
    logger.info("------------- 开始测试由于使用自定义中转产生的 gemini-2.5 模型 ------------- ")
    try:
        # 使用 onerouter:... 前缀表示采用兼容通道，默认模型走配置
        model_name = f"onerouter:{DEFAULT_ONEROUTER_MODEL}"
        llm = LLMSelector.get_llm(model_name=model_name, temperature=0.0)
        
        logger.info(f"实例化成功! 发起对大模型 {model_name} 的流式请求...")
        response = llm.invoke("Hi! Please tell me a one-sentence joke about Python.")
        logger.info(f"大模型返回成功! \n{response.content}")
        
    except Exception as e:
        logger.error(f"测试失败! {e}")

if __name__ == "__main__":
    test_llm_factory()
