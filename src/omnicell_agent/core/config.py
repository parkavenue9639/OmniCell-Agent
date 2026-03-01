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

# ==============================================================================
# OmniCell-Agent 论文对照实验特性开关 (Ablation Experiment Switches)
# 控制系统双向通讯及多模态组件是否开启，可用于通过 --env 参数化运行多组纯净实验 
# ==============================================================================

# [实验二：闭环反馈] 控制是否允许 Annotation Boost 跨越 Graph B 回调要求 Graph A 的 Programmer/Sandbox 进行亚聚类或参数重算
ENABLE_CROSS_GRAPH_BOOST = os.getenv("ENABLE_CROSS_GRAPH_BOOST", "True").lower() in ("true", "1", "t")

# [实验三：防幻觉可靠性] 控制 Graph A 的 Evaluator 是否调用 GPT-4V/4o 多模态视觉接口去对 UMAP 图像进行专业生信读图打分
ENABLE_VISION_EVAL = os.getenv("ENABLE_VISION_EVAL", "True").lower() in ("true", "1", "t")

# [实验五：LangGraph并发] 控制 Graph B 是否对多簇并行采用 LangGraph 的 Send API (Map-Reduce 范式) 异步运行全并发
# 如果为 False，则系统将降级为类似原生 CASSIA 的 For-loop 串行排队计算
ENABLE_MAP_REDUCE_CONCURRENCY = os.getenv("ENABLE_MAP_REDUCE_CONCURRENCY", "True").lower() in ("true", "1", "t")

# ==============================================================================
# 实验底账与运行记录持久化存储区
# 为撰写论文图表提供数据支撑的 JSON/SQLite 本地沉淀目录
# ==============================================================================
EXPERIMENT_RECORDS_DIR = project_root / "experiment_records"
# 初始化时如果目录不存在则建立
EXPERIMENT_RECORDS_DIR.mkdir(parents=True, exist_ok=True)
