from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from omnicell_agent.memory import MemoryService, PostgresMemoryRuntime
from omnicell_agent.agent.hooks import DispatchAuthorizationInvalidatedError
from omnicell_agent.memory.errors import (
    MemoryProposalLimitError,
    MemorySuppressedError,
)
from omnicell_agent.memory.service import _source_message_fingerprint
from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.models import (
    LOCAL_DEFAULT_MEMORY_SCOPE,
    Conversation,
    MemorySuppression,
    Run,
)
from omnicell_agent.persistence.repositories import MemorySettingsRepository
from omnicell_agent.runs.events import (
    EventType,
    MemoryContextLoadedPayload,
)
from omnicell_agent.runs.memory import RunMemoryPreparationError


TEST_DSN = os.environ.get("OMNICELL_TEST_POSTGRES_DSN", "").strip()

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="设置 OMNICELL_TEST_POSTGRES_DSN 后运行 Memory PostgreSQL 测试",
    ),
]


@pytest_asyncio.fixture
async def postgres_settings():
    suffix = uuid.uuid4().hex[:10]
    settings = PostgresSettings(
        dsn=TEST_DSN,
        app_schema=f"omnicell_memory_test_{suffix}",
        checkpoint_schema=f"omnicell_memory_checkpoint_test_{suffix}",
        pool_min_size=1,
        pool_max_size=4,
    )
    try:
        yield settings
    finally:
        async with await psycopg.AsyncConnection.connect(
            settings.psycopg_conninfo,
            autocommit=True,
        ) as connection:
            for schema_name in (
                settings.checkpoint_schema,
                settings.app_schema,
            ):
                await connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )


@pytest.mark.asyncio
async def test_memory_snapshot_event_share_transaction_and_honor_fence_and_forget(
    postgres_settings: PostgresSettings,
) -> None:
    persistence = PersistenceRuntime(postgres_settings)
    await persistence.initialize_schemas()
    await persistence.open()
    now = datetime.now(UTC)
    service = MemoryService(persistence.unit_of_work, clock=lambda: now)
    memory_runtime = PostgresMemoryRuntime(
        persistence.unit_of_work,
        service=service,
        clock=lambda: now,
    )
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    try:
        await service.set_provider_consent(
            granted=True,
            statement_version="memory-provider-v1",
            confirmed=True,
        )
        await service.update_settings(use_enabled=True)
        memory = await service.create_memory(
            kind="response_preference",
            stable_key="response.preferred_name",
            content="用户希望在问候时被称为小木。",
        )
        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.conversations.add(
                Conversation(
                    id=conversation_id,
                    workspace_uri=f"workspace://{conversation_id}",
                )
            )
            await repositories.runs.add(
                Run(
                    id=run_id,
                    conversation_id=conversation_id,
                    request_key="memory-pg-transaction",
                    status="running",
                    attempt=2,
                    worker_id="owner-a",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={
                        "instruction": "请按跨会话称呼偏好问好",
                        "memory_mode": "default",
                        "selected_memories": [],
                    },
                    checkpoint_thread_id=str(conversation_id),
                )
            )

        with pytest.raises(RunMemoryPreparationError) as fenced:
            async with persistence.unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                run = await repositories.runs.get_for_update(run_id)
                assert run is not None
                await memory_runtime.prepare_snapshot(
                    repositories=repositories,
                    run=run,
                    goal="请按跨会话称呼偏好问好",
                    worker_id="stale-owner",
                    expected_attempt=2,
                )
        assert fenced.value.error_code == "memory_attempt_fence_lost"

        with pytest.raises(RuntimeError, match="force application rollback"):
            async with persistence.unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                run = await repositories.runs.get_for_update(run_id)
                assert run is not None
                prepared = await memory_runtime.prepare_snapshot(
                    repositories=repositories,
                    run=run,
                    goal="请按跨会话称呼偏好问好",
                    worker_id="owner-a",
                    expected_attempt=2,
                )
                assert prepared is not None
                await repositories.events.append(
                    event_id=uuid.uuid4(),
                    run_id=run_id,
                    event_type=EventType.MEMORY_CONTEXT_LOADED.value,
                    payload=MemoryContextLoadedPayload(
                        snapshot_id=prepared.snapshot_id,
                        mode=prepared.mode,
                        outcome=prepared.outcome,
                        inputs=[
                            item.public_identity()
                            for item in prepared.inputs
                        ],
                        content_bytes=prepared.content_bytes,
                    ).model_dump(mode="json"),
                )
                raise RuntimeError("force application rollback")

        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            assert await repositories.memory_snapshots.get_by_run(run_id) is None
            assert await repositories.events.replay(run_id, after_sequence=0) == []
            run = await repositories.runs.get(run_id)
            assert run is not None
            assert run.next_event_sequence == 0

        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            assert run is not None
            prepared = await memory_runtime.prepare_snapshot(
                repositories=repositories,
                run=run,
                goal="请按跨会话称呼偏好问好",
                worker_id="owner-a",
                expected_attempt=2,
            )
            assert prepared is not None
            await repositories.events.append(
                event_id=uuid.uuid4(),
                run_id=run_id,
                event_type=EventType.MEMORY_CONTEXT_LOADED.value,
                payload=MemoryContextLoadedPayload(
                    snapshot_id=prepared.snapshot_id,
                    mode=prepared.mode,
                    outcome=prepared.outcome,
                    inputs=[
                        item.public_identity()
                        for item in prepared.inputs
                    ],
                    content_bytes=prepared.content_bytes,
                ).model_dump(mode="json"),
            )

        resolution = await memory_runtime.resolver(run_id).resolve([])
        assert [item.content for item in resolution.memories] == [
            "用户希望在问候时被称为小木。"
        ]
        assert resolution.pre_dispatch is not None
        await resolution.pre_dispatch()
        corrected = await service.correct_memory(
            memory.item_id,
            expected_version=1,
            content="用户希望在问候时被称为阿木。",
        )
        assert corrected.current_version == 2
        frozen_resolution = await memory_runtime.resolver(run_id).resolve([])
        assert [item.content for item in frozen_resolution.memories] == [
            "用户希望在问候时被称为小木。"
        ]
        await resolution.pre_dispatch()
        await asyncio.wait_for(
            service.forget_memory(memory.item_id, expected_version=2),
            timeout=1,
        )
        with pytest.raises(DispatchAuthorizationInvalidatedError):
            await resolution.pre_dispatch()
        assert (await memory_runtime.resolver(run_id).resolve([])).memories == ()

        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            replay = await repositories.events.replay(run_id, after_sequence=0)
            assert len(replay) == 1
            assert replay[0].event_type == EventType.MEMORY_CONTEXT_LOADED.value
            assert "content" not in replay[0].payload
            assert replay[0].payload["inputs"][0]["version_id"] == str(
                memory.version_id
            )
    finally:
        await persistence.close()


