from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnicell_agent.memory.errors import (
    MemoryAttemptFenceError,
    MemoryConflictError,
    MemoryProposalLimitError,
    MemoryProviderConsentRequiredError,
    MemorySourceInvalidError,
    MemorySuppressedError,
)
from omnicell_agent.memory.service import MemoryService
from omnicell_agent.memory.types import (
    MemoryKind,
    MemorySelectionReason,
    MemoryStatus,
)
from omnicell_agent.persistence.models import RunMemorySnapshot

from ._fake_store import FakeRepositories, NOW, unit_of_work_factory


@pytest.mark.asyncio
async def test_settings_default_off_and_require_persisted_provider_consent() -> None:
    repositories = FakeRepositories()
    service = MemoryService(
        unit_of_work_factory(repositories),
        clock=lambda: NOW,
    )

    initial = await service.get_settings()
    assert initial.version == 1
    assert initial.use_enabled is False
    assert initial.generation_enabled is False
    assert initial.tools_enabled is False
    assert initial.provider_consent_granted is False

    with pytest.raises(
        MemoryProviderConsentRequiredError,
        match="provider consent",
    ):
        await service.update_settings(
            use_enabled=True,
            expected_version=1,
        )

    unchanged = await service.get_settings()
    assert unchanged.version == 1
    assert unchanged.use_enabled is False

    with pytest.raises(
        MemoryProviderConsentRequiredError,
        match="声明版本已失效",
    ):
        await service.set_provider_consent(
            granted=True,
            statement_version="memory-provider-v0",
            confirmed=True,
            expected_version=1,
        )

    consented = await service.set_provider_consent(
        granted=True,
        statement_version="memory-provider-v1",
        confirmed=True,
        expected_version=1,
    )
    assert consented.provider_consent_granted is True
    assert consented.version == 2

    enabled = await service.update_settings(
        use_enabled=True,
        generation_enabled=True,
        tools_enabled=True,
        expected_version=2,
    )
    assert enabled.version == 3
    assert enabled.use_enabled is True
    assert enabled.generation_enabled is True
    assert enabled.tools_enabled is True

    revoked = await service.set_provider_consent(
        granted=False,
        statement_version="memory-provider-v1",
        confirmed=True,
        expected_version=3,
    )
    assert revoked.provider_consent_granted is False
    assert revoked.use_enabled is False
    assert revoked.generation_enabled is True
    assert revoked.tools_enabled is True


