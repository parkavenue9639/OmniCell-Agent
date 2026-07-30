from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from importlib.resources import files
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command

from omnicell_agent.persistence.migrations import _alembic_config
from omnicell_agent.persistence.models import (
    APP_SCHEMA,
    LOCAL_DEFAULT_MEMORY_SCOPE,
    Base,
    MemoryItem,
    MemorySettings,
    MemoryVersion,
    RunMemoryInput,
    RunMemoryForgetIntent,
    RunMemoryProposal,
    RunMemorySearch,
    RunMemorySnapshot,
)
from omnicell_agent.persistence.repositories import (
    MemoryItemRepository,
    MemoryVersionRepository,
    Repositories,
    RunMemoryInputRepository,
    RunMemoryForgetIntentRepository,
    RunMemoryProposalRepository,
    RunMemorySearchRepository,
    RunMemorySnapshotRepository,
)
from omnicell_agent.memory.validation import memory_fingerprint


@dataclass(frozen=True)
class Settings:
    dsn: str = "postgresql://user:password@127.0.0.1:5432/omnicell"
    app_schema: str = APP_SCHEMA

    @property
    def sqlalchemy_dsn(self) -> str:
        return self.dsn.replace("postgresql://", "postgresql+psycopg://")


def test_memory_models_separate_plaintext_versions_from_identity_only_inputs() -> None:
    expected_tables = {
        "memory_settings",
        "memory_items",
        "memory_versions",
        "memory_suppressions",
        "run_memory_snapshots",
        "run_memory_inputs",
        "run_memory_searches",
        "run_memory_forget_intents",
        "run_memory_proposals",
    }
    assert expected_tables <= {
        table.name for table in Base.metadata.tables.values()
    }
    assert "content" in MemoryVersion.__table__.columns
    assert "content" not in RunMemoryInput.__table__.columns
    assert not RunMemoryInput.__table__.columns.version_id.foreign_keys
    assert MemorySettings.__table__.columns.use_enabled.default.arg is False
    assert MemorySettings.__table__.columns.generation_enabled.default.arg is False
    assert MemorySettings.__table__.columns.tools_enabled.default.arg is False


def test_memory_migration_creates_default_off_plane_and_identity_snapshot(
    capsys,
) -> None:
    command.upgrade(_alembic_config(Settings()), "head", sql=True)
    sql = capsys.readouterr().out
    assert f"CREATE TABLE {APP_SCHEMA}.memory_settings" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.memory_items" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.memory_versions" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.memory_suppressions" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.run_memory_snapshots" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.run_memory_inputs" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.run_memory_forget_intents" in sql
    assert f"CREATE TABLE {APP_SCHEMA}.run_memory_proposals" in sql
    assert "VALUES ('local-default', false, false, false, 1, 1)" in sql
    assert "ck_memory_versions_memory_version_content_length" in sql
    assert "fk_run_memory_inputs_version_id" not in sql


def test_memory_migration_is_packaged() -> None:
    migration = files("omnicell_agent.persistence").joinpath(
        "alembic/versions/20260726_0003_cross_conversation_memory.py"
    )
    assert migration.is_file()


@pytest.mark.asyncio
async def test_memory_version_repository_enforces_digest_and_immutable_body_bound(
) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    content = "用户偏好中文解释，并保留 English identifier。"
    version = MemoryVersion(
        item_id=uuid.uuid4(),
        version_number=1,
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        fingerprint=memory_fingerprint(content),
        source_kind="explicit",
        dataset_scope={},
        source_refs=[],
    )
    repository = MemoryVersionRepository(session, max_metadata_bytes=1024)

    assert await repository.add(version) is version
    session.add.assert_called_once_with(version)
    session.flush.assert_awaited_once()

    version.sha256 = "0" * 64
    with pytest.raises(ValueError, match="does not match"):
        await repository.add(version)

    version.sha256 = hashlib.sha256(version.content.encode("utf-8")).hexdigest()
    version.content = "x" * 8001
    with pytest.raises(ValueError, match="1..8000"):
        await repository.add(version)


