"""Repositories for application-owned persistence.

Repositories flush when a generated value or constraint must be observed, but
never commit.  The Unit of Work is the only transaction owner.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import and_, delete as sqlalchemy_delete, or_, select
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
    LOCAL_DEFAULT_MEMORY_SCOPE,
    MEMORY_ITEM_STATUSES,
    MEMORY_KINDS,
    MEMORY_SELECTION_REASONS,
    MEMORY_SNAPSHOT_MODES,
    MEMORY_SNAPSHOT_OUTCOMES,
    MEMORY_SOURCE_KINDS,
    MemoryItem,
    MemorySettings,
    MemorySuppression,
    MemoryVersion,
    Review,
    Run,
    RunEvent,
    RunMemoryForgetIntent,
    RunMemoryInput,
    RunMemoryProposal,
    RunMemorySearch,
    RunMemorySnapshot,
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


class MemorySettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, settings: MemorySettings) -> MemorySettings:
        if not settings.scope_key:
            raise ValueError("memory settings scope_key must not be empty")
        if settings.use_enabled is None:
            settings.use_enabled = False
        if settings.generation_enabled is None:
            settings.generation_enabled = False
        if settings.tools_enabled is None:
            settings.tools_enabled = False
        if settings.version is None:
            settings.version = 1
        if settings.disclosure_epoch is None:
            settings.disclosure_epoch = 1
        if settings.version < 1:
            raise ValueError("memory settings version must be positive")
        if settings.disclosure_epoch < 1:
            raise ValueError("memory settings disclosure_epoch must be positive")
        self._session.add(settings)
        await self._session.flush()
        return settings

    async def get(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> MemorySettings | None:
        return await self._session.get(MemorySettings, scope_key)

    async def get_for_update(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> MemorySettings | None:
        return (
            await self._session.execute(
                select(MemorySettings)
                .where(MemorySettings.scope_key == scope_key)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_for_share(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> MemorySettings | None:
        return (
            await self._session.execute(
                select(MemorySettings)
                .where(MemorySettings.scope_key == scope_key)
                .execution_options(populate_existing=True)
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()

    async def get_or_create_for_update(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> MemorySettings:
        settings = await self.get_for_update(scope_key)
        if settings is not None:
            return settings
        return await self.add(MemorySettings(scope_key=scope_key))


class MemoryItemRepository:
    def __init__(self, session: AsyncSession, *, max_metadata_bytes: int) -> None:
        self._session = session
        self._max_metadata_bytes = max_metadata_bytes

    async def add(self, item: MemoryItem) -> MemoryItem:
        self._validate(item)
        self._session.add(item)
        await self._session.flush()
        return item

    async def update(self, item: MemoryItem) -> MemoryItem:
        self._validate(item)
        await self._session.flush()
        # ``updated_at`` has a SQL expression onupdate. SQLAlchemy expires
        # server-populated attributes after the flush; refresh explicitly so
        # callers can safely project the entity without implicit async I/O.
        await self._session.refresh(item)
        return item

    async def delete(self, item: MemoryItem) -> None:
        await self._session.delete(item)
        await self._session.flush()

    async def get(
        self,
        item_id: uuid.UUID,
        *,
        scope_key: str | None = None,
    ) -> MemoryItem | None:
        statement = select(MemoryItem).where(MemoryItem.id == item_id)
        if scope_key is not None:
            statement = statement.where(MemoryItem.scope_key == scope_key)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_for_update(
        self,
        item_id: uuid.UUID,
        *,
        scope_key: str | None = None,
    ) -> MemoryItem | None:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.id == item_id)
            .execution_options(populate_existing=True)
        )
        if scope_key is not None:
            statement = statement.where(MemoryItem.scope_key == scope_key)
        return (
            await self._session.execute(statement.with_for_update())
        ).scalar_one_or_none()

    async def get_by_stable_key(
        self,
        *,
        scope_key: str,
        stable_key: str,
    ) -> MemoryItem | None:
        return (
            await self._session.execute(
                select(MemoryItem).where(
                    MemoryItem.scope_key == scope_key,
                    MemoryItem.stable_key == stable_key,
                )
            )
        ).scalar_one_or_none()

    async def get_by_stable_key_for_update(
        self,
        *,
        scope_key: str,
        stable_key: str,
    ) -> MemoryItem | None:
        return (
            await self._session.execute(
                select(MemoryItem)
                .where(
                    MemoryItem.scope_key == scope_key,
                    MemoryItem.stable_key == stable_key,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_by_origin_for_update(
        self,
        *,
        run_id: uuid.UUID,
        tool_call_id: str,
    ) -> MemoryItem | None:
        return (
            await self._session.execute(
                select(MemoryItem)
                .where(
                    MemoryItem.origin_run_id == run_id,
                    MemoryItem.origin_tool_call_id == tool_call_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
        kind: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[MemoryItem]:
        _validate_limit(limit)
        _validate_offset(offset)
        statement = select(MemoryItem).where(MemoryItem.scope_key == scope_key)
        if kind is not None:
            if kind not in MEMORY_KINDS:
                raise ValueError(f"unsupported memory kind: {kind}")
            statement = statement.where(MemoryItem.kind == kind)
        if status is not None:
            if status not in MEMORY_ITEM_STATUSES:
                raise ValueError(f"unsupported memory item status: {status}")
            statement = statement.where(MemoryItem.status == status)
        result = await self._session.scalars(
            statement.order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.all()

    def _validate(self, item: MemoryItem) -> None:
        if not item.scope_key:
            raise ValueError("memory item scope_key must not be empty")
        if not item.stable_key or len(item.stable_key) > 255:
            raise ValueError("memory item stable_key must contain 1..255 characters")
        if item.kind not in MEMORY_KINDS:
            raise ValueError(f"unsupported memory kind: {item.kind}")
        if item.status not in MEMORY_ITEM_STATUSES:
            raise ValueError(f"unsupported memory item status: {item.status}")
        if item.current_version is not None and item.current_version < 1:
            raise ValueError("memory item current_version must be positive")
        origin_values = (
            item.origin_run_id,
            item.origin_attempt,
            item.origin_tool_call_id,
        )
        if any(value is not None for value in origin_values) and not all(
            value is not None for value in origin_values
        ):
            raise ValueError("memory item origin identity must be complete")
        if item.origin_attempt is not None and item.origin_attempt < 0:
            raise ValueError("memory item origin attempt must be non-negative")
        if item.origin_tool_call_id is not None and (
            not item.origin_tool_call_id
            or len(item.origin_tool_call_id) > 255
        ):
            raise ValueError(
                "memory item origin tool_call_id must contain 1..255 characters"
            )
        if item.dataset_scope is None:
            item.dataset_scope = {}
        ensure_payload_safe(
            item.dataset_scope,
            max_bytes=self._max_metadata_bytes,
            label="memory item dataset scope",
        )


class MemoryVersionRepository:
    def __init__(self, session: AsyncSession, *, max_metadata_bytes: int) -> None:
        self._session = session
        self._max_metadata_bytes = max_metadata_bytes

    async def add(self, version: MemoryVersion) -> MemoryVersion:
        self._validate(version)
        self._session.add(version)
        await self._session.flush()
        return version

    async def get_by_id(self, version_id: uuid.UUID) -> MemoryVersion | None:
        return await self._session.get(MemoryVersion, version_id)

    async def get_exact(
        self,
        *,
        item_id: uuid.UUID,
        version_number: int,
    ) -> MemoryVersion | None:
        return (
            await self._session.execute(
                select(MemoryVersion).where(
                    MemoryVersion.item_id == item_id,
                    MemoryVersion.version_number == version_number,
                )
            )
        ).scalar_one_or_none()

    async def list_for_item(
        self,
        item_id: uuid.UUID,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> Sequence[MemoryVersion]:
        _validate_limit(limit)
        result = await self._session.scalars(
            select(MemoryVersion)
            .where(MemoryVersion.item_id == item_id)
            .order_by(MemoryVersion.version_number.desc())
            .limit(limit)
        )
        return result.all()

    async def list_fingerprints_for_item(
        self,
        item_id: uuid.UUID,
    ) -> Sequence[str]:
        result = await self._session.scalars(
            select(MemoryVersion.fingerprint)
            .where(MemoryVersion.item_id == item_id)
            .distinct()
        )
        return result.all()

    async def current_exists_by_fingerprint(
        self,
        *,
        scope_key: str,
        fingerprint: str,
        exclude_item_id: uuid.UUID | None = None,
    ) -> bool:
        _validate_sha256(
            fingerprint,
            label="memory version fingerprint",
        )
        statement = (
            select(MemoryVersion.id)
            .join(MemoryItem, MemoryItem.id == MemoryVersion.item_id)
            .where(
                MemoryItem.scope_key == scope_key,
                MemoryItem.status.in_(("active", "proposed")),
                MemoryItem.current_version == MemoryVersion.version_number,
                MemoryVersion.fingerprint == fingerprint,
            )
            .limit(1)
        )
        if exclude_item_id is not None:
            statement = statement.where(MemoryItem.id != exclude_item_id)
        return await self._session.scalar(statement) is not None

    async def delete_for_item(self, item_id: uuid.UUID) -> int:
        result = await self._session.execute(
            sqlalchemy_delete(MemoryVersion).where(MemoryVersion.item_id == item_id)
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    def _validate(self, version: MemoryVersion) -> None:
        if version.version_number < 1:
            raise ValueError("memory version_number must be positive")
        if not 1 <= len(version.content) <= 8000:
            raise ValueError("memory content must contain 1..8000 characters")
        digest = hashlib.sha256(version.content.encode("utf-8")).hexdigest()
        if version.sha256 != digest:
            raise ValueError("memory version sha256 does not match content")
        _validate_sha256(
            version.fingerprint,
            label="memory version fingerprint",
        )
        if version.source_kind not in MEMORY_SOURCE_KINDS:
            raise ValueError(
                f"unsupported memory version source_kind: {version.source_kind}"
            )
        if version.dataset_scope is None:
            version.dataset_scope = {}
        ensure_payload_safe(
            version.dataset_scope,
            max_bytes=self._max_metadata_bytes,
            label="memory version dataset scope",
        )
        if version.source_refs is None:
            version.source_refs = []
        ensure_payload_safe(
            version.source_refs,
            max_bytes=self._max_metadata_bytes,
            label="memory version source refs",
        )


class MemorySuppressionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, suppression: MemorySuppression) -> MemorySuppression:
        _validate_sha256(
            suppression.fingerprint,
            label="memory suppression fingerprint",
        )
        if not suppression.scope_key:
            raise ValueError("memory suppression scope_key must not be empty")
        if not suppression.reason or len(suppression.reason) > 128:
            raise ValueError("memory suppression reason must contain 1..128 characters")
        self._session.add(suppression)
        await self._session.flush()
        return suppression

    async def exists(
        self,
        *,
        scope_key: str,
        fingerprint: str,
    ) -> bool:
        _validate_sha256(fingerprint, label="memory suppression fingerprint")
        found = await self._session.scalar(
            select(MemorySuppression.id)
            .where(
                MemorySuppression.scope_key == scope_key,
                MemorySuppression.fingerprint == fingerprint,
            )
            .limit(1)
        )
        return found is not None


class RunMemorySnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, snapshot: RunMemorySnapshot) -> RunMemorySnapshot:
        self._validate(snapshot)
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def get_by_run(self, run_id: uuid.UUID) -> RunMemorySnapshot | None:
        return (
            await self._session.execute(
                select(RunMemorySnapshot).where(RunMemorySnapshot.run_id == run_id)
            )
        ).scalar_one_or_none()

    async def get_by_run_for_update(
        self,
        run_id: uuid.UUID,
    ) -> RunMemorySnapshot | None:
        return (
            await self._session.execute(
                select(RunMemorySnapshot)
                .where(RunMemorySnapshot.run_id == run_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    def _validate(snapshot: RunMemorySnapshot) -> None:
        if snapshot.mode not in MEMORY_SNAPSHOT_MODES:
            raise ValueError(f"unsupported memory snapshot mode: {snapshot.mode}")
        if snapshot.outcome not in MEMORY_SNAPSHOT_OUTCOMES:
            raise ValueError(
                f"unsupported memory snapshot outcome: {snapshot.outcome}"
            )
        _validate_sha256(snapshot.query_sha256, label="memory snapshot query_sha256")
        if snapshot.policy_version < 1:
            raise ValueError("memory snapshot policy_version must be positive")
        if not snapshot.worker_id or len(snapshot.worker_id) > 255:
            raise ValueError(
                "memory snapshot worker_id must contain 1..255 characters"
            )
        if snapshot.attempt < 0:
            raise ValueError("memory snapshot attempt must be non-negative")
        if snapshot.content_bytes is None:
            snapshot.content_bytes = 0
        if snapshot.content_bytes < 0:
            raise ValueError("memory snapshot content_bytes must be non-negative")
        if snapshot.degraded_code is not None and (
            not snapshot.degraded_code or len(snapshot.degraded_code) > 128
        ):
            raise ValueError(
                "memory snapshot degraded_code must contain 1..128 characters"
            )


class RunMemoryInputRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, memory_input: RunMemoryInput) -> RunMemoryInput:
        self._validate(memory_input)
        self._session.add(memory_input)
        await self._session.flush()
        return memory_input

    async def add_many(
        self,
        memory_inputs: Sequence[RunMemoryInput],
    ) -> Sequence[RunMemoryInput]:
        for memory_input in memory_inputs:
            self._validate(memory_input)
        self._session.add_all(memory_inputs)
        await self._session.flush()
        return memory_inputs

    async def list_for_snapshot(
        self,
        snapshot_id: uuid.UUID,
    ) -> Sequence[RunMemoryInput]:
        result = await self._session.scalars(
            select(RunMemoryInput)
            .where(RunMemoryInput.snapshot_id == snapshot_id)
            .order_by(RunMemoryInput.ordinal, RunMemoryInput.id)
        )
        return result.all()

    @staticmethod
    def _validate(memory_input: RunMemoryInput) -> None:
        if memory_input.version_number < 1:
            raise ValueError("run memory input version_number must be positive")
        _validate_sha256(
            memory_input.content_sha256,
            label="run memory input content_sha256",
        )
        if memory_input.kind not in MEMORY_KINDS:
            raise ValueError(f"unsupported run memory input kind: {memory_input.kind}")
        if memory_input.source_kind not in MEMORY_SOURCE_KINDS:
            raise ValueError(
                "unsupported run memory input source_kind: "
                f"{memory_input.source_kind}"
            )
        if memory_input.selection_reason not in MEMORY_SELECTION_REASONS:
            raise ValueError(
                "unsupported run memory input selection_reason: "
                f"{memory_input.selection_reason}"
            )
        if memory_input.ordinal < 0:
            raise ValueError("run memory input ordinal must be non-negative")


class RunMemorySearchRepository:
    def __init__(self, session: AsyncSession, *, max_payload_bytes: int) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def add(self, search: RunMemorySearch) -> RunMemorySearch:
        self._validate(search)
        self._session.add(search)
        await self._session.flush()
        return search

    async def get_for_update(
        self,
        *,
        run_id: uuid.UUID,
        tool_call_id: str,
    ) -> RunMemorySearch | None:
        return (
            await self._session.execute(
                select(RunMemorySearch)
                .where(
                    RunMemorySearch.run_id == run_id,
                    RunMemorySearch.tool_call_id == tool_call_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    def _validate(self, search: RunMemorySearch) -> None:
        from omnicell_agent.memory.types import (  # local: avoid layer cycle
            MemoryResourceIdentity,
            MemorySelectionReason,
        )

        if not search.tool_call_id or len(search.tool_call_id) > 255:
            raise ValueError(
                "memory search tool_call_id must contain 1..255 characters"
            )
        _validate_sha256(
            search.request_sha256,
            label="memory search request_sha256",
        )
        if not search.worker_id or len(search.worker_id) > 255:
            raise ValueError(
                "memory search worker_id must contain 1..255 characters"
            )
        if search.attempt < 0:
            raise ValueError("memory search attempt must be non-negative")
        if search.result_identities is None:
            search.result_identities = []
        if search.result_count != len(search.result_identities):
            raise ValueError("memory search result_count does not match identities")
        if not 0 <= search.result_count <= 32:
            raise ValueError("memory search result_count must be between 0 and 32")
        identity_keys = {
            "item_id",
            "version_id",
            "version_number",
            "content_sha256",
            "kind",
            "source_kind",
            "selection_reason",
        }
        canonical: list[dict[str, Any]] = []
        seen_items: set[uuid.UUID] = set()
        seen_versions: set[uuid.UUID] = set()
        for raw_identity in search.result_identities:
            if (
                not isinstance(raw_identity, dict)
                or set(raw_identity) != identity_keys
            ):
                raise ValueError(
                    "memory search result must contain identity-only fields"
                )
            try:
                identity = MemoryResourceIdentity.from_mapping(raw_identity)
            except ValueError as exc:
                raise ValueError("invalid memory search result identity") from exc
            if (
                identity.selection_reason
                is not MemorySelectionReason.TOOL_SEARCH
            ):
                raise ValueError(
                    "memory search result must use tool_search selection"
                )
            if (
                identity.item_id in seen_items
                or identity.version_id in seen_versions
            ):
                raise ValueError("memory search result identity is duplicated")
            seen_items.add(identity.item_id)
            seen_versions.add(identity.version_id)
            canonical.append(identity.to_checkpoint_dict())
        search.result_identities = canonical
        ensure_payload_safe(
            search.result_identities,
            max_bytes=self._max_payload_bytes,
            label="memory search result identities",
        )


class RunMemoryForgetIntentRepository:
    def __init__(self, session: AsyncSession, *, max_payload_bytes: int) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def add(
        self,
        intent: RunMemoryForgetIntent,
    ) -> RunMemoryForgetIntent:
        self._validate(intent)
        self._session.add(intent)
        await self._session.flush()
        return intent

    async def get_for_update(
        self,
        *,
        run_id: uuid.UUID,
        tool_call_id: str,
    ) -> RunMemoryForgetIntent | None:
        return (
            await self._session.execute(
                select(RunMemoryForgetIntent)
                .where(
                    RunMemoryForgetIntent.run_id == run_id,
                    RunMemoryForgetIntent.tool_call_id == tool_call_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    def _validate(self, intent: RunMemoryForgetIntent) -> None:
        from omnicell_agent.memory.types import (  # local: avoid layer cycle
            MemoryResourceIdentity,
            MemorySelectionReason,
        )

        if not intent.tool_call_id or len(intent.tool_call_id) > 255:
            raise ValueError(
                "memory forget tool_call_id must contain 1..255 characters"
            )
        _validate_sha256(
            intent.request_sha256,
            label="memory forget request_sha256",
        )
        if not intent.worker_id or len(intent.worker_id) > 255:
            raise ValueError(
                "memory forget worker_id must contain 1..255 characters"
            )
        if intent.attempt < 0:
            raise ValueError("memory forget attempt must be non-negative")
        identity_keys = {
            "item_id",
            "version_id",
            "version_number",
            "content_sha256",
            "kind",
            "source_kind",
            "selection_reason",
        }
        if (
            not isinstance(intent.memory_identity, dict)
            or set(intent.memory_identity) != identity_keys
        ):
            raise ValueError(
                "memory forget result must contain identity-only fields"
            )
        try:
            identity = MemoryResourceIdentity.from_mapping(
                intent.memory_identity
            )
        except ValueError as exc:
            raise ValueError("invalid memory forget result identity") from exc
        if identity.selection_reason is not MemorySelectionReason.SELECTED:
            raise ValueError("memory forget result must use selected identity")
        intent.memory_identity = identity.to_checkpoint_dict()
        ensure_payload_safe(
            intent.memory_identity,
            max_bytes=self._max_payload_bytes,
            label="memory forget result identity",
        )


class RunMemoryProposalRepository:
    def __init__(self, session: AsyncSession, *, max_payload_bytes: int) -> None:
        self._session = session
        self._max_payload_bytes = max_payload_bytes

    async def add(self, proposal: RunMemoryProposal) -> RunMemoryProposal:
        self._validate(proposal)
        self._session.add(proposal)
        await self._session.flush()
        return proposal

    async def get_for_update(
        self,
        *,
        run_id: uuid.UUID,
        tool_call_id: str,
    ) -> RunMemoryProposal | None:
        return (
            await self._session.execute(
                select(RunMemoryProposal)
                .where(
                    RunMemoryProposal.run_id == run_id,
                    RunMemoryProposal.tool_call_id == tool_call_id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_any_for_run(
        self,
        *,
        run_id: uuid.UUID,
    ) -> RunMemoryProposal | None:
        return (
            await self._session.execute(
                select(RunMemoryProposal)
                .where(RunMemoryProposal.run_id == run_id)
                .order_by(
                    RunMemoryProposal.created_at.asc(),
                    RunMemoryProposal.id.asc(),
                )
                .limit(1)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()

    def _validate(self, proposal: RunMemoryProposal) -> None:
        from omnicell_agent.memory.types import (  # local: avoid layer cycle
            MemoryResourceIdentity,
            MemorySelectionReason,
        )

        if not proposal.tool_call_id or len(proposal.tool_call_id) > 255:
            raise ValueError(
                "memory proposal tool_call_id must contain 1..255 characters"
            )
        _validate_sha256(
            proposal.request_sha256,
            label="memory proposal request_sha256",
        )
        if not proposal.worker_id or len(proposal.worker_id) > 255:
            raise ValueError(
                "memory proposal worker_id must contain 1..255 characters"
            )
        if proposal.attempt < 0:
            raise ValueError("memory proposal attempt must be non-negative")
        identity_keys = {
            "item_id",
            "version_id",
            "version_number",
            "content_sha256",
            "kind",
            "source_kind",
            "selection_reason",
        }
        if (
            not isinstance(proposal.memory_identity, dict)
            or set(proposal.memory_identity) != identity_keys
        ):
            raise ValueError(
                "memory proposal result must contain identity-only fields"
            )
        try:
            identity = MemoryResourceIdentity.from_mapping(
                proposal.memory_identity
            )
        except ValueError as exc:
            raise ValueError("invalid memory proposal result identity") from exc
        if identity.selection_reason is not MemorySelectionReason.SELECTED:
            raise ValueError("memory proposal result must use selected identity")
        proposal.memory_identity = identity.to_checkpoint_dict()
        ensure_payload_safe(
            proposal.memory_identity,
            max_bytes=self._max_payload_bytes,
            label="memory proposal result identity",
        )


def _validate_sha256(value: str, *, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be a lowercase 64-character sha256 digest")


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
        self.memory_settings = MemorySettingsRepository(session)
        self.memory_items = MemoryItemRepository(
            session, max_metadata_bytes=metadata_max_bytes
        )
        self.memory_versions = MemoryVersionRepository(
            session, max_metadata_bytes=metadata_max_bytes
        )
        self.memory_suppressions = MemorySuppressionRepository(session)
        self.memory_snapshots = RunMemorySnapshotRepository(session)
        self.memory_inputs = RunMemoryInputRepository(session)
        self.memory_searches = RunMemorySearchRepository(
            session,
            max_payload_bytes=event_payload_max_bytes,
        )
        self.memory_forget_intents = RunMemoryForgetIntentRepository(
            session,
            max_payload_bytes=event_payload_max_bytes,
        )
        self.memory_proposals = RunMemoryProposalRepository(
            session,
            max_payload_bytes=event_payload_max_bytes,
        )
        self.checkpoint_anchors = CheckpointAnchorRepository(session)