@pytest.mark.asyncio
async def test_new_proposal_rechecks_source_after_purge_linearization_lock(
    postgres_settings: PostgresSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = PersistenceRuntime(postgres_settings)
    await persistence.initialize_schemas()
    await persistence.open()
    now = datetime.now(UTC)
    service = MemoryService(persistence.unit_of_work, clock=lambda: now)
    conversation_id, run_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    proposal_task: asyncio.Task[object] | None = None
    try:
        await service.update_settings(
            generation_enabled=True,
            tools_enabled=True,
        )
        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.conversations.add(
                Conversation(
                    id=conversation_id,
                    workspace_uri=f"workspace://{conversation_id}",
                )
            )
            await repositories.runs.add(
                Run(
                    id=run_id,
                    conversation_id=conversation_id,
                    request_key="memory-source-linearization",
                    status="running",
                    attempt=1,
                    worker_id="owner-a",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={
                        "goal": "记住报告语言偏好",
                        "memory_mode": "off",
                        "selected_memories": [],
                    },
                    checkpoint_thread_id=str(conversation_id),
                )
            )
            await repositories.events.append(
                event_id=uuid.uuid4(),
                run_id=run_id,
                event_type="message.completed",
                payload={
                    "message_id": str(message_id),
                    "role": "user",
                    "content": "项目报告默认使用中文。",
                },
            )

        async with persistence.unit_of_work() as purge_uow:
            repositories = purge_uow.repositories
            assert repositories is not None
            await repositories.memory_settings.get_or_create_for_update()
            proposal_reached_settings_lock = asyncio.Event()
            original = MemorySettingsRepository.get_or_create_for_update

            async def observed_settings_lock(repository):
                proposal_reached_settings_lock.set()
                return await original(repository)

            monkeypatch.setattr(
                MemorySettingsRepository,
                "get_or_create_for_update",
                observed_settings_lock,
            )
            proposal_task = asyncio.create_task(
                service.propose_from_run(
                    run_id=run_id,
                    worker_id="owner-a",
                    expected_attempt=1,
                    tool_call_id="proposal-after-purge-linearization",
                    kind="response_preference",
                    source_message_id=message_id,
                )
            )
            await asyncio.wait_for(
                proposal_reached_settings_lock.wait(),
                timeout=2,
            )
            await asyncio.sleep(0)
            assert not proposal_task.done()
            await repositories.memory_suppressions.add(
                MemorySuppression(
                    scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                    fingerprint=_source_message_fingerprint(message_id),
                    item_id=None,
                    reason="user_purge_source",
                )
            )

        with pytest.raises(MemorySuppressedError):
            await asyncio.wait_for(proposal_task, timeout=2)
        assert await service.list_memories() == ()
    finally:
        if proposal_task is not None and not proposal_task.done():
            proposal_task.cancel()
            await asyncio.gather(proposal_task, return_exceptions=True)
        await persistence.close()


@pytest.mark.asyncio
async def test_concurrent_proposals_create_exactly_one_candidate(
    postgres_settings: PostgresSettings,
) -> None:
    persistence = PersistenceRuntime(postgres_settings)
    await persistence.initialize_schemas()
    await persistence.open()
    now = datetime.now(UTC)
    service = MemoryService(persistence.unit_of_work, clock=lambda: now)
    conversation_id, run_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        await service.update_settings(
            generation_enabled=True,
            tools_enabled=True,
        )
        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.conversations.add(
                Conversation(
                    id=conversation_id,
                    workspace_uri=f"workspace://{conversation_id}",
                )
            )
            await repositories.runs.add(
                Run(
                    id=run_id,
                    conversation_id=conversation_id,
                    request_key="memory-concurrent-proposal",
                    status="running",
                    attempt=1,
                    worker_id="owner-a",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={
                        "goal": "保存长期项目背景",
                        "memory_mode": "off",
                        "selected_memories": [],
                    },
                    checkpoint_thread_id=str(conversation_id),
                )
            )
            await repositories.events.append(
                event_id=uuid.uuid4(),
                run_id=run_id,
                event_type="message.completed",
                payload={
                    "message_id": str(message_id),
                    "role": "user",
                    "content": "这个项目长期使用 PostgreSQL 保存状态。",
                },
            )

        results = await asyncio.gather(
            *(
                service.propose_from_run(
                    run_id=run_id,
                    worker_id="owner-a",
                    expected_attempt=1,
                    tool_call_id=tool_call_id,
                    kind="project_context",
                    source_message_id=message_id,
                )
                for tool_call_id in ("proposal-a", "proposal-b")
            ),
            return_exceptions=True,
        )

        candidates = [value for value in results if not isinstance(value, Exception)]
        failures = [value for value in results if isinstance(value, Exception)]
        assert len(candidates) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], MemoryProposalLimitError)
        assert failures[0].retryable is False
        proposed = await service.list_memories(status="proposed")
        assert len(proposed) == 1
        assert proposed[0].item_id == candidates[0].item_id
    finally:
        await persistence.close()


