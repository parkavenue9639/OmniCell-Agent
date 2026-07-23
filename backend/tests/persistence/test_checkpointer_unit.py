import asyncio
import datetime as dt

import pytest
from langchain_core.messages import HumanMessage
from langgraph.types import Send

from omnicell_agent.persistence import checkpointer as checkpointer_module
from omnicell_agent.persistence.checkpointer import (
    CheckpointLifecycle,
    CheckpointRetention,
    _checkpoint_serializer,
    checkpoint_namespace,
    checkpoint_thread_id,
)
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.guards import (
    ForbiddenPersistenceTypeError,
    PersistencePayloadError,
    PersistencePayloadTooLargeError,
    ensure_payload_safe,
)


def _settings() -> PostgresSettings:
    return PostgresSettings(
        dsn="postgresql://user:secret@127.0.0.1:55432/omnicell",
        pool_min_size=1,
        pool_max_size=2,
    )


def test_postgres_settings_normalizes_and_redacts_dsn() -> None:
    settings = _settings()

    assert settings.sqlalchemy_dsn.startswith("postgresql+psycopg://")
    assert settings.psycopg_conninfo.startswith("postgresql://")
    assert "secret" not in settings.safe_target
    assert "user" not in settings.safe_target


def test_postgres_safe_target_drops_user_and_all_query_parameters() -> None:
    settings = PostgresSettings(
        dsn=(
            "postgresql://private-user:authority-secret@127.0.0.1:55432/omnicell"
            "?sslpassword=query-secret&application_name=private-app"
        )
    )

    assert settings.safe_target == "postgresql+psycopg://127.0.0.1:55432/omnicell"
    assert "secret" not in settings.safe_target
    assert "private" not in settings.safe_target


@pytest.mark.parametrize("schema", ["Public", "bad-schema", "x;drop schema public"])
def test_postgres_settings_rejects_unsafe_schema(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        PostgresSettings(dsn="postgresql://localhost/db", app_schema=schema)


def test_postgres_settings_rejects_shared_application_and_checkpoint_schema() -> None:
    with pytest.raises(ValueError, match="必须相互隔离"):
        PostgresSettings(
            dsn="postgresql://localhost/db",
            app_schema="same_schema",
            checkpoint_schema="same_schema",
        )


def test_checkpoint_identity_contract() -> None:
    assert checkpoint_thread_id("abc") == "conversation:abc"
    assert checkpoint_namespace("Graph A") == "graph_a"
    assert checkpoint_namespace("graph_b", "run-1") == "graph_b:run-1"


def test_checkpoint_serializer_round_trips_allowed_message() -> None:
    serializer = _checkpoint_serializer()
    payload = serializer.dumps_typed(HumanMessage(content="hello"))

    restored = serializer.loads_typed(payload)

    assert isinstance(restored, HumanMessage)
    assert restored.content == "hello"


def test_payload_guard_accepts_small_structured_control_data() -> None:
    size = ensure_payload_safe(
        {"event": "run.started", "payload": {"run_id": "r1", "sequence": 1}},
        max_bytes=1024,
        label="event",
    )

    assert 0 < size < 1024


def test_payload_guard_accepts_send_but_recursively_guards_its_argument() -> None:
    assert ensure_payload_safe(
        Send("worker", {"cluster_id": "1", "markers": ["IL7R", "CCR7"]}),
        max_bytes=1024,
        label="checkpoint",
    ) > 0

    with pytest.raises(ForbiddenPersistenceTypeError, match="二进制"):
        ensure_payload_safe(
            Send("worker", {"raw": b"not-control-state"}),
            max_bytes=1024,
            label="checkpoint",
        )


def test_payload_guard_rejects_binary_scientific_and_large_values() -> None:
    with pytest.raises(ForbiddenPersistenceTypeError, match="二进制"):
        ensure_payload_safe(b"raw", max_bytes=10, label="event")

    np = pytest.importorskip("numpy")
    with pytest.raises(ForbiddenPersistenceTypeError, match="artifact"):
        ensure_payload_safe(np.array([1, 2]), max_bytes=1024, label="checkpoint")

    with pytest.raises(PersistencePayloadTooLargeError, match="超过上限"):
        ensure_payload_safe("x" * 20, max_bytes=10, label="event")


def test_payload_guard_rejects_cycles() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(PersistencePayloadError, match="循环引用"):
        ensure_payload_safe(cyclic, max_bytes=1024, label="event")


@pytest.mark.asyncio
async def test_checkpoint_lifecycle_is_fail_closed_and_close_is_idempotent() -> None:
    lifecycle = CheckpointLifecycle(_settings())

    assert lifecycle.is_open is False
    assert await lifecycle.healthcheck() is False
    with pytest.raises(RuntimeError, match="未启动"):
        lifecycle.get_saver()

    await lifecycle.close()
    await lifecycle.close()


class _BlockingOpenPool:
    check_connection = staticmethod(lambda connection: None)

    def __init__(self, *args, **kwargs) -> None:
        self.open_started = asyncio.Event()
        self.release_open = asyncio.Event()
        self.closed = False
        self.close_calls = 0

    async def open(self, *args, **kwargs) -> None:
        self.open_started.set()
        await self.release_open.wait()

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


@pytest.mark.asyncio
async def test_checkpoint_open_cancellation_closes_unpublished_pool(monkeypatch) -> None:
    pool = _BlockingOpenPool()

    class PoolFactory:
        check_connection = staticmethod(lambda connection: None)

        def __new__(cls, *args, **kwargs):
            return pool

    monkeypatch.setattr(checkpointer_module, "AsyncConnectionPool", PoolFactory)
    lifecycle = CheckpointLifecycle(_settings())
    open_task = asyncio.create_task(lifecycle.open())
    await pool.open_started.wait()

    open_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await open_task

    assert pool.closed is True
    assert pool.close_calls == 1
    assert lifecycle.is_open is False
    await lifecycle.close()


class _BlockingClosePool:
    def __init__(self) -> None:
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.closed = False

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


@pytest.mark.asyncio
async def test_checkpoint_close_finishes_before_propagating_cancellation() -> None:
    pool = _BlockingClosePool()
    lifecycle = CheckpointLifecycle(_settings())
    lifecycle._pool = pool
    lifecycle._saver = object()

    close_task = asyncio.create_task(lifecycle.close())
    await pool.close_started.wait()
    close_task.cancel()
    pool.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert pool.closed is True
    assert lifecycle.is_open is False
    await lifecycle.close()


def test_retention_is_bound_to_reviewed_vendor_series() -> None:
    CheckpointRetention.assert_vendor_contract()


@pytest.mark.asyncio
async def test_retention_does_not_touch_active_or_recently_terminal_namespace() -> None:
    terminal_at = dt.datetime.now(dt.UTC)
    retention = CheckpointRetention(
        object(),
        app_schema="omnicell_app",
        clock=lambda: terminal_at + dt.timedelta(minutes=30),
    )

    assert (
        await retention.prune_namespace(
            thread_id="thread",
            checkpoint_ns="namespace",
            terminal_at=terminal_at,
            keep_latest=1,
        )
        == 0
    )
