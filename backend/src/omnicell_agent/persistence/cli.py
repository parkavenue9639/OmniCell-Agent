from __future__ import annotations

import argparse
import asyncio
import json

from omnicell_agent.core.environment import load_project_environment
from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.persistence.config import PostgresSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OmniCell-Agent PostgreSQL 管理入口")
    parser.add_argument("command", choices=("migrate", "check"))
    return parser


async def _run(command: str) -> None:
    load_project_environment()
    runtime = PersistenceRuntime(PostgresSettings.from_env())
    try:
        if command == "migrate":
            await runtime.initialize_schemas()
        await runtime.open()
        print(json.dumps(await runtime.healthcheck(), ensure_ascii=False))
    finally:
        await runtime.close()


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_run(args.command))


if __name__ == "__main__":
    main()
