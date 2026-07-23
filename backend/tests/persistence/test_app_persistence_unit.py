from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from importlib.resources import files
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command
from sqlalchemy import UniqueConstraint

from omnicell_agent.persistence.database import ApplicationDatabase
from omnicell_agent.persistence.guards import ForbiddenPersistenceTypeError
from omnicell_agent.persistence.migrations import _alembic_config
from omnicell_agent.persistence.models import (
    APP_SCHEMA,
    Artifact,
    Base,
    CheckpointAnchor,
    Run,
    RunEvent,
)
from omnicell_agent.persistence.repositories import (
    EventIdConflictError,
    RunEventRepository,
    RunRepository,
)
from omnicell_agent.persistence.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class Settings:
    dsn: str = "postgresql://user:password@127.0.0.1:5432/omnicell"
    app_schema: str = APP_SCHEMA
    pool_min_size: int = 1
    pool_max_size: int = 3
    connect_timeout_seconds: float = 1.0
    event_payload_max_bytes: int = 128 * 1024
    artifact_metadata_max_bytes: int = 64 * 1024

    @property
    def sqlalchemy_dsn(self) -> str:
        return self.dsn.replace("postgresql://", "postgresql+psycopg://")


def test_application_metadata_is_schema_qualified_and_checkpoint_tables_are_excluded():
    expected = {
        "conversations",
        "runs",
        "run_events",
        "run_tasks",
        "reviews",
        "artifacts",
        "checkpoint_anchors",
    }
    assert {table.name for table in Base.metadata.tables.values()} == expected
    assert {table.schema for table in Base.metadata.tables.values()} == {APP_SCHEMA}
    assert not any(table.name.startswith("checkpoint_") and table.name != "checkpoint_anchors" for table in Base.metadata.tables.values())

    event_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in RunEvent.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("run_id", "sequence") in event_uniques
    assert RunEvent.__table__.primary_key.columns.keys() == ["id"]


def test_models_store_resource_references_not_large_artifact_or_checkpoint_payloads():
    assert "uri" in Artifact.__table__.columns
    assert "payload" not in Artifact.__table__.columns
    assert "content" not in Artifact.__table__.columns
    assert "checkpoint_id" in CheckpointAnchor.__table__.columns
    assert "payload" not in CheckpointAnchor.__table__.columns
    assert "checkpoint" not in CheckpointAnchor.__table__.columns


def test_offline_migration_is_explicit_and_uses_application_version_schema(capsys):
    command.upgrade(_alembic_config(Settings()), "head", sql=True)
    sql = capsys.readouterr().out
    assert f"CREATE SCHEMA IF NOT EXISTS \"{APP_SCHEMA}\"" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.alembic_version" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.conversations" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.checkpoint_anchors" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.run_tasks" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.reviews" in sql
    assert "ck_runs_run_status" in sql
    assert "ck_run_tasks_run_task_status" in sql
    assert "ck_reviews_review_status" in sql
    assert "ck_runs_ck_runs" not in sql
    assert "ck_run_tasks_ck_run_tasks" not in sql
    assert "ck_reviews_ck_reviews" not in sql
    assert "ix_runs_status_lease" in sql
    assert "uq_runs_one_active_per_conversation" in sql
    assert "WHERE status IN ('pending', 'running', 'review_required', 'cancelling')" in sql
    assert "CREATE TABLE omnicell_app.checkpoints" not in sql
    assert "create_all" not in sql


def test_packaged_alembic_resource_set_is_complete():
    root = files("omnicell_agent.persistence").joinpath("alembic")
    expected = (
        "env.py",
        "script.py.mako",
        "versions/20260722_0001_app_schema.py",
        "versions/20260722_0002_run_lifecycle.py",
    )
    assert all(root.joinpath(path).is_file() for path in expected)


def test_lifecycle_migration_downgrade_uses_symmetric_constraint_names(capsys):
    command.downgrade(
        _alembic_config(Settings()),
        "20260722_0002:20260722_0001",
        sql=True,
    )
    sql = capsys.readouterr().out
    assert "DROP TABLE" in sql and f"{APP_SCHEMA}.reviews" in sql
    assert f"DROP INDEX {APP_SCHEMA}.uq_runs_one_active_per_conversation" in sql
    assert "DROP CONSTRAINT ck_runs_run_status" in sql
    assert "DROP CONSTRAINT ck_runs_run_attempt_non_negative" in sql
    assert "ck_runs_ck_runs" not in sql