@pytest.mark.asyncio
async def test_memory_repositories_validate_identity_and_are_uow_bundled() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    repositories = Repositories(session)
    assert isinstance(repositories.memory_items, MemoryItemRepository)
    assert isinstance(repositories.memory_versions, MemoryVersionRepository)
    assert isinstance(repositories.memory_snapshots, RunMemorySnapshotRepository)
    assert isinstance(repositories.memory_inputs, RunMemoryInputRepository)
    assert isinstance(repositories.memory_searches, RunMemorySearchRepository)
    assert isinstance(
        repositories.memory_forget_intents,
        RunMemoryForgetIntentRepository,
    )
    assert isinstance(
        repositories.memory_proposals,
        RunMemoryProposalRepository,
    )

    item = MemoryItem(
        scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
        kind="profile_fact",
        stable_key="profile.language",
        status="active",
        current_version=1,
        dataset_scope={},
    )
    assert await repositories.memory_items.add(item) is item

    snapshot = RunMemorySnapshot(
        run_id=uuid.uuid4(),
        scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
        mode="default",
        outcome="loaded",
        query_sha256="a" * 64,
        policy_version=1,
        worker_id="worker-1",
        attempt=1,
        content_bytes=128,
    )
    assert await repositories.memory_snapshots.add(snapshot) is snapshot

    memory_input = RunMemoryInput(
        snapshot_id=uuid.uuid4(),
        item_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_number=1,
        content_sha256="b" * 64,
        kind="profile_fact",
        source_kind="explicit",
        selection_reason="selected",
        ordinal=0,
    )
    assert await repositories.memory_inputs.add(memory_input) is memory_input

    invalid = RunMemorySnapshot(
        run_id=uuid.uuid4(),
        scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
        mode="unexpected",
        outcome="loaded",
        query_sha256="a" * 64,
        policy_version=1,
        worker_id="worker-1",
        attempt=1,
        content_bytes=0,
    )
    with pytest.raises(ValueError, match="unsupported memory snapshot mode"):
        await repositories.memory_snapshots.add(invalid)


@pytest.mark.asyncio
async def test_memory_search_repository_rejects_non_identity_payload() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repository = RunMemorySearchRepository(session, max_payload_bytes=4096)
    identity = {
        "item_id": str(uuid.uuid4()),
        "version_id": str(uuid.uuid4()),
        "version_number": 1,
        "content_sha256": "c" * 64,
        "kind": "project_context",
        "source_kind": "explicit",
        "selection_reason": "tool_search",
        "content": "不应进入 identity-only 结果",
    }
    search = RunMemorySearch(
        run_id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        tool_call_id="search-1",
        request_sha256="d" * 64,
        worker_id="worker-1",
        attempt=1,
        result_count=1,
        result_identities=[identity],
    )

    with pytest.raises(ValueError, match="identity-only"):
        await repository.add(search)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_memory_forget_repository_rejects_non_identity_payload() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repository = RunMemoryForgetIntentRepository(
        session,
        max_payload_bytes=4096,
    )
    intent = RunMemoryForgetIntent(
        run_id=uuid.uuid4(),
        tool_call_id="forget-1",
        request_sha256="e" * 64,
        worker_id="worker-1",
        attempt=1,
        memory_identity={
            "item_id": str(uuid.uuid4()),
            "version_id": str(uuid.uuid4()),
            "version_number": 1,
            "content_sha256": "f" * 64,
            "kind": "project_context",
            "source_kind": "explicit",
            "selection_reason": "selected",
            "content": "不得持久化",
        },
    )

    with pytest.raises(ValueError, match="identity-only"):
        await repository.add(intent)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_memory_proposal_repository_rejects_non_identity_payload() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repository = RunMemoryProposalRepository(
        session,
        max_payload_bytes=4096,
    )
    proposal = RunMemoryProposal(
        run_id=uuid.uuid4(),
        tool_call_id="proposal-1",
        request_sha256="1" * 64,
        worker_id="worker-1",
        attempt=1,
        memory_identity={
            "item_id": str(uuid.uuid4()),
            "version_id": str(uuid.uuid4()),
            "version_number": 1,
            "content_sha256": "2" * 64,
            "kind": "project_context",
            "source_kind": "proposed",
            "selection_reason": "selected",
            "content": "不得持久化",
        },
    )

    with pytest.raises(ValueError, match="identity-only"):
        await repository.add(proposal)
    session.add.assert_not_called()
