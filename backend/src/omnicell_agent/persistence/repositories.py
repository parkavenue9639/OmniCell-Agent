"""Repositories for application-owned persistence.

Repositories flush when a generated value or constraint must be observed, but
never commit.  The Unit of Work is the only transaction owner.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnicell_agent.runs.status import (
    InvalidRunTransitionError,
    ReviewStatus,
    RunStatus,
    TaskStatus,
    is_terminal_run_status,
    validate_run_transition,
)

from .guards import ensure_payload_safe
from .models import (
    Artifact,
    CheckpointAnchor,
    Conversation,
    Review,
    Run,
    RunEvent,
    RunTask,
)


DEFAULT_EVENT_PAYLOAD_MAX_BYTES = 128 * 1024
DEFAULT_METADATA_MAX_BYTES = 64 * 1024
DEFAULT_LIST_LIMIT = 100


class RepositoryError(RuntimeError):
    pass


class RunNotFoundError(RepositoryError):
    pass


class EventIdConflictError(RepositoryError):
    pass


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")


def _validate_offset(offset: int) -> None:
    if offset < 0:
        raise ValueError("offset must be non-negative")


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> Conversation:
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self._session.get(Conversation, conversation_id)

    async def list(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Conversation]:
        _validate_limit(limit)
        _validate_offset(offset)
        statement = select(Conversation)
        if status is not None:
            statement = statement.where(Conversation.status == status)
        result = await self._session.scalars(
            statement.order_by(Conversation.created_at.desc(), Conversation.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()


class RunRepository:
    def __init__(self, session: AsyncSession, *, max_payload_bytes: int) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def add(self, run: Run) -> Run:
        ensure_payload_safe(
            run.request_payload,
            max_bytes=self._max_payload_bytes,
            label="run request payload",
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> Run | None:
        return await self._session.get(Run, run_id)

    async def get_for_update(self, run_id: uuid.UUID) -> Run | None:
        return (
            await self._session.execute(
                select(Run).where(Run.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def get_for_conversation(
        self,
        run_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
    ) -> Run | None:
        return (
            await self._session.execute(
                select(Run).where(
                    Run.id == run_id,
                    Run.conversation_id == conversation_id,
                )
            )
        ).scalar_one_or_none()

    async def get_by_request_key(
        self,
        *,
        conversation_id: uuid.UUID,
        request_key: str,
    ) -> Run | None:
        if not request_key:
            raise ValueError("request_key must not be empty")
        return (
            await self._session.execute(
                select(Run).where(
                    Run.conversation_id == conversation_id,
                    Run.request_key == request_key,
                )
            )
        ).scalar_one_or_none()

    async def get_active_for_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> Run | None:
        active_statuses = (
            RunStatus.PENDING.value,
            RunStatus.RUNNING.value,
            RunStatus.REVIEW_REQUIRED.value,
            RunStatus.CANCELLING.value,
        )
        return (
            await self._session.execute(
                select(Run).where(
                    Run.conversation_id == conversation_id,
                    Run.status.in_(active_statuses),
                )
            )
        ).scalar_one_or_none()

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Run]:
        _validate_limit(limit)
        _validate_offset(offset)
        result = await self._session.scalars(
            select(Run)
            .where(Run.conversation_id == conversation_id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def list_recoverable(
        self,
        *,
        at: datetime,
        limit: int = DEFAULT_LIST_LIMIT,
        after_created_at: datetime | None = None,
        after_id: uuid.UUID | None = None,
    ) -> Sequence[Run]:
        """List non-terminal runs without a currently valid lease.

        This is only a recovery candidate query.  It deliberately does not
        claim a lease or decide whether a candidate should be resumed.
        """

        _validate_limit(limit)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        if (after_created_at is None) != (after_id is None):
            raise ValueError("recovery cursor fields must be provided together")
        if (
            after_created_at is not None
            and (after_created_at.tzinfo is None or after_created_at.utcoffset() is None)
        ):
            raise ValueError("after_created_at must be timezone-aware")
        non_terminal = tuple(
            status.value
            for status in (
                RunStatus.PENDING,
                RunStatus.RUNNING,
                RunStatus.REVIEW_REQUIRED,
                RunStatus.CANCELLING,
            )
        )
        statement = select(Run).where(
                Run.status.in_(non_terminal),
                or_(Run.lease_expires_at.is_(None), Run.lease_expires_at <= at),
        )
        if after_created_at is not None and after_id is not None:
            statement = statement.where(
                or_(
                    Run.created_at > after_created_at,
                    and_(Run.created_at == after_created_at, Run.id > after_id),
                )
            )
        result = await self._session.scalars(
            statement.order_by(Run.created_at, Run.id).limit(limit)
        )
        return result.all()

    async def list_with_expired_lease(
        self,
        *,
        at: datetime,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Run]:
        _validate_limit(limit)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        result = await self._session.scalars(
            select(Run)
            .where(
                Run.worker_id.is_not(None),
                Run.lease_expires_at.is_not(None),
                Run.lease_expires_at <= at,
                Run.status.in_(
                    (
                        RunStatus.RUNNING.value,
                        RunStatus.REVIEW_REQUIRED.value,
                        RunStatus.CANCELLING.value,
                    )
                ),
            )
            .order_by(Run.lease_expires_at, Run.id)
            .limit(limit)
        )
        return result.all()


class RunEventRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        max_payload_bytes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    async def append(
        self,
        *,
        event_id: uuid.UUID,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        schema_version: int = 1,
        run_status: RunStatus | str | None = None,
        error_summary: str | None = None,
    ) -> RunEvent:
        """Append an event with a per-run database-serialized sequence.

        The run row is the lock scope: appends for one run serialize while
        unrelated runs remain concurrent.  A repeated event ID returns the
        original row only when its immutable envelope is identical.
        """

        ensure_payload_safe(
            payload,
            max_bytes=self._max_payload_bytes,
            label="run event payload",
        )

        target_status = RunStatus(run_status) if run_status is not None else None
        target_status_value = target_status.value if target_status is not None else None

        existing = await self._get_by_id(event_id)
        if existing is not None:
            self._validate_idempotent(
                existing,
                run_id,
                event_type,
                payload,
                schema_version,
                target_status_value,
                error_summary,
            )
            return existing

        run = (
            await self._session.execute(
                select(Run).where(Run.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            raise RunNotFoundError(f"Run {run_id} does not exist")

        # The first check avoids taking a row lock for the common retry path;
        # the second closes the race between concurrent retries for one run.
        existing = await self._get_by_id(event_id)
        if existing is not None:
            self._validate_idempotent(
                existing,
                run_id,
                event_type,
                payload,
                schema_version,
                target_status_value,
                error_summary,
            )
            return existing

        current_status = RunStatus(run.status)
        if is_terminal_run_status(current_status):
            raise InvalidRunTransitionError(
                f"终态 run 不能追加新事件：{current_status.value}"
            )
        if target_status is not None:
            validate_run_transition(current_status, target_status)

        transition_at: datetime | None = None
        if target_status is not None:
            transition_at = self._clock()
            if transition_at.tzinfo is None or transition_at.utcoffset() is None:
                raise RuntimeError("run event clock must be timezone-aware")

        next_sequence = run.next_event_sequence + 1
        run.next_event_sequence = next_sequence
        if target_status is not None:
            assert transition_at is not None
            run.status = target_status.value
            if target_status is RunStatus.RUNNING and run.started_at is None:
                run.started_at = transition_at
            if target_status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
                if run.cancel_requested_at is None:
                    run.cancel_requested_at = transition_at
            if is_terminal_run_status(target_status):
                run.finished_at = transition_at
        if error_summary is not None:
            run.error_summary = error_summary

        event = RunEvent(
            id=event_id,
            run_id=run_id,
            sequence=next_sequence,
            event_type=event_type,
            schema_version=schema_version,
            payload=payload,
            run_status=target_status_value,
            error_summary=error_summary,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def replay(
        self,
        run_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> Sequence[RunEvent]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        result = await self._session.scalars(
            select(RunEvent)
            .where(
                RunEvent.run_id == run_id,
                RunEvent.sequence > after_sequence,
            )
            .order_by(RunEvent.sequence)
            .limit(limit)
        )
        return result.all()

    async def _get_by_id(self, event_id: uuid.UUID) -> RunEvent | None:
        return await self._session.get(RunEvent, event_id)

    @staticmethod
    def _validate_idempotent(
        existing: RunEvent,
        run_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        schema_version: int,
        run_status: str | None,
        error_summary: str | None,
    ) -> None:
        if (
            existing.run_id != run_id
            or existing.event_type != event_type
            or existing.payload != payload
            or existing.schema_version != schema_version
            or existing.run_status != run_status
            or existing.error_summary != error_summary
        ):
            raise EventIdConflictError(
                f"Event ID {existing.id} was reused with a different envelope"
            )


class ArtifactRepository:
    def __init__(self, session: AsyncSession, *, max_metadata_bytes: int) -> None:
        self._session = session
        self._max_metadata_bytes = max_metadata_bytes

    async def add(self, artifact: Artifact) -> Artifact:
        ensure_payload_safe(
            artifact.artifact_metadata,
            max_bytes=self._max_metadata_bytes,
            label="artifact metadata",
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def get(self, artifact_id: uuid.UUID) -> Artifact | None:
        return await self._session.get(Artifact, artifact_id)

    async def get_for_conversation(
        self,
        artifact_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
    ) -> Artifact | None:
        return (
            await self._session.execute(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.conversation_id == conversation_id,
                )
            )
        ).scalar_one_or_none()

    async def get_many_for_conversation(
        self,
        artifact_ids: Sequence[uuid.UUID],
        *,
        conversation_id: uuid.UUID,
    ) -> Sequence[Artifact]:
        """Load exactly the requested conversation-owned artifacts.

        Callers that care about request order must rebuild it from the returned
        rows. PostgreSQL is the ownership boundary; this method deliberately
        does not route the lookup through a paginated conversation listing.
        """

        normalized_ids = tuple(dict.fromkeys(artifact_ids))
        if not normalized_ids:
            return ()
        result = await self._session.scalars(
            select(Artifact).where(
                Artifact.conversation_id == conversation_id,
                Artifact.id.in_(normalized_ids),
            )
        )
        return result.all()

    async def get_by_uri_for_conversation(
        self,
        uri: str,
        *,
        conversation_id: uuid.UUID,
    ) -> Artifact | None:
        return (
            await self._session.execute(
                select(Artifact).where(
                    Artifact.uri == uri,
                    Artifact.conversation_id == conversation_id,
                )
            )
        ).scalar_one_or_none()

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        kind: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Artifact]:
        _validate_limit(limit)
        _validate_offset(offset)
        statement = select(Artifact).where(
            Artifact.conversation_id == conversation_id
        )
        if kind is not None:
            statement = statement.where(Artifact.kind == kind)
        result = await self._session.scalars(
            statement.order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def list_for_run(
        self,
        run_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
        kind: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Artifact]:
        _validate_limit(limit)
        _validate_offset(offset)
        statement = select(Artifact).where(
            Artifact.run_id == run_id,
            Artifact.conversation_id == conversation_id,
        )
        if kind is not None:
            statement = statement.where(Artifact.kind == kind)
        result = await self._session.scalars(
            statement.order_by(Artifact.created_at, Artifact.id)
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def list_for_run_context(
        self,
        run_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
    ) -> Sequence[Artifact]:
        """Load the artifacts owned by one run for resume/continue hydration."""

        result = await self._session.scalars(
            select(Artifact)
            .where(
                Artifact.run_id == run_id,
                Artifact.conversation_id == conversation_id,
            )
            .order_by(Artifact.created_at, Artifact.id)
        )
        return result.all()


class RunTaskRepository:
    def __init__(self, session: AsyncSession, *, max_payload_bytes: int) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def add(self, task: RunTask) -> RunTask:
        if task.status is None:
            task.status = TaskStatus.PENDING.value
        if task.request_payload is None:
            task.request_payload = {}
        ensure_payload_safe(
            task.request_payload,
            max_bytes=self._max_payload_bytes,
            label="run task request payload",
        )
        TaskStatus(task.status)
        self._session.add(task)
        await self._session.flush()
        return task

    async def get(
        self,
        task_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RunTask | None:
        return (
            await self._session.execute(
                select(RunTask).where(
                    RunTask.id == task_id,
                    RunTask.conversation_id == conversation_id,
                    RunTask.run_id == run_id,
                )
            )
        ).scalar_one_or_none()

    async def get_by_tool_call(
        self,
        *,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        tool_call_id: str,
    ) -> RunTask | None:
        return (
            await self._session.execute(
                select(RunTask).where(
                    RunTask.conversation_id == conversation_id,
                    RunTask.run_id == run_id,
                    RunTask.tool_call_id == tool_call_id,
                )
            )
        ).scalar_one_or_none()

    async def list_for_run(
        self,
        run_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[RunTask]:
        _validate_limit(limit)
        result = await self._session.scalars(
            select(RunTask)
            .where(
                RunTask.run_id == run_id,
                RunTask.conversation_id == conversation_id,
            )
            .order_by(RunTask.created_at, RunTask.id)
            .limit(limit)
        )
        return result.all()


class ReviewRepository:
    def __init__(self, session: AsyncSession, *, max_payload_bytes: int) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def add(self, review: Review) -> Review:
        if review.status is None:
            review.status = ReviewStatus.PENDING.value
        if review.request_payload is None:
            review.request_payload = {}
        if review.decision_payload is None:
            review.decision_payload = {}
        ensure_payload_safe(
            review.request_payload,
            max_bytes=self._max_payload_bytes,
            label="review request payload",
        )
        ensure_payload_safe(
            review.decision_payload,
            max_bytes=self._max_payload_bytes,
            label="review decision payload",
        )
        # Validate strings at the repository boundary even before the database
        # check constraint is reached.
        ReviewStatus(review.status)
        self._session.add(review)
        await self._session.flush()
        return review

    async def get(
        self,
        review_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> Review | None:
        return (
            await self._session.execute(
                select(Review).where(
                    Review.id == review_id,
                    Review.conversation_id == conversation_id,
                    Review.run_id == run_id,
                )
            )
        ).scalar_one_or_none()

    async def get_by_id(self, review_id: uuid.UUID) -> Review | None:
        return await self._session.get(Review, review_id)

    async def get_by_id_for_update(self, review_id: uuid.UUID) -> Review | None:
        return (
            await self._session.execute(
                select(Review)
                .where(Review.id == review_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_by_tool_call(
        self,
        *,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
        tool_call_id: str,
    ) -> Review | None:
        return (
            await self._session.execute(
                select(Review).where(
                    Review.conversation_id == conversation_id,
                    Review.run_id == run_id,
                    Review.tool_call_id == tool_call_id,
                )
            )
        ).scalar_one_or_none()

    async def list_for_run(
        self,
        run_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID,
        status: ReviewStatus | str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Review]:
        _validate_limit(limit)
        _validate_offset(offset)
        statement = select(Review).where(
            Review.run_id == run_id,
            Review.conversation_id == conversation_id,
        )
        if status is not None:
            statement = statement.where(Review.status == ReviewStatus(status).value)
        result = await self._session.scalars(
            statement.order_by(Review.created_at, Review.id)
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        status: ReviewStatus | str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[Review]:
        _validate_limit(limit)
        _validate_offset(offset)
        statement = select(Review).where(Review.conversation_id == conversation_id)
        if status is not None:
            statement = statement.where(Review.status == ReviewStatus(status).value)
        result = await self._session.scalars(
            statement.order_by(Review.created_at.desc(), Review.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()


class CheckpointAnchorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, anchor: CheckpointAnchor) -> CheckpointAnchor:
        self._session.add(anchor)
        await self._session.flush()
        return anchor

    async def list_for_checkpoint(
        self,
        *,
        thread_id: str,
        namespace: str,
        checkpoint_id: str,
    ) -> Sequence[CheckpointAnchor]:
        result = await self._session.scalars(
            select(CheckpointAnchor)
            .where(
                CheckpointAnchor.thread_id == thread_id,
                CheckpointAnchor.checkpoint_ns == namespace,
                CheckpointAnchor.checkpoint_id == checkpoint_id,
            )
            .order_by(CheckpointAnchor.created_at)
        )
        return result.all()


class Repositories:
    """Repositories bound to one Unit of Work session."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        event_payload_max_bytes: int = DEFAULT_EVENT_PAYLOAD_MAX_BYTES,
        metadata_max_bytes: int = DEFAULT_METADATA_MAX_BYTES,
    ) -> None:
        self.conversations = ConversationRepository(session)
        self.runs = RunRepository(session, max_payload_bytes=event_payload_max_bytes)
        self.events = RunEventRepository(session, max_payload_bytes=event_payload_max_bytes)
        self.artifacts = ArtifactRepository(session, max_metadata_bytes=metadata_max_bytes)
        self.tasks = RunTaskRepository(
            session, max_payload_bytes=event_payload_max_bytes
        )
        self.reviews = ReviewRepository(
            session, max_payload_bytes=event_payload_max_bytes
        )
        self.checkpoint_anchors = CheckpointAnchorRepository(session)