@pytest.mark.asyncio
async def test_memory_lifecycle_is_versioned_forgettable_and_suppresses_relearning() -> None:
    repositories = FakeRepositories()
    service = MemoryService(
        unit_of_work_factory(repositories),
        clock=lambda: NOW,
    )
    await service.get_settings()
    await service.set_provider_consent(
        granted=True,
        statement_version="memory-provider-v1",
        confirmed=True,
        expected_version=1,
    )
    await service.update_settings(
        use_enabled=True,
        generation_enabled=True,
        tools_enabled=True,
        expected_version=2,
    )

    created = await service.create_memory(
        kind=MemoryKind.RESPONSE_PREFERENCE,
        stable_key="response.language",
        content="回答时优先使用中文，并保留必要的 English identifier。",
        expires_at=NOW + timedelta(days=30),
    )
    assert created.status is MemoryStatus.ACTIVE
    assert created.current_version == 1
    assert created.content is not None
    original_version_id = created.version_id
    original_content = created.content

    assert await service.get_memory(created.item_id) == created
    assert await service.list_memories() == (created,)
    assert await service.list_memories(
        kind=MemoryKind.RESPONSE_PREFERENCE,
        status=MemoryStatus.ACTIVE,
    ) == (created,)

    with pytest.raises(MemoryConflictError) as stale:
        await service.correct_memory(
            created.item_id,
            expected_version=99,
            content="用中文回答。",
        )
    assert stale.value.error_code == "memory_version_conflict"

    corrected = await service.correct_memory(
        created.item_id,
        expected_version=1,
        content="默认用中文回答，技术标识符保持原始 English 形式。",
    )
    assert corrected.current_version == 2
    assert corrected.version_id != original_version_id
    assert corrected.content != original_content
    original = await repositories.memory_versions.get_by_id(
        original_version_id  # type: ignore[arg-type]
    )
    assert original is not None
    assert original.content == original_content

    forgotten = await service.forget_memory(
        created.item_id,
        expected_version=2,
    )
    assert forgotten.status is MemoryStatus.REVOKED
    assert await service.list_memories(status=MemoryStatus.ACTIVE) == ()

    purged = await service.purge_memory(
        created.item_id,
        expected_version=2,
    )
    assert purged.status is MemoryStatus.PURGED
    assert purged.current_version is None
    assert purged.version_id is None
    assert purged.content_sha256 is None
    assert purged.content is None
    assert purged.stable_key == f"purged:{created.item_id}"
    assert await repositories.memory_versions.list_for_item(created.item_id) == []
    assert len(repositories.memory_suppressions.rows) == 2

    run_id = uuid4()
    message_id = uuid4()
    repositories.runs.rows[run_id] = SimpleNamespace(
        id=run_id,
        conversation_id=uuid4(),
        worker_id="worker-1",
        attempt=1,
        status="running",
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    repositories.events.rows[run_id] = [
        SimpleNamespace(
            event_type="message.completed",
            payload={
                "message_id": str(message_id),
                "role": "user",
                "content": corrected.content,
            },
        )
    ]

    with pytest.raises(MemorySuppressedError) as suppressed:
        await service.propose_from_run(
            run_id=run_id,
            worker_id="worker-1",
            expected_attempt=1,
            tool_call_id="memory-propose-relearn",
            kind=MemoryKind.RESPONSE_PREFERENCE,
            source_message_id=message_id,
        )
    assert suppressed.value.error_code == "memory_suppressed"


@pytest.mark.asyncio
async def test_purge_suppresses_proposal_from_the_same_source_message() -> None:
    repositories = FakeRepositories()
    service = MemoryService(
        unit_of_work_factory(repositories),
        clock=lambda: NOW,
    )
    await service.get_settings()
    await service.set_provider_consent(
        granted=True,
        statement_version="memory-provider-v1",
        confirmed=True,
        expected_version=1,
    )
    await service.update_settings(
        generation_enabled=True,
        tools_enabled=True,
        expected_version=2,
    )
    run_id = uuid4()
    old_message_id = uuid4()
    old_body = "项目报告默认使用中文。"
    repositories.runs.rows[run_id] = SimpleNamespace(
        id=run_id,
        conversation_id=uuid4(),
        worker_id="worker-1",
        attempt=1,
        status="running",
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    repositories.events.rows[run_id] = [
        SimpleNamespace(
            event_type="message.completed",
            payload={
                "message_id": str(old_message_id),
                "role": "user",
                "content": old_body,
            },
        )
    ]

    proposed = await service.propose_from_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=1,
        tool_call_id="proposal-before-purge",
        kind=MemoryKind.RESPONSE_PREFERENCE,
        source_message_id=old_message_id,
    )
    await service.purge_memory(proposed.item_id, expected_version=1)
    with pytest.raises(MemorySuppressedError) as suppressed:
        await service.propose_from_run(
            run_id=run_id,
            worker_id="worker-1",
            expected_attempt=1,
            tool_call_id="proposal-relearn",
            kind=MemoryKind.RESPONSE_PREFERENCE,
            source_message_id=old_message_id,
        )

    assert suppressed.value.error_code == "memory_suppressed"
    assert len(repositories.memory_suppressions.rows) == 2
    assert all(
        old_body not in version.content
        for version in repositories.memory_versions.rows.values()
    )


@pytest.mark.asyncio
async def test_duplicate_active_body_is_rejected_across_stable_keys() -> None:
    repositories = FakeRepositories()
    service = MemoryService(unit_of_work_factory(repositories), clock=lambda: NOW)
    body = "本项目使用 PostgreSQL 保存 checkpoint。"

    await service.create_memory(
        kind=MemoryKind.PROJECT_CONTEXT,
        stable_key="project.database",
        content=body,
    )
    with pytest.raises(MemoryConflictError):
        await service.create_memory(
            kind=MemoryKind.PROJECT_CONTEXT,
            stable_key="project.checkpointer",
            content=body,
        )


@pytest.mark.asyncio
async def test_agent_proposal_is_attempt_fenced_and_idempotent_after_disable() -> None:
    repositories = FakeRepositories()
    service = MemoryService(unit_of_work_factory(repositories), clock=lambda: NOW)
    await service.get_settings()
    await service.update_settings(
        generation_enabled=True,
        tools_enabled=True,
        expected_version=1,
    )
    run_id, message_id, assistant_message_id = uuid4(), uuid4(), uuid4()
    repositories.runs.rows[run_id] = SimpleNamespace(
        id=run_id,
        conversation_id=uuid4(),
        worker_id="worker-1",
        attempt=2,
        status="running",
        lease_expires_at=NOW + timedelta(minutes=5),
        request_payload={"goal": "记住项目背景"},
    )
    repositories.events.rows[run_id] = [
        SimpleNamespace(
            event_type="message.completed",
            payload={
                "message_id": str(message_id),
                "role": "user",
                "content": "这个项目使用 Cafe\u0301 和本地 PostgreSQL。",
            },
        ),
        SimpleNamespace(
            event_type="message.completed",
            payload={
                "message_id": str(assistant_message_id),
                "role": "assistant",
                "content": "模型推断用户偏好所有回答都使用学术写作风格。",
            },
        ),
    ]

    with pytest.raises(
        MemorySourceInvalidError,
        match="一条用户 message identity",
    ):
        await service.propose_from_run(
            run_id=run_id,
            worker_id="worker-1",
            expected_attempt=2,
            tool_call_id="proposal-multiple-sources",
            kind=MemoryKind.PROJECT_CONTEXT,
            source_message_id=[message_id, assistant_message_id],  # type: ignore[arg-type]
        )
    with pytest.raises(MemoryAttemptFenceError):
        await service.propose_from_run(
            run_id=run_id,
            worker_id="stale-worker",
            expected_attempt=2,
            tool_call_id="proposal-1",
            kind=MemoryKind.PROJECT_CONTEXT,
            source_message_id=message_id,
        )
    with pytest.raises(MemorySourceInvalidError):
        await service.propose_from_run(
            run_id=run_id,
            worker_id="worker-1",
            expected_attempt=2,
            tool_call_id="proposal-from-assistant",
            kind=MemoryKind.RESPONSE_PREFERENCE,
            source_message_id=assistant_message_id,
        )
    first = await service.propose_from_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=2,
        tool_call_id="proposal-1",
        kind=MemoryKind.PROJECT_CONTEXT,
        source_message_id=message_id,
    )
    proposed = await service.get_memory(first.item_id)
    assert proposed.content == "这个项目使用 Cafe\u0301 和本地 PostgreSQL。"
    with pytest.raises(
        MemoryProposalLimitError,
    ) as proposal_limit:
        await service.propose_from_run(
            run_id=run_id,
            worker_id="worker-1",
            expected_attempt=2,
            tool_call_id="proposal-2",
            kind=MemoryKind.PROJECT_CONTEXT,
            source_message_id=message_id,
        )
    assert proposal_limit.value.error_code == "memory_proposal_limit_reached"
    assert proposal_limit.value.retryable is False
    assert "本 Run 不再创建候选" in proposal_limit.value.recovery_hint
    await service.approve_memory(first.item_id, expected_version=1)
    await service.correct_memory(
        first.item_id,
        expected_version=1,
        content="这个项目使用本地 PostgreSQL，并采用独立 schema。",
    )
    await service.purge_memory(first.item_id, expected_version=2)
    settings = await service.get_settings()
    await service.update_settings(
        generation_enabled=False,
        tools_enabled=False,
        expected_version=settings.version,
    )
    replay = await service.propose_from_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=2,
        tool_call_id="proposal-1",
        kind=MemoryKind.PROJECT_CONTEXT,
        source_message_id=message_id,
    )
    assert replay.item_id == first.item_id
    assert replay.version_id == first.version_id
    stored = repositories.memory_proposals.rows[(run_id, "proposal-1")]
    assert "content" not in stored.memory_identity


