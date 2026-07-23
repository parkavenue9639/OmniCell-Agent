from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from omnicell_agent.persistence.guards import ForbiddenPersistenceTypeError
from omnicell_agent.persistence.models import Artifact, Review, Run, RunEvent, RunTask
from omnicell_agent.persistence.repositories import (
    ArtifactRepository,
    EventIdConflictError,
    ReviewRepository,
    RunEventRepository,
    RunRepository,
    RunTaskRepository,
)
from omnicell_agent.runs.status import (
    InvalidRunTransitionError,
    ReviewStatus,
    RunStatus,
    TaskStatus,
)


class ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _constraint_names(model) -> set[str]:
    return {
        str(constraint.name)
        for constraint in model.__table__.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }


def test_lifecycle_models_have_status_constraints_identity_and_recovery_fields() -> None:
    assert {
        "attempt",
        "worker_id",
        "lease_expires_at",
        "last_heartbeat_at",
        "cancel_requested_at",
    } <= set(Run.__table__.columns.keys())
    assert {
        "ck_runs_run_status",
        "ck_runs_run_attempt_non_negative",
        "uq_runs_conversation_request_key",
    } <= _constraint_names(Run)
    run_indexes = {index.name: index for index in Run.__table__.indexes}
    assert "ix_runs_status_lease" in run_indexes
    active_index = run_indexes["uq_runs_one_active_per_conversation"]
    assert active_index.unique is True
    assert "review_required" in str(active_index.dialect_options["postgresql"]["where"])

    assert {
        "conversation_id",
        "run_id",
        "tool_call_id",
        "capability_name",
        "status",
        "request_payload",
    } <= set(RunTask.__table__.columns.keys())
    assert {
        "ck_run_tasks_run_task_status",
        "uq_run_tasks_run_tool_call",
    } <= _constraint_names(RunTask)

    assert {
        "conversation_id",
        "run_id",
        "capability_name",
        "tool_call_id",
        "checkpoint_thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "request_payload",
        "decision_payload",
        "decided_at",
    } <= set(Review.__table__.columns.keys())
    assert {
        "ck_reviews_review_status",
        "uq_reviews_run_tool_call",
    } <= _constraint_names(Review)

    assert {"run_status", "error_summary"} <= set(RunEvent.__table__.columns.keys())


