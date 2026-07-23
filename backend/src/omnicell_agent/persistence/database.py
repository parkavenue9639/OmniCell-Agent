"""Async SQLAlchemy lifecycle for application-owned persistence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import APP_SCHEMA


class PostgresSettingsLike(Protocol):
    sqlalchemy_dsn: str
    app_schema: str
    pool_min_size: int
    pool_max_size: int
    connect_timeout_seconds: float
    event_payload_max_bytes: int
    artifact_metadata_max_bytes: int


_T = TypeVar("_T")


async def await_cancellation_safe(awaitable: Awaitable[_T]) -> _T:
    """Finish a cleanup/finalization awaitable before propagating cancellation."""

    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


class ApplicationDatabase:
    """Own one application engine and session factory.

    Engine construction is lazy and side-effect free with respect to the
    database.  Migrations remain an explicit, separately invoked operation.
    """

    def __init__(
        self,
        settings: PostgresSettingsLike,
        *,
        engine_factory: Callable[..., AsyncEngine] = create_async_engine,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self._engine is not None

    @property
    def settings(self) -> PostgresSettingsLike:
        return self._settings

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Application database is not open")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Application database is not open")
        return self._session_factory

    async def open(self) -> None:
        async with self._lifecycle_lock:
            if self._engine is not None:
                return

            pool_min = max(1, self._settings.pool_min_size)
            pool_max = max(pool_min, self._settings.pool_max_size)
            engine = self._engine_factory(
                self._settings.sqlalchemy_dsn,
                pool_size=pool_min,
                max_overflow=pool_max - pool_min,
                pool_pre_ping=True,
                connect_args={
                    "connect_timeout": self._settings.connect_timeout_seconds
                },
                execution_options={
                    "schema_translate_map": {APP_SCHEMA: self._settings.app_schema}
                },
            )
            try:
                session_factory = async_sessionmaker(
                    engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autoflush=False,
                )
            except BaseException:
                await await_cancellation_safe(engine.dispose())
                raise
            self._engine = engine
            self._session_factory = session_factory

    async def check_connection(self) -> None:
        """Fail fast if the configured database cannot execute a query."""

        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        async with self._lifecycle_lock:
            engine = self._engine
            self._engine = None
            self._session_factory = None

        if engine is not None:
            await await_cancellation_safe(engine.dispose())


Database = ApplicationDatabase
