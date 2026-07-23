"""Transaction boundary for application repositories."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from .database import ApplicationDatabase, await_cancellation_safe
from .repositories import (
    DEFAULT_EVENT_PAYLOAD_MAX_BYTES,
    DEFAULT_METADATA_MAX_BYTES,
    Repositories,
)


class UnitOfWork:
    def __init__(
        self,
        database: ApplicationDatabase,
        *,
        event_payload_max_bytes: int | None = None,
        metadata_max_bytes: int | None = None,
    ) -> None:
        self._database = database
        self._event_payload_max_bytes = (
            event_payload_max_bytes
            if event_payload_max_bytes is not None
            else getattr(
                database.settings,
                "event_payload_max_bytes",
                DEFAULT_EVENT_PAYLOAD_MAX_BYTES,
            )
        )
        self._metadata_max_bytes = (
            metadata_max_bytes
            if metadata_max_bytes is not None
            else getattr(
                database.settings,
                "artifact_metadata_max_bytes",
                DEFAULT_METADATA_MAX_BYTES,
            )
        )
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None
        self._completed = False
        self.repositories: Repositories | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("Unit of Work has not been entered")
        return self._session

    async def __aenter__(self) -> UnitOfWork:
        if self._session is not None:
            raise RuntimeError("Unit of Work is already active")
        session_factory: async_sessionmaker[AsyncSession] = self._database.session_factory
        self._session = session_factory()
        self._completed = False
        try:
            self._transaction = await self._session.begin()
        except BaseException:
            await await_cancellation_safe(self._session.close())
            self._session = None
            raise
        self.repositories = Repositories(
            self._session,
            event_payload_max_bytes=self._event_payload_max_bytes,
            metadata_max_bytes=self._metadata_max_bytes,
        )
        return self

    async def commit(self) -> None:
        if self._transaction is None:
            raise RuntimeError("Unit of Work has not been entered")
        if not self._completed:
            try:
                await await_cancellation_safe(self._transaction.commit())
            except BaseException:
                # await_cancellation_safe only re-raises external cancellation
                # after the inner commit has reached a terminal state.  A
                # regular database error keeps the transaction available for
                # rollback in __aexit__.
                if not self._transaction.is_active:
                    self._completed = True
                raise
            else:
                self._completed = True

    async def rollback(self) -> None:
        if self._transaction is None:
            raise RuntimeError("Unit of Work has not been entered")
        if not self._completed:
            try:
                await await_cancellation_safe(self._transaction.rollback())
            finally:
                if not self._transaction.is_active:
                    self._completed = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        session = self._session
        try:
            if not self._completed:
                if exc_type is None:
                    try:
                        await self.commit()
                    except BaseException:
                        if (
                            not self._completed
                            and self._transaction is not None
                            and self._transaction.is_active
                        ):
                            await self.rollback()
                        raise
                else:
                    await self.rollback()
        finally:
            if session is not None:
                await await_cancellation_safe(session.close())
            self._session = None
            self._transaction = None
            self.repositories = None
        return False


ApplicationUnitOfWork = UnitOfWork
