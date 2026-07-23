from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from omnicell_agent.persistence.checkpointer import CheckpointLifecycle
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.database import (
    ApplicationDatabase,
    await_cancellation_safe,
)
from omnicell_agent.persistence.migrations import upgrade_app_schema
from omnicell_agent.persistence.unit_of_work import UnitOfWork


logger = logging.getLogger(__name__)
APP_SCHEMA_HEAD = "20260722_0002"


class PersistenceRuntime:
    """组合应用数据库与 checkpointer 的初始化、ready 检查和关闭顺序。"""

    def __init__(self, settings: PostgresSettings):
        self.settings = settings
        self.application = ApplicationDatabase(settings)
        self.checkpoints = CheckpointLifecycle(settings)

    async def initialize_schemas(self) -> None:
        await upgrade_app_schema(self.settings)
        await self.checkpoints.setup_schema()

    async def open(self) -> None:
        await self.application.open()
        try:
            await self.application.check_connection()
            await self._assert_app_revision()
            await self.checkpoints.open()
        except BaseException:
            await self.close()
            raise

    async def _assert_app_revision(self) -> None:
        schema = self.settings.app_schema
        async with self.application.engine.connect() as connection:
            result = await connection.execute(
                text(f'SELECT version_num FROM "{schema}".alembic_version')
            )
            revision = result.scalar_one_or_none()
        if revision != APP_SCHEMA_HEAD:
            raise RuntimeError(
                f"应用 schema revision={revision!r}，期望 {APP_SCHEMA_HEAD!r}；请先运行 migration"
            )

    async def healthcheck(self) -> dict[str, bool]:
        app_ok = False
        if self.application.is_open:
            try:
                await self.application.check_connection()
                app_ok = True
            except Exception:
                app_ok = False
        checkpoint_ok = await self.checkpoints.healthcheck()
        return {"application": app_ok, "checkpointer": checkpoint_ok}

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.application)

    async def close(self) -> None:
        results = await await_cancellation_safe(
            asyncio.gather(
                self.checkpoints.close(),
                self.application.close(),
                return_exceptions=True,
            )
        )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("关闭 PostgreSQL 持久化资源失败", errors)
