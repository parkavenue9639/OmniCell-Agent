import os
from pathlib import Path

from .environment import load_project_environment

# Wheel/子进程不能从 ``__file__`` 推断仓库根。组合根可显式配置；
# 本地开发未配置时使用当前工作目录。
project_root = Path(
    os.environ.get("OMNICELL_PROJECT_ROOT", str(Path.cwd()))
).expanduser().resolve(strict=False)
load_project_environment()

# Graph A 是否启用视觉质量评估。
ENABLE_VISION_EVAL = os.getenv("ENABLE_VISION_EVAL", "True").lower() in ("true", "1", "t")

# Graph B Annotator 是否启用三温度自一致性投票。
ENABLE_SELF_CONSISTENCY = os.getenv("ENABLE_SELF_CONSISTENCY", "True").lower() in ("true", "1", "t")

# Graph B 是否在 Reporter 前运行跨簇 consistency reviewer。
ENABLE_CONSISTENCY_REVIEWER = os.getenv("ENABLE_CONSISTENCY_REVIEWER", "True").lower() in ("true", "1", "t")

# Graph B 微观图中低分簇是否触发 Boost 深潜纠错。
ENABLE_BOOST = os.getenv("ENABLE_BOOST", "True").lower() in ("true", "1", "t")
