"""Local API server command."""

from __future__ import annotations

import argparse
import os

import uvicorn

from omnicell_agent.core.environment import load_project_environment


def main() -> None:
    load_project_environment()
    parser = argparse.ArgumentParser(description="启动 OmniCell-Agent 本地 API")
    parser.add_argument(
        "--host",
        default=os.environ.get("OMNICELL_API_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OMNICELL_API_PORT", "8000")),
    )
    args = parser.parse_args()
    host = args.host.strip()
    port = args.port
    if not host:
        raise ValueError("OMNICELL_API_HOST 不能为空")
    if not 1 <= port <= 65_535:
        raise ValueError("OMNICELL_API_PORT 必须在 1..65535 之间")
    uvicorn.run(
        "omnicell_agent.api.bootstrap:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
