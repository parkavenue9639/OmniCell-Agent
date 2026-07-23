from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Sequence
from importlib.metadata import version
from typing import Any

import psycopg
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.database import await_cancellation_safe
from omnicell_agent.persistence.guards import ensure_payload_safe


logger = logging.getLogger(__name__)

_SUPPORTED_POSTGRES_SAVER_SERIES = (3, 0)


def _checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=(HumanMessage, AIMessage, SystemMessage, ToolMessage),
    )


def checkpoint_thread_id(conversation_id: str) -> str:
    identity = conversation_id.strip()
    if not identity:
        raise ValueError("conversation_id 不能为空")
    return f"conversation:{identity}"


def checkpoint_namespace(capability: str, invocation_id: str | None = None) -> str:
    capability = capability.strip().lower().replace(" ", "_")
    if not capability or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in capability):
        raise ValueError(f"非法 checkpoint capability: {capability!r}")
    if invocation_id is None:
        return capability
    invocation_id = invocation_id.strip()
    if not invocation_id or ":" in invocation_id:
        raise ValueError("invocation_id 不能为空且不能包含冒号")
    return f"{capability}:{invocation_id}"


class GuardedAsyncPostgresSaver(AsyncPostgresSaver):
    """在 vendor saver 写入前执行控制状态大小与类型边界。"""

    def __init__(self, conn: Any, *, max_state_bytes: int, serde: JsonPlusSerializer):
        super().__init__(conn, serde=serde)
        self._max_state_bytes = max_state_bytes

    async def aput(self, config, checkpoint, metadata, new_versions):
        ensure_payload_safe(
            {
                "checkpoint": checkpoint,
                "metadata": metadata,
                "new_versions": new_versions,
            },
            max_bytes=self._max_state_bytes,
            label="checkpoint payload",
        )
        return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        ensure_payload_safe(
            {"writes": writes, "task_id": task_id, "task_path": task_path},
            max_bytes=self._max_state_bytes,
            label="checkpoint writes",
        )
        await super().aput_writes(config, writes, task_id, task_path)


class CheckpointLifecycle:
    """显式管理 checkpoint schema、连接池、saver 获取与幂等关闭。"""

    def __init__(self, settings: PostgresSettings):
        self._settings = settings
        self._pool: AsyncConnectionPool | None = None
        self._saver: GuardedAsyncPostgresSaver | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self._pool is not None and not self._pool.closed

    async def setup_schema(self) -> None:
        """由显式初始化流程调用；vendor saver 是 checkpoint 表的唯一 owner。"""

        async with await psycopg.AsyncConnection.connect(
            self._settings.psycopg_conninfo,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            connect_timeout=self._settings.connect_timeout_seconds,
        ) as conn:
            await conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self._settings.checkpoint_schema)
                )
            )
            await conn.execute(
                sql.SQL("SET search_path TO {}").format(
                    sql.Identifier(self._settings.checkpoint_schema)
                )
            )
            saver = AsyncPostgresSaver(conn, serde=_checkpoint_serializer())
            await saver.setup()

    async def open(self) -> None:
        async with self._lifecycle_lock:
            if self.is_open:
                return
            stale_pool = self._pool
            self._pool = None
            self._saver = None
            if stale_pool is not None and not stale_pool.closed:
                await await_cancellation_safe(stale_pool.close())

            pool = AsyncConnectionPool(
                conninfo=self._settings.psycopg_conninfo,
                min_size=self._settings.pool_min_size,
                max_size=self._settings.pool_max_size,
                open=False,
                check=AsyncConnectionPool.check_connection,
                timeout=self._settings.connect_timeout_seconds,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                    "options": f"-c search_path={self._settings.checkpoint_schema}",
                },
            )
            try:
                await pool.open(
                    wait=True,
                    timeout=self._settings.connect_timeout_seconds,
                )
                async with pool.connection() as conn:
                    result = await conn.execute(
                        "SELECT COALESCE(MAX(v), -1) AS version FROM checkpoint_migrations"
                    )
                    row = await result.fetchone()
                    expected = len(AsyncPostgresSaver.MIGRATIONS) - 1
                    if row is None or int(row["version"]) != expected:
                        raise RuntimeError(
                            "checkpoint schema 未初始化或 revision 落后；请先运行显式 migration"
                        )
            except BaseException:
                await await_cancellation_safe(pool.close())
                raise

            self._pool = pool
            self._saver = GuardedAsyncPostgresSaver(
                pool,
                max_state_bytes=self._settings.checkpoint_state_max_bytes,
                serde=_checkpoint_serializer(),
            )
            logger.info("Checkpoint pool opened: %s", self._settings.safe_target)

    def get_saver(self) -> GuardedAsyncPostgresSaver:
        if self._saver is None or not self.is_open:
            raise RuntimeError("Checkpointer 未启动或已经关闭")
        return self._saver

    def get_pool(self) -> AsyncConnectionPool:
        if self._pool is None or not self.is_open:
            raise RuntimeError("Checkpoint pool 未启动或已经关闭")
        return self._pool

    async def healthcheck(self) -> bool:
        if not self.is_open:
            return False
        try:
            async with self.get_pool().connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def close(self) -> None:
        async with self._lifecycle_lock:
            pool = self._pool
            self._saver = None
            self._pool = None
            if pool is not None and not pool.closed:
                await await_cancellation_safe(pool.close())
                logger.info("Checkpoint pool closed")


