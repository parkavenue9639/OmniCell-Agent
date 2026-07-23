import os
import sys

import pytest

# 添加 src 到扫描路径以使用包名
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from omnicell_agent import llm
from omnicell_agent.core.logger import logger

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("OMNICELL_RUN_LIVE_TESTS") != "1",
        reason="设置 OMNICELL_RUN_LIVE_TESTS=1 后运行真实 LLM 观察测试",
    ),
]

def test_llm_factory():
    logger.info("------------- 开始测试角色 alias 对应的真实模型 ------------- ")
    model = llm.get_llm_by_alias(llm.LLMRole.AGENT_PRIMARY, temperature=0.0)

    logger.info("实例化成功，发起 agent_primary 模型流式请求...")
    response = model.invoke("Hi! Please tell me a one-sentence joke about Python.")

    assert isinstance(response.content, str)
    assert response.content.strip()
    logger.info(f"大模型返回成功! \n{response.content}")

if __name__ == "__main__":
    test_llm_factory()
