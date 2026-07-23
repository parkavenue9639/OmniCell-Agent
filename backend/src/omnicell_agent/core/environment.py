"""项目级本地环境加载入口。"""

from __future__ import annotations

from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def load_project_environment() -> Path | None:
    """从当前工作目录向上查找 `.env`，且不覆盖显式进程环境。"""

    env_file = find_dotenv(usecwd=True)
    if not env_file:
        return None
    load_dotenv(dotenv_path=env_file, override=False)
    return Path(env_file)


__all__ = ["load_project_environment"]
