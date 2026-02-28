from dotenv import load_dotenv
import os
from pathlib import Path
import logging

# 初始化配置层专用的日志
logger = logging.getLogger(__name__)

# 获取项目根目录，定位 .env 文件
project_root = Path(__file__).parent.parent.parent.parent
env_file = project_root / ".env"

env_loaded = load_dotenv(dotenv_path=env_file)
if not env_loaded:
    logger.warning(f".env 文件未找到或未加载: {env_file}")

# ==============================================================================
# OpenRouter 模型配置加载区
# 推荐 OpenRouter 是因为它能够无缝切换 Claude 3.5 Sonnet / GPT-4o / GPT-o1 等核心模型
# ==============================================================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
# 默认指向 openrouter 的兼容 API
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# 支持用户在 env 中自定义默认的模型，比如 "anthropic/claude-3.5-sonnet"
DEFAULT_OPENROUTER_MODEL = os.getenv("DEFAULT_OPENROUTER_MODEL", "openai/gpt-4o-mini")

# 平台识别标头（OpenRouter 推荐设置，防止请求被拦截并获取正确统计信息）
APP_TITLE = "OmniCell-Agent"
APP_REFERER = "https://github.com/OmniCell-Agent"

# ==============================================================================
# 其它备用大模型配置区 (参考 MyGraph 架构预留的 OneRouter)
# ==============================================================================
ONEROUTER_API_KEY = os.getenv("ONEROUTER_API_KEY")
ONEROUTER_BASE_URL = os.getenv("ONEROUTER_BASE_URL", "https://llm.onerouter.pro/v1")
DEFAULT_ONEROUTER_MODEL = os.getenv("DEFAULT_ONEROUTER_MODEL", "gemini-2.5-flash")