@pytest.mark.asyncio
async def test_database_open_failure_does_not_publish_partial_lifecycle():
    def fail_engine_factory(*args, **kwargs):
        raise RuntimeError("engine construction failed")

    database = ApplicationDatabase(Settings(), engine_factory=fail_engine_factory)
    with pytest.raises(RuntimeError, match="engine construction failed"):
        await database.open()
    assert database.is_open is False
    await database.close()


class BlockingDisposeEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False

    async def dispose(self) -> None:
        self.started.set()
        await self.release.wait()
        self.finished = True


@pytest.mark.asyncio
async def test_database_close_is_idempotent_and_finishes_disposal_when_cancelled():
    database = ApplicationDatabase(Settings())
    engine = BlockingDisposeEngine()
    database._engine = engine  # Lifecycle isolation: no network connection is created.
    database._session_factory = object()

    close_task = asyncio.create_task(database.close())
    await engine.started.wait()
    close_task.cancel()
    engine.release.set()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert engine.finished is True
    assert database.is_open is False
    await database.close()


class FakeTransaction:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.is_active = True

    async def commit(self) -> None:
        self.commits += 1
        self.is_active = False

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.is_active = False


class FakeSession:
    def __init__(self) -> None:
        self.transaction = FakeTransaction()
        self.closed = 0

    async def begin(self) -> FakeTransaction:
        return self.transaction

    async def close(self) -> None:
        self.closed += 1


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    @property
    def session_factory(self):
        return lambda: self._session

    @property
    def settings(self):
        return Settings()


@pytest.mark.asyncio
async def test_unit_of_work_commits_success_and_closes_session():
    session = FakeSession()
    async with UnitOfWork(FakeDatabase(session)) as unit_of_work:
        assert unit_of_work.repositories is not None

    assert session.transaction.commits == 1
    assert session.transaction.rollbacks == 0
    assert session.closed == 1


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_failure_and_closes_session():
    session = FakeSession()
    with pytest.raises(LookupError, match="boom"):
        async with UnitOfWork(FakeDatabase(session)):
            raise LookupError("boom")

    assert session.transaction.commits == 0
    assert session.transaction.rollbacks == 1
    assert session.closed == 1


class ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@pytest.mark.asyncio
async def test_event_append_locks_run_allocates_sequence_and_updates_status_atomically():
    run_id = uuid.uuid4()
    event_id = uuid.uuid4()
    run = Run(
        id=run_id,
        conversation_id=uuid.uuid4(),
        status="running",
        request_payload={},
        next_event_sequence=7,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [None, None]
    session.execute.return_value = ScalarResult(run)
    repository = RunEventRepository(session, max_payload_bytes=1024)

    event = await repository.append(
        event_id=event_id,
        run_id=run_id,
        event_type="run.completed",
        payload={"artifact_id": str(uuid.uuid4())},
        run_status="completed",
    )

    lock_statement = session.execute.await_args.args[0]
    assert lock_statement._for_update_arg is not None
    assert run.next_event_sequence == 8
    assert run.status == "completed"
    assert event.sequence == 8
    assert event.id == event_id
    session.add.assert_called_once_with(event)
    session.flush.assert_awaited_once()
    assert session.commit.await_count == 0


@pytest.mark.asyncio
async def test_event_append_is_idempotent_but_rejects_event_id_reuse():
    run_id = uuid.uuid4()
    event_id = uuid.uuid4()
    existing = RunEvent(
        id=event_id,
        run_id=run_id,
        sequence=1,
        event_type="run.started",
        schema_version=1,
        payload={"source": "api"},
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.return_value = existing
    repository = RunEventRepository(session, max_payload_bytes=1024)

    replayed = await repository.append(
        event_id=event_id,
        run_id=run_id,
        event_type="run.started",
        payload={"source": "api"},
    )
    assert replayed is existing
    assert session.execute.await_count == 0
    assert session.flush.await_count == 0

    with pytest.raises(EventIdConflictError):
        await repository.append(
            event_id=event_id,
            run_id=run_id,
            event_type="run.failed",
            payload={"source": "api"},
        )


@pytest.mark.asyncio
async def test_repository_rejects_binary_or_scientific_payload_before_flush():
    session = AsyncMock()
    session.add = MagicMock()
    repository = RunRepository(session, max_payload_bytes=1024)
    run = Run(
        conversation_id=uuid.uuid4(),
        request_payload={"dataset": b"large-binary"},
    )
    with pytest.raises(ForbiddenPersistenceTypeError):
        await repository.add(run)
    assert session.add.call_count == 0
    assert session.flush.await_count == 0