@pytest.mark.asyncio
async def test_agent_proposal_preserves_exact_source_across_retrieval() -> None:
    repositories = FakeRepositories()
    service = MemoryService(unit_of_work_factory(repositories), clock=lambda: NOW)
    await service.get_settings()
    await service.update_settings(
        generation_enabled=True,
        tools_enabled=True,
        expected_version=1,
    )
    run_id, message_id = uuid4(), uuid4()
    source = "  这个项目使用 Cafe\u0301 和本地 PostgreSQL。\n"
    repositories.runs.rows[run_id] = SimpleNamespace(
        id=run_id,
        conversation_id=uuid4(),
        worker_id="worker-1",
        attempt=1,
        status="running",
        lease_expires_at=NOW + timedelta(minutes=5),
        request_payload={"goal": "记录项目背景"},
    )
    repositories.events.rows[run_id] = [
        SimpleNamespace(
            event_type="message.completed",
            payload={
                "message_id": str(message_id),
                "role": "user",
                "content": source,
            },
        )
    ]

    identity = await service.propose_from_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=1,
        tool_call_id="proposal-exact-source",
        kind=MemoryKind.PROJECT_CONTEXT,
        source_message_id=message_id,
    )
    await service.approve_memory(identity.item_id, expected_version=1)

    candidates = await service._active_candidates(  # noqa: SLF001
        repositories,
        kinds=(MemoryKind.PROJECT_CONTEXT,),
        selection_reason=MemorySelectionReason.TOOL_SEARCH,
    )

    assert len(candidates) == 1
    assert candidates[0].content == source
    assert candidates[0].identity.content_sha256 == identity.content_sha256