class CheckpointRetention:
    """按 namespace 清理非保护 checkpoint；不进入 checkpoint 写入关键路径。"""

    def __init__(
        self,
        lifecycle: CheckpointLifecycle,
        *,
        app_schema: str,
        terminal_grace_period: dt.timedelta = dt.timedelta(hours=24),
        clock: Callable[[], dt.datetime] | None = None,
    ):
        if terminal_grace_period <= dt.timedelta(0):
            raise ValueError("terminal_grace_period 必须大于 0")
        self._lifecycle = lifecycle
        self._app_schema = app_schema
        self._terminal_grace_period = terminal_grace_period
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))

    @staticmethod
    def assert_vendor_contract() -> None:
        parts = version("langgraph-checkpoint-postgres").split(".")
        current = (int(parts[0]), int(parts[1]))
        if current != _SUPPORTED_POSTGRES_SAVER_SERIES:
            raise RuntimeError(
                "retention SQL 只验证过 langgraph-checkpoint-postgres 3.0.x，"
                f"当前为 {'.'.join(parts[:3])}"
            )

    async def prune_namespace(
        self,
        *,
        thread_id: str,
        checkpoint_ns: str,
        terminal_at: dt.datetime,
        keep_latest: int = 4,
    ) -> int:
        if keep_latest < 1:
            raise ValueError("keep_latest 必须大于 0")
        if terminal_at.tzinfo is None or terminal_at.utcoffset() is None:
            raise ValueError("terminal_at 必须包含时区")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("retention clock 必须返回带时区的时间")
        if now < terminal_at + self._terminal_grace_period:
            return 0
        self.assert_vendor_contract()

        pool = self._lifecycle.get_pool()
        async with pool.connection() as conn, conn.transaction():
            protected_result = await conn.execute(
                sql.SQL(
                    """
                    SELECT checkpoint_id
                    FROM {}.checkpoint_anchors
                    WHERE thread_id = %s AND checkpoint_ns = %s
                      AND (protected_until IS NULL OR protected_until > NOW())
                    """
                ).format(sql.Identifier(self._app_schema)),
                (thread_id, checkpoint_ns),
            )
            protected = {str(row["checkpoint_id"]) async for row in protected_result}

            checkpoint_result = await conn.execute(
                """
                SELECT checkpoint_id, checkpoint->>'ts' AS checkpoint_ts,
                       checkpoint->'channel_versions' AS channel_versions
                FROM checkpoints
                WHERE thread_id = %s AND checkpoint_ns = %s
                ORDER BY checkpoint_id DESC
                """,
                (thread_id, checkpoint_ns),
            )
            checkpoints = [row async for row in checkpoint_result]
            checkpoint_ids = [str(row["checkpoint_id"]) for row in checkpoints]
            rows_by_id = {str(row["checkpoint_id"]): row for row in checkpoints}
            doomed = [
                checkpoint_id
                for checkpoint_id in checkpoint_ids[keep_latest:]
                if checkpoint_id not in protected
                and dt.datetime.fromisoformat(rows_by_id[checkpoint_id]["checkpoint_ts"])
                <= terminal_at
            ]
            if not doomed:
                return 0

            doomed_versions = {
                (str(channel), str(channel_version))
                for checkpoint_id in doomed
                for channel, channel_version in (
                    rows_by_id[checkpoint_id]["channel_versions"] or {}
                ).items()
            }

            await conn.execute(
                """
                DELETE FROM checkpoint_writes
                WHERE thread_id = %s AND checkpoint_ns = %s
                  AND checkpoint_id = ANY(%s)
                """,
                (thread_id, checkpoint_ns, doomed),
            )
            deleted = await conn.execute(
                """
                DELETE FROM checkpoints
                WHERE thread_id = %s AND checkpoint_ns = %s
                  AND checkpoint_id = ANY(%s)
                """,
                (thread_id, checkpoint_ns, doomed),
            )
            if doomed_versions:
                channels, versions = zip(*sorted(doomed_versions), strict=True)
                await conn.execute(
                    """
                    WITH doomed(channel, version) AS (
                      SELECT * FROM UNNEST(%s::text[], %s::text[])
                    )
                    DELETE FROM checkpoint_blobs cb
                    USING doomed d
                    WHERE cb.thread_id = %s AND cb.checkpoint_ns = %s
                      AND cb.channel = d.channel AND cb.version = d.version
                      AND NOT EXISTS (
                        SELECT 1
                        FROM checkpoints c,
                             jsonb_each_text(c.checkpoint->'channel_versions') AS kv
                        WHERE c.thread_id = cb.thread_id
                          AND c.checkpoint_ns = cb.checkpoint_ns
                          AND kv.key = cb.channel
                          AND kv.value = cb.version
                      )
                    """,
                    (list(channels), list(versions), thread_id, checkpoint_ns),
                )
            return max(deleted.rowcount or 0, 0)
