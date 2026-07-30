from __future__ import annotations

import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnicell_agent.memory.runtime import PostgresMemoryRuntime
from omnicell_agent.memory.service import MemoryService
from omnicell_agent.memory.types import MemoryKind
from omnicell_agent.runs.memory import RunMemoryPreparationError

from ._fake_store import (
    FakeRepositories,
    NOW,
    unit_of_work_factory,
)


def _run(
    *,
    mode: str,
    worker_id: str = "worker-1",
    attempt: int = 4,
    selected_memories: list[dict[str, object]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        worker_id=worker_id,
        attempt=attempt,
        lease_expires_at=NOW + timedelta(minutes=5),
        request_payload={
            "memory_mode": mode,
            "selected_memories": selected_memories or [],
        },
    )


@pytest.mark.asyncio
async def test_snapshot_requires_current_attempt_fence_before_repository_reads() -> None:
    run = _run(mode="default", worker_id="old-worker")
    repositories = SimpleNamespace(
        memory_snapshots=SimpleNamespace(
            get_by_run_for_update=AsyncMock()
        )
    )
    runtime = PostgresMemoryRuntime(
        lambda: None,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    with pytest.raises(RunMemoryPreparationError) as captured:
        await runtime.prepare_snapshot(
            repositories=repositories,
            run=run,
            goal="继续当前任务",
            worker_id="current-worker",
            expected_attempt=run.attempt,
        )

    assert captured.value.error_code == "memory_attempt_fence_lost"
    repositories.memory_snapshots.get_by_run_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_snapshot_replays_persisted_order_without_reselection() -> None:
    first_item_id, second_item_id = uuid4(), uuid4()
    first_version_id, second_version_id = uuid4(), uuid4()
    run = _run(
        mode="selected",
        selected_memories=[
            {
                "item_id": second_item_id,
                "version_id": second_version_id,
            },
            {
                "item_id": first_item_id,
                "version_id": first_version_id,
            },
        ],
    )
    snapshot = SimpleNamespace(
        id=uuid4(),
        mode="selected",
        outcome="loaded",
        query_sha256=hashlib.sha256(
            "继续当前任务".encode("utf-8")
        ).hexdigest(),
        content_bytes=23,
        degraded_code=None,
    )
    rows = [
        SimpleNamespace(
            item_id=first_item_id,
            version_id=first_version_id,
            version_number=1,
            content_sha256="a" * 64,
            kind="response_preference",
            source_kind="explicit",
            selection_reason="selected",
        ),
        SimpleNamespace(
            item_id=second_item_id,
            version_id=second_version_id,
            version_number=3,
            content_sha256="b" * 64,
            kind="project_context",
            source_kind="corrected",
            selection_reason="selected",
        ),
    ]
    repositories = SimpleNamespace(
        memory_snapshots=SimpleNamespace(
            get_by_run_for_update=AsyncMock(return_value=snapshot),
        ),
        memory_inputs=SimpleNamespace(
            list_for_snapshot=AsyncMock(return_value=rows),
        ),
        memory_settings=SimpleNamespace(get=AsyncMock()),
        memory_items=SimpleNamespace(get=AsyncMock()),
        memory_versions=SimpleNamespace(get_by_id=AsyncMock()),
    )
    runtime = PostgresMemoryRuntime(
        lambda: None,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    prepared = await runtime.prepare_snapshot(
        repositories=repositories,
        run=run,
        goal="继续当前任务",
        worker_id=run.worker_id,
        expected_attempt=run.attempt,
    )

    assert prepared is not None
    assert [item.item_id for item in prepared.inputs] == [
        first_item_id,
        second_item_id,
    ]
    repositories.memory_settings.get.assert_not_awaited()
    repositories.memory_items.get.assert_not_awaited()
    repositories.memory_versions.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_snapshot_fails_closed_when_exact_version_is_stale() -> None:
    item_id, stale_version_id = uuid4(), uuid4()
    run = _run(
        mode="selected",
        selected_memories=[
            {"item_id": item_id, "version_id": stale_version_id}
        ],
    )
    item = SimpleNamespace(
        id=item_id,
        status="active",
        current_version=2,
        expires_at=None,
        kind="project_context",
        dataset_scope={},
    )
    stale = SimpleNamespace(
        id=stale_version_id,
        item_id=item_id,
        version_number=1,
        content="旧版本背景。",
        sha256="c" * 64,
        source_kind="explicit",
        source_refs=[],
    )
    repositories = SimpleNamespace(
        memory_snapshots=SimpleNamespace(
            get_by_run_for_update=AsyncMock(return_value=None),
        ),
            memory_settings=SimpleNamespace(
                get_for_share=AsyncMock(
                    return_value=SimpleNamespace(
                        use_enabled=True,
                        provider_consent_version="memory-provider-v1",
                        provider_consent_at=NOW,
                    )
                ),
                get_or_create_for_update=AsyncMock(),
            ),
        memory_items=SimpleNamespace(get=AsyncMock(return_value=item)),
        memory_versions=SimpleNamespace(
            get_by_id=AsyncMock(return_value=stale)
        ),
    )
    runtime = PostgresMemoryRuntime(
        lambda: None,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    with pytest.raises(RunMemoryPreparationError) as captured:
        await runtime.prepare_snapshot(
            repositories=repositories,
            run=run,
            goal="使用精确记忆",
            worker_id=run.worker_id,
            expected_attempt=run.attempt,
        )

    assert captured.value.error_code == "memory_selection_invalid"


@pytest.mark.asyncio
async def test_selected_snapshot_budgets_canonical_encoded_context() -> None:
    repositories = FakeRepositories()
    unit_of_work = unit_of_work_factory(repositories)
    service = MemoryService(unit_of_work, clock=lambda: NOW)
    await service.get_settings()
    await service.set_provider_consent(
        granted=True,
        statement_version="memory-provider-v1",
        confirmed=True,
        expected_version=1,
    )
    await service.update_settings(use_enabled=True, expected_version=2)
    memory = await service.create_memory(
        kind=MemoryKind.RESPONSE_PREFERENCE,
        content='回答中保留这些引号与反斜线：' + '"\\' * 100,
    )
    run = _run(
        mode="selected",
        selected_memories=[
            {
                "item_id": memory.item_id,
                "version_id": memory.version_id,
            }
        ],
    )
    runtime = PostgresMemoryRuntime(
        unit_of_work,
        service=service,
        clock=lambda: NOW,
        max_context_bytes=256,
    )

    with pytest.raises(RunMemoryPreparationError) as captured:
        await runtime.prepare_snapshot(
            repositories=repositories,
            run=run,
            goal="使用精确记忆",
            worker_id=run.worker_id,
            expected_attempt=run.attempt,
        )

    assert captured.value.error_code == "memory_context_limit_exceeded"
    assert repositories.memory_snapshots.rows == {}


@pytest.mark.asyncio
async def test_default_mode_degrades_without_consent_instead_of_blocking_run() -> None:
    repositories = FakeRepositories()
    unit_of_work = unit_of_work_factory(repositories)
    service = MemoryService(unit_of_work, clock=lambda: NOW)
    await service.get_settings()
    run = _run(mode="default")
    runtime = PostgresMemoryRuntime(
        unit_of_work,
        service=service,
        clock=lambda: NOW,
    )

    prepared = await runtime.prepare_snapshot(
        repositories=repositories,
        run=run,
        goal="普通问答仍应继续",
        worker_id=run.worker_id,
        expected_attempt=run.attempt,
    )

    assert prepared is not None
    assert prepared.outcome == "degraded"
    assert prepared.degraded_code == "memory_retrieval_unavailable"
    assert prepared.inputs == ()
    assert prepared.content_bytes == 0