@pytest.mark.asyncio
async def test_run_transition_sets_started_cancel_and_finished_timestamps() -> None:
    now = dt.datetime(2026, 7, 23, 1, 2, 3, tzinfo=dt.UTC)
    run = Run(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        status=RunStatus.PENDING.value,
        request_payload={},
        next_event_sequence=0,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [None] * 6
    session.execute.return_value = ScalarResult(run)
    repository = RunEventRepository(
        session,
        max_payload_bytes=1024,
        clock=lambda: now,
    )

    started = await repository.append(
        event_id=uuid.uuid4(),
        run_id=run.id,
        event_type="run.started",
        payload={},
        run_status=RunStatus.RUNNING,
    )
    assert run.status == RunStatus.RUNNING.value
    assert run.started_at == now
    assert run.finished_at is None
    assert started.run_status == RunStatus.RUNNING.value

    cancelling = await repository.append(
        event_id=uuid.uuid4(),
        run_id=run.id,
        event_type="run.cancel_requested",
        payload={},
        run_status=RunStatus.CANCELLING,
    )
    assert run.status == RunStatus.CANCELLING.value
    assert run.cancel_requested_at == now
    assert cancelling.run_status == RunStatus.CANCELLING.value

    cancelled = await repository.append(
        event_id=uuid.uuid4(),
        run_id=run.id,
        event_type="run.cancelled",
        payload={},
        run_status=RunStatus.CANCELLED,
    )
    assert run.status == RunStatus.CANCELLED.value
    assert run.finished_at == now
    assert cancelled.run_status == RunStatus.CANCELLED.value
    assert [started.sequence, cancelling.sequence, cancelled.sequence] == [1, 2, 3]


@pytest.mark.asyncio
async def test_run_transition_rejects_illegal_transition_without_allocating_sequence() -> None:
    run = Run(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        status=RunStatus.PENDING.value,
        request_payload={},
        next_event_sequence=4,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [None, None]
    session.execute.return_value = ScalarResult(run)
    repository = RunEventRepository(session, max_payload_bytes=1024)

    with pytest.raises(InvalidRunTransitionError, match="pending -> completed"):
        await repository.append(
            event_id=uuid.uuid4(),
            run_id=run.id,
            event_type="run.completed",
            payload={},
            run_status=RunStatus.COMPLETED,
        )

    assert run.status == RunStatus.PENDING.value
    assert run.next_event_sequence == 4
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_run_rejects_new_event_but_exact_event_retry_is_allowed() -> None:
    run_id = uuid.uuid4()
    existing = RunEvent(
        id=uuid.uuid4(),
        run_id=run_id,
        sequence=2,
        event_type="run.completed",
        schema_version=1,
        payload={"artifact_count": 1},
        run_status=RunStatus.COMPLETED.value,
        error_summary=None,
    )
    run = Run(
        id=run_id,
        conversation_id=uuid.uuid4(),
        status=RunStatus.COMPLETED.value,
        request_payload={},
        next_event_sequence=2,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.side_effect = [existing, None, None]
    session.execute.return_value = ScalarResult(run)
    repository = RunEventRepository(session, max_payload_bytes=1024)

    assert (
        await repository.append(
            event_id=existing.id,
            run_id=run_id,
            event_type=existing.event_type,
            payload=existing.payload,
            run_status=RunStatus.COMPLETED,
        )
        is existing
    )

    with pytest.raises(InvalidRunTransitionError, match="终态 run"):
        await repository.append(
            event_id=uuid.uuid4(),
            run_id=run_id,
            event_type="run.progress",
            payload={},
        )
    assert run.next_event_sequence == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "error_summary"),
    [
        (RunStatus.CANCELLED, "boom"),
        (RunStatus.FAILED, "different"),
        (RunStatus.FAILED, None),
    ],
)
async def test_event_retry_validates_transition_intent(
    run_status: RunStatus,
    error_summary: str | None,
) -> None:
    existing = RunEvent(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        sequence=1,
        event_type="run.failed",
        schema_version=1,
        payload={"source": "agent"},
        run_status=RunStatus.FAILED.value,
        error_summary="boom",
    )
    session = AsyncMock()
    session.get.return_value = existing
    repository = RunEventRepository(session, max_payload_bytes=1024)

    with pytest.raises(EventIdConflictError, match="different envelope"):
        await repository.append(
            event_id=existing.id,
            run_id=existing.run_id,
            event_type=existing.event_type,
            payload=existing.payload,
            run_status=run_status,
            error_summary=error_summary,
        )


@pytest.mark.asyncio
async def test_task_and_review_repositories_bound_all_payloads() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    task_repository = RunTaskRepository(session, max_payload_bytes=64)
    review_repository = ReviewRepository(session, max_payload_bytes=64)
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()

    with pytest.raises(ForbiddenPersistenceTypeError):
        await task_repository.add(
            RunTask(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_id="tool-1",
                capability_name="single_cell_analysis",
                status=TaskStatus.PENDING.value,
                request_payload={"raw": b"forbidden"},
            )
        )

    with pytest.raises(ForbiddenPersistenceTypeError):
        await review_repository.add(
            Review(
                conversation_id=conversation_id,
                run_id=run_id,
                capability_name="deep_cell_annotation",
                tool_call_id="tool-2",
                checkpoint_thread_id=f"conversation:{conversation_id}",
                checkpoint_ns="",
                checkpoint_id="checkpoint-1",
                status=ReviewStatus.APPROVED.value,
                request_payload={"question": "approve?"},
                decision_payload={"raw": b"forbidden"},
            )
        )

    session.add.assert_not_called()
    assert session.flush.await_count == 0


@pytest.mark.asyncio
async def test_ownership_queries_include_conversation_and_run_boundaries() -> None:
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    object_id = uuid.uuid4()
    session = AsyncMock()
    session.execute.return_value = ScalarResult(None)

    await RunRepository(session, max_payload_bytes=1024).get_for_conversation(
        run_id,
        conversation_id=conversation_id,
    )
    await ArtifactRepository(session, max_metadata_bytes=1024).get_for_conversation(
        object_id,
        conversation_id=conversation_id,
    )
    await RunTaskRepository(session, max_payload_bytes=1024).get(
        object_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    await ReviewRepository(session, max_payload_bytes=1024).get(
        object_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert "runs.id" in statements[0] and "runs.conversation_id" in statements[0]
    assert "artifacts.id" in statements[1] and "artifacts.conversation_id" in statements[1]
    assert all("conversation_id" in statement for statement in statements)
    assert all("run_id" in statement for statement in statements[2:])


@pytest.mark.asyncio
async def test_run_request_recovery_and_lease_queries_are_bounded_and_scoped() -> None:
    scalar_rows = MagicMock()
    scalar_rows.all.return_value = []
    session = AsyncMock()
    session.scalars.return_value = scalar_rows
    session.execute.return_value = ScalarResult(None)
    repository = RunRepository(session, max_payload_bytes=1024)
    conversation_id = uuid.uuid4()
    now = dt.datetime(2026, 7, 23, tzinfo=dt.UTC)

    await repository.get_by_request_key(
        conversation_id=conversation_id,
        request_key="request-1",
    )
    request_statement = str(session.execute.await_args.args[0])
    assert "runs.conversation_id" in request_statement
    assert "runs.request_key" in request_statement

    await repository.get_active_for_conversation(conversation_id)
    active_statement = str(session.execute.await_args.args[0])
    assert "runs.conversation_id" in active_statement
    assert "runs.status IN" in active_statement

    await repository.list_recoverable(at=now, limit=25)
    await repository.list_with_expired_lease(at=now, limit=25)
    await repository.list_recoverable(
        at=now,
        limit=25,
        after_created_at=now - dt.timedelta(minutes=1),
        after_id=uuid.uuid4(),
    )
    recoverable_statement = str(session.scalars.await_args_list[0].args[0])
    expired_statement = str(session.scalars.await_args_list[1].args[0])
    paged_statement = str(session.scalars.await_args_list[2].args[0])
    assert "runs.status IN" in recoverable_statement
    assert "runs.lease_expires_at IS NULL" in recoverable_statement
    assert "runs.worker_id IS NOT NULL" in expired_statement
    assert "runs.lease_expires_at IS NOT NULL" in expired_statement
    assert "LIMIT" in recoverable_statement and "LIMIT" in expired_statement
    assert "runs.created_at >" in paged_statement
    assert "runs.id >" in paged_statement

    with pytest.raises(ValueError, match="timezone-aware"):
        await repository.list_recoverable(at=dt.datetime(2026, 7, 23))
    with pytest.raises(ValueError, match="provided together"):
        await repository.list_recoverable(
            at=now,
            after_created_at=now,
        )


@pytest.mark.asyncio
async def test_artifact_task_and_review_lists_scope_run_to_conversation() -> None:
    scalar_rows = MagicMock()
    scalar_rows.all.return_value = []
    session = AsyncMock()
    session.scalars.return_value = scalar_rows
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()

    await ArtifactRepository(session, max_metadata_bytes=1024).list_for_run(
        run_id,
        conversation_id=conversation_id,
    )
    await RunTaskRepository(session, max_payload_bytes=1024).list_for_run(
        run_id,
        conversation_id=conversation_id,
    )
    await ReviewRepository(session, max_payload_bytes=1024).list_for_run(
        run_id,
        conversation_id=conversation_id,
    )

    statements = [str(call.args[0]) for call in session.scalars.await_args_list]
    assert all("run_id" in statement for statement in statements)
    assert all("conversation_id" in statement for statement in statements)