@pytest.mark.asyncio
async def test_memory_control_results_replay_after_lifecycle_changes_and_reopen(
    postgres_settings: PostgresSettings,
) -> None:
    persistence = PersistenceRuntime(postgres_settings)
    await persistence.initialize_schemas()
    await persistence.open()
    now = datetime.now(UTC)
    service = MemoryService(persistence.unit_of_work, clock=lambda: now)
    runtime = PostgresMemoryRuntime(
        persistence.unit_of_work,
        service=service,
        clock=lambda: now,
    )
    conversation_id, run_id, message_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    try:
        await service.set_provider_consent(
            granted=True,
            statement_version="memory-provider-v1",
            confirmed=True,
        )
        await service.update_settings(
            use_enabled=True,
            generation_enabled=True,
            tools_enabled=True,
        )
        searchable = await service.create_memory(
            kind="project_context",
            content="本机数据库采用 PostgreSQL。",
        )
        forgettable = await service.create_memory(
            kind="profile_fact",
            content="用户的常用称呼是小木。",
        )
        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.conversations.add(
                Conversation(
                    id=conversation_id,
                    workspace_uri=f"workspace://{conversation_id}",
                )
            )
            await repositories.runs.add(
                Run(
                    id=run_id,
                    conversation_id=conversation_id,
                    request_key="memory-control-replay",
                    status="running",
                    attempt=1,
                    worker_id="owner-a",
                    lease_expires_at=now + timedelta(minutes=5),
                    request_payload={
                        "goal": "记住本地分析约定并检查 PostgreSQL",
                        "memory_mode": "default",
                        "selected_memories": [],
                    },
                    checkpoint_thread_id=str(conversation_id),
                )
            )
            await repositories.events.append(
                event_id=uuid.uuid4(),
                run_id=run_id,
                event_type="message.completed",
                payload={
                    "message_id": str(message_id),
                    "role": "user",
                    "content": "本项目的报告默认使用中文。",
                },
            )
            run = await repositories.runs.get_for_update(run_id)
            assert run is not None
            await runtime.prepare_snapshot(
                repositories=repositories,
                run=run,
                goal=run.request_payload["goal"],
                worker_id="owner-a",
                expected_attempt=1,
            )

        proposal = await service.propose_from_run(
            run_id=run_id,
            worker_id="owner-a",
            expected_attempt=1,
            tool_call_id="proposal-1",
            kind="response_preference",
            source_message_id=message_id,
        )
        with pytest.raises(
            MemoryProposalLimitError,
        ) as proposal_limit:
            await service.propose_from_run(
                run_id=run_id,
                worker_id="owner-a",
                expected_attempt=1,
                tool_call_id="proposal-2",
                kind="response_preference",
                source_message_id=message_id,
            )
        assert proposal_limit.value.retryable is False
        search = await service.search_for_run(
            run_id=run_id,
            worker_id="owner-a",
            expected_attempt=1,
            tool_call_id="search-1",
            kinds=("project_context",),
            limit=8,
        )
        forget = await service.request_forget_from_run(
            run_id=run_id,
            worker_id="owner-a",
            expected_attempt=1,
            tool_call_id="forget-1",
            item_id=forgettable.item_id,
            version_id=forgettable.version_id,  # type: ignore[arg-type]
        )

        await service.approve_memory(proposal.item_id, expected_version=1)
        await service.correct_memory(
            proposal.item_id,
            expected_version=1,
            content="本项目的报告默认使用中文，并保留 English identifier。",
        )
        await service.purge_memory(proposal.item_id, expected_version=2)
        await service.purge_memory(searchable.item_id, expected_version=1)
        await service.purge_memory(forgettable.item_id, expected_version=1)
        with pytest.raises(MemorySuppressedError):
            await service.propose_from_run(
                run_id=run_id,
                worker_id="owner-a",
                expected_attempt=1,
                tool_call_id="proposal-relearn",
                kind="response_preference",
                source_message_id=message_id,
            )
        settings = await service.get_settings()
        await service.update_settings(
            use_enabled=False,
            generation_enabled=False,
            tools_enabled=False,
            expected_version=settings.version,
        )

        await persistence.close()
        await persistence.open()
        replay_service = MemoryService(
            persistence.unit_of_work,
            clock=lambda: now,
        )
        assert await replay_service.propose_from_run(
            run_id=run_id,
            worker_id="owner-a",
            expected_attempt=1,
            tool_call_id="proposal-1",
            kind="response_preference",
            source_message_id=message_id,
        ) == proposal
        assert await replay_service.search_for_run(
            run_id=run_id,
            worker_id="owner-a",
            expected_attempt=1,
            tool_call_id="search-1",
            kinds=("project_context",),
            limit=8,
        ) == search
        assert await replay_service.request_forget_from_run(
            run_id=run_id,
            worker_id="owner-a",
            expected_attempt=1,
            tool_call_id="forget-1",
            item_id=forgettable.item_id,
            version_id=forgettable.version_id,  # type: ignore[arg-type]
        ) == forget
        async with persistence.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            durable_values = [
                (
                    await repositories.memory_proposals.get_for_update(
                        run_id=run_id,
                        tool_call_id="proposal-1",
                    )
                ).memory_identity,
                (
                    await repositories.memory_searches.get_for_update(
                        run_id=run_id,
                        tool_call_id="search-1",
                    )
                ).result_identities,
                (
                    await repositories.memory_forget_intents.get_for_update(
                        run_id=run_id,
                        tool_call_id="forget-1",
                    )
                ).memory_identity,
            ]
            durable_identities = [
                identity
                for value in durable_values
                for identity in (value if isinstance(value, list) else [value])
            ]
            assert all(
                "content" not in identity and "body" not in identity
                for identity in durable_identities
            )
    finally:
        await persistence.close()
