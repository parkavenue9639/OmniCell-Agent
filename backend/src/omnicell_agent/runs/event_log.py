"""Database-authoritative event replay and replay-first live following."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from omnicell_agent.persistence.models import Run, RunEvent
from omnicell_agent.persistence.unit_of_work import UnitOfWork

from .events import PersistedEvent, validate_persisted_event
from .status import is_terminal_run_status


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[UnitOfWork]: ...


class EventRunNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayPage:
    events: tuple[PersistedEvent, ...]
    next_sequence: int
    has_more: bool
    terminal: bool


@dataclass(slots=True)
class _RunNotifierState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    revision: int = 0
    followers: int = 0
    waiters: int = 0
    operations: int = 0
    terminal: bool = False


def project_persisted_event(run: Run, event: RunEvent) -> PersistedEvent:
    return validate_persisted_event(
        {
            "event_id": event.id,
            "schema_version": event.schema_version,
            "conversation_id": run.conversation_id,
            "run_id": run.id,
            "sequence": event.sequence,
            "type": event.event_type,
            "occurred_at": event.created_at,
            "payload": event.payload,
        }
    )


class RunEventNotifier:
    """Best-effort local wakeups; PostgreSQL replay remains the source of truth."""

    def __init__(self) -> None:
        self._states: dict[UUID, _RunNotifierState] = {}

    @property
    def tracked_run_count(self) -> int:
        return len(self._states)

    def _state(self, run_id: UUID) -> _RunNotifierState:
        return self._states.setdefault(run_id, _RunNotifierState())

    def _discard_if_idle(self, run_id: UUID, state: _RunNotifierState) -> None:
        if state.followers or state.waiters or state.operations:
            return
        if self._states.get(run_id) is state:
            self._states.pop(run_id, None)

    def revision(self, run_id: UUID) -> int:
        state = self._states.get(run_id)
        return state.revision if state is not None else 0

    @asynccontextmanager
    async def follow_run(self, run_id: UUID) -> AsyncIterator[None]:
        """Keep one notifier generation alive for a replay/follow subscription."""

        state = self._state(run_id)
        state.followers += 1
        try:
            yield
        finally:
            state.followers -= 1
            self._discard_if_idle(run_id, state)

    async def notify(self, run_id: UUID) -> None:
        state = self._state(run_id)
        state.operations += 1
        try:
            async with state.condition:
                state.revision += 1
                state.condition.notify_all()
        finally:
            state.operations -= 1
            self._discard_if_idle(run_id, state)

    async def mark_terminal(self, run_id: UUID) -> None:
        """Close an observed terminal run and wake any remaining followers."""

        state = self._states.get(run_id)
        if state is None:
            return
        state.operations += 1
        try:
            async with state.condition:
                state.terminal = True
                state.revision += 1
                state.condition.notify_all()
        finally:
            state.operations -= 1
            self._discard_if_idle(run_id, state)

    async def wait_for_change(
        self,
        run_id: UUID,
        observed_revision: int,
        *,
        timeout_seconds: float,
    ) -> int:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        state = self._state(run_id)
        state.waiters += 1
        try:
            async with state.condition:
                if state.terminal or state.revision != observed_revision:
                    return state.revision
                try:
                    await asyncio.wait_for(
                        state.condition.wait_for(
                            lambda: state.terminal
                            or state.revision != observed_revision
                        ),
                        timeout=timeout_seconds,
                    )
                except TimeoutError:
                    pass
                return state.revision
        finally:
            state.waiters -= 1
            self._discard_if_idle(run_id, state)


class RunEventLog:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        notifier: RunEventNotifier | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0 or poll_interval_seconds > 30:
            raise ValueError("poll_interval_seconds 必须在 0..30 秒之间")
        self._unit_of_work = unit_of_work
        self.notifier = notifier or RunEventNotifier()
        self._poll_interval = poll_interval_seconds

    async def replay(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> ReplayPage:
        if after_sequence < 0:
            raise ValueError("after_sequence 必须非负")
        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1..500 之间")
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get(run_id)
            if run is None:
                raise EventRunNotFoundError(f"run {run_id} 不存在")
            rows = await repositories.events.replay(
                run_id,
                after_sequence=after_sequence,
                limit=limit + 1,
            )
            has_more = len(rows) > limit
            selected = rows[:limit]
            projected = tuple(project_persisted_event(run, row) for row in selected)
            next_sequence = (
                int(projected[-1].sequence) if projected else after_sequence
            )
            return ReplayPage(
                events=projected,
                next_sequence=next_sequence,
                has_more=has_more,
                terminal=is_terminal_run_status(run.status),
            )

    async def follow(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        page_size: int = 200,
    ) -> AsyncIterator[PersistedEvent]:
        cursor = after_sequence
        async with self.notifier.follow_run(run_id):
            while True:
                observed = self.notifier.revision(run_id)
                page = await self.replay(
                    run_id,
                    after_sequence=cursor,
                    limit=page_size,
                )
                for event in page.events:
                    sequence = int(event.sequence)
                    if sequence <= cursor:
                        continue
                    cursor = sequence
                    yield event
                if page.has_more:
                    continue
                if page.terminal:
                    await self.notifier.mark_terminal(run_id)
                    return
                await self.notifier.wait_for_change(
                    run_id,
                    observed,
                    timeout_seconds=self._poll_interval,
                )


__all__ = [
    "EventRunNotFoundError",
    "ReplayPage",
    "RunEventLog",
    "RunEventNotifier",
    "UnitOfWorkFactory",
    "project_persisted_event",
]