@pytest.mark.asyncio
async def test_run_search_is_identity_only_attempt_fenced_and_idempotent() -> None:
    repositories = FakeRepositories()
    service = MemoryService(unit_of_work_factory(repositories), clock=lambda: NOW)
    await service.get_settings()
    await service.set_provider_consent(
        granted=True,
        statement_version="memory-provider-v1",
        confirmed=True,
        expected_version=1,
    )
    await service.update_settings(
        use_enabled=True,
        tools_enabled=True,
        expected_version=2,
    )
    memory = await service.create_memory(
        kind=MemoryKind.PROJECT_CONTEXT,
        content="本机数据库采用 PostgreSQL。",
    )
    run_id = uuid4()
    repositories.runs.rows[run_id] = SimpleNamespace(
        id=run_id,
        conversation_id=uuid4(),
        worker_id="worker-1",
        attempt=3,
        status="running",
        lease_expires_at=NOW + timedelta(minutes=5),
        request_payload={"goal": "检查 PostgreSQL 数据库"},
    )
    await repositories.memory_snapshots.add(
        RunMemorySnapshot(
            run_id=run_id,
            scope_key="local-default",
            mode="default",
            outcome="loaded",
            query_sha256="a" * 64,
            policy_version=1,
            worker_id="worker-1",
            attempt=3,
            content_bytes=1,
        )
    )

    with pytest.raises(MemoryAttemptFenceError):
        await service.search_for_run(
            run_id=run_id,
            worker_id="old-worker",
            expected_attempt=3,
            tool_call_id="search-1",
            limit=8,
        )
    first = await service.search_for_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=3,
        tool_call_id="search-1",
        limit=8,
    )
    assert first and first[0].item_id == memory.item_id
    await service.forget_memory(memory.item_id, expected_version=1)
    settings = await service.get_settings()
    await service.update_settings(
        use_enabled=False,
        tools_enabled=False,
        expected_version=settings.version,
    )
    replay = await service.search_for_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=3,
        tool_call_id="search-1",
        limit=8,
    )
    assert replay == first
    persisted = repositories.memory_searches.rows[(run_id, "search-1")]
    assert all("content" not in value for value in persisted.result_identities)
    with pytest.raises(MemorySourceInvalidError):
        await service.search_for_run(
            run_id=run_id,
            worker_id="worker-1",
            expected_attempt=3,
            tool_call_id="search-science",
            kinds=(MemoryKind.SCIENTIFIC_OBSERVATION,),
            limit=8,
        )


@pytest.mark.asyncio
async def test_forget_request_replays_identity_after_target_is_purged() -> None:
    repositories = FakeRepositories()
    service = MemoryService(unit_of_work_factory(repositories), clock=lambda: NOW)
    await service.get_settings()
    await service.update_settings(tools_enabled=True, expected_version=1)
    memory = await service.create_memory(
        kind=MemoryKind.PROFILE_FACT,
        content="用户的常用称呼是小木。",
    )
    run_id = uuid4()
    repositories.runs.rows[run_id] = SimpleNamespace(
        id=run_id,
        conversation_id=uuid4(),
        worker_id="worker-1",
        attempt=1,
        status="running",
        lease_expires_at=NOW + timedelta(minutes=5),
        request_payload={"goal": "忘记称呼"},
    )
    first = await service.request_forget_from_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=1,
        tool_call_id="forget-1",
        item_id=memory.item_id,
        version_id=memory.version_id,  # type: ignore[arg-type]
    )
    await service.purge_memory(memory.item_id, expected_version=1)
    settings = await service.get_settings()
    await service.update_settings(
        tools_enabled=False,
        expected_version=settings.version,
    )
    replay = await service.request_forget_from_run(
        run_id=run_id,
        worker_id="worker-1",
        expected_attempt=1,
        tool_call_id="forget-1",
        item_id=memory.item_id,
        version_id=memory.version_id,  # type: ignore[arg-type]
    )

    assert replay == first
    stored = repositories.memory_forget_intents.rows[(run_id, "forget-1")]
    assert "content" not in stored.memory_identity


@pytest.mark.asyncio
async def test_scientific_correction_cannot_move_between_dataset_scopes() -> None:
    repositories = FakeRepositories()
    service = MemoryService(unit_of_work_factory(repositories), clock=lambda: NOW)
    artifact_a, artifact_b = uuid4(), uuid4()

    def provenance(artifact_id):
        return [
            {
                "source_verified": True,
                "conversation_id": str(uuid4()),
                "run_id": str(uuid4()),
                "artifact_id": str(artifact_id),
                "message_ids": [str(uuid4())],
            }
        ]

    memory = await service.create_memory(
        kind=MemoryKind.SCIENTIFIC_OBSERVATION,
        content="历史数据集 A 的 cluster 2 曾呈现 T-cell marker。",
        dataset_scope={"artifact_id": str(artifact_a)},
        provenance=provenance(artifact_a),
    )
    with pytest.raises(MemorySourceInvalidError):
        await service.correct_memory(
            memory.item_id,
            expected_version=1,
            content="历史数据集 B 的 cluster 2 曾呈现 T-cell marker。",
            dataset_scope={"artifact_id": str(artifact_b)},
            provenance=provenance(artifact_b),
        )
