from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from omnicell_agent.persistence.models import LOCAL_DEFAULT_MEMORY_SCOPE


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)


class FakeMemorySettingsRepository:
    def __init__(self) -> None:
        self.row: SimpleNamespace | None = None

    async def get(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> SimpleNamespace | None:
        if self.row is None or self.row.scope_key != scope_key:
            return None
        return self.row

    async def get_or_create_for_update(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> SimpleNamespace:
        if self.row is None:
            self.row = SimpleNamespace(
                scope_key=scope_key,
                use_enabled=False,
                generation_enabled=False,
                tools_enabled=False,
                provider_consent_version=None,
                provider_consent_at=None,
                version=1,
                disclosure_epoch=1,
                created_at=NOW,
                updated_at=NOW,
            )
        return self.row

    async def get_for_share(
        self,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
    ) -> SimpleNamespace | None:
        return await self.get(scope_key)


class FakeMemoryItemRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, Any] = {}

    async def add(self, item: Any) -> Any:
        if getattr(item, "id", None) is None:
            item.id = uuid4()
        if getattr(item, "created_at", None) is None:
            item.created_at = NOW
        if getattr(item, "updated_at", None) is None:
            item.updated_at = NOW
        if getattr(item, "expires_at", None) is None:
            item.expires_at = None
        self.rows[item.id] = item
        return item

    async def update(self, item: Any) -> Any:
        self.rows[item.id] = item
        return item

    async def get(
        self,
        item_id: UUID,
        *,
        scope_key: str | None = None,
    ) -> Any | None:
        item = self.rows.get(item_id)
        if item is None or (
            scope_key is not None and item.scope_key != scope_key
        ):
            return None
        return item

    async def get_for_update(
        self,
        item_id: UUID,
        *,
        scope_key: str | None = None,
    ) -> Any | None:
        return await self.get(item_id, scope_key=scope_key)

    async def get_by_stable_key_for_update(
        self,
        *,
        scope_key: str,
        stable_key: str,
    ) -> Any | None:
        return next(
            (
                item
                for item in self.rows.values()
                if item.scope_key == scope_key
                and item.stable_key == stable_key
            ),
            None,
        )

    async def get_by_origin_for_update(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
    ) -> Any | None:
        return next(
            (
                item
                for item in self.rows.values()
                if getattr(item, "origin_run_id", None) == run_id
                and getattr(item, "origin_tool_call_id", None) == tool_call_id
            ),
            None,
        )

    async def list(
        self,
        *,
        scope_key: str = LOCAL_DEFAULT_MEMORY_SCOPE,
        kind: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Any]:
        rows = [
            item
            for item in self.rows.values()
            if item.scope_key == scope_key
            and (kind is None or item.kind == kind)
            and (status is None or item.status == status)
        ]
        rows.sort(
            key=lambda item: (item.updated_at, str(item.id)),
            reverse=True,
        )
        return rows[offset : offset + limit]


class FakeMemoryVersionRepository:
    def __init__(self, items: FakeMemoryItemRepository) -> None:
        self.rows: dict[UUID, Any] = {}
        self._items = items

    async def add(self, version: Any) -> Any:
        if getattr(version, "id", None) is None:
            version.id = uuid4()
        if getattr(version, "created_at", None) is None:
            version.created_at = NOW
        self.rows[version.id] = version
        return version

    async def get_by_id(self, version_id: UUID) -> Any | None:
        return self.rows.get(version_id)

    async def get_exact(
        self,
        *,
        item_id: UUID,
        version_number: int,
    ) -> Any | None:
        return next(
            (
                version
                for version in self.rows.values()
                if version.item_id == item_id
                and version.version_number == version_number
            ),
            None,
        )

    async def list_for_item(
        self,
        item_id: UUID,
        *,
        limit: int = 100,
    ) -> list[Any]:
        rows = [
            version
            for version in self.rows.values()
            if version.item_id == item_id
        ]
        rows.sort(key=lambda version: version.version_number, reverse=True)
        return rows[:limit]

    async def list_fingerprints_for_item(
        self,
        item_id: UUID,
    ) -> list[str]:
        return sorted(
            {
                version.fingerprint
                for version in self.rows.values()
                if version.item_id == item_id
            }
        )

    async def current_exists_by_fingerprint(
        self,
        *,
        scope_key: str,
        fingerprint: str,
        exclude_item_id: UUID | None = None,
    ) -> bool:
        for version in self.rows.values():
            item = self._items.rows.get(version.item_id)
            if (
                item is not None
                and item.scope_key == scope_key
                and item.status in {"active", "proposed"}
                and item.current_version == version.version_number
                and version.fingerprint == fingerprint
                and item.id != exclude_item_id
            ):
                return True
        return False

    async def delete_for_item(self, item_id: UUID) -> int:
        selected = [
            version_id
            for version_id, version in self.rows.items()
            if version.item_id == item_id
        ]
        for version_id in selected:
            del self.rows[version_id]
        return len(selected)


class FakeMemorySuppressionRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    async def exists(self, *, scope_key: str, fingerprint: str) -> bool:
        row = self.rows.get(fingerprint)
        return row is not None and row.scope_key == scope_key

    async def add(self, suppression: Any) -> Any:
        if getattr(suppression, "id", None) is None:
            suppression.id = uuid4()
        self.rows[suppression.fingerprint] = suppression
        return suppression


class FakeRunRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, Any] = {}

    async def get(self, run_id: UUID) -> Any | None:
        return self.rows.get(run_id)

    async def get_for_update(self, run_id: UUID) -> Any | None:
        return self.rows.get(run_id)


class FakeRunEventRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, list[Any]] = {}

    async def replay(
        self,
        run_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[Any]:
        return self.rows.get(run_id, [])[after_sequence : after_sequence + limit]


class FakeRunMemorySearchRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], Any] = {}

    async def add(self, search: Any) -> Any:
        if getattr(search, "id", None) is None:
            search.id = uuid4()
        if getattr(search, "created_at", None) is None:
            search.created_at = NOW
        self.rows[(search.run_id, search.tool_call_id)] = search
        return search

    async def get_for_update(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
    ) -> Any | None:
        return self.rows.get((run_id, tool_call_id))


class FakeRunMemoryForgetIntentRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], Any] = {}

    async def add(self, intent: Any) -> Any:
        if getattr(intent, "id", None) is None:
            intent.id = uuid4()
        if getattr(intent, "created_at", None) is None:
            intent.created_at = NOW
        self.rows[(intent.run_id, intent.tool_call_id)] = intent
        return intent

    async def get_for_update(
        self,
        *,
        run_id: UUID,
        tool_call_id: str,
    ) -> Any | None:
        return self.rows.get((run_id, tool_call_id))

    async def get_any_for_run(self, *, run_id: UUID) -> Any | None:
        return next(
            (
                value
                for (stored_run_id, _), value in self.rows.items()
                if stored_run_id == run_id
            ),
            None,
        )


class FakeRunMemoryProposalRepository(FakeRunMemoryForgetIntentRepository):
    pass


class FakeRunMemorySnapshotRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, Any] = {}

    async def add(self, snapshot: Any) -> Any:
        if getattr(snapshot, "id", None) is None:
            snapshot.id = uuid4()
        if getattr(snapshot, "created_at", None) is None:
            snapshot.created_at = NOW
        if getattr(snapshot, "updated_at", None) is None:
            snapshot.updated_at = NOW
        self.rows[snapshot.run_id] = snapshot
        return snapshot

    async def get_by_run(self, run_id: UUID) -> Any | None:
        return self.rows.get(run_id)

    async def get_by_run_for_update(self, run_id: UUID) -> Any | None:
        return self.rows.get(run_id)


class FakeRunMemoryInputRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, list[Any]] = {}

    async def add_many(self, memory_inputs: list[Any]) -> list[Any]:
        for memory_input in memory_inputs:
            if getattr(memory_input, "id", None) is None:
                memory_input.id = uuid4()
            self.rows.setdefault(memory_input.snapshot_id, []).append(
                memory_input
            )
        return memory_inputs

    async def list_for_snapshot(self, snapshot_id: UUID) -> list[Any]:
        return list(self.rows.get(snapshot_id, ()))


class FakeRepositories:
    def __init__(self) -> None:
        self.memory_settings = FakeMemorySettingsRepository()
        self.memory_items = FakeMemoryItemRepository()
        self.memory_versions = FakeMemoryVersionRepository(self.memory_items)
        self.memory_suppressions = FakeMemorySuppressionRepository()
        self.runs = FakeRunRepository()
        self.events = FakeRunEventRepository()
        self.memory_snapshots = FakeRunMemorySnapshotRepository()
        self.memory_inputs = FakeRunMemoryInputRepository()
        self.memory_searches = FakeRunMemorySearchRepository()
        self.memory_forget_intents = FakeRunMemoryForgetIntentRepository()
        self.memory_proposals = FakeRunMemoryProposalRepository()


class FakeUnitOfWork(AbstractAsyncContextManager):
    def __init__(self, repositories: FakeRepositories) -> None:
        self.repositories = repositories

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        return None


def unit_of_work_factory(
    repositories: FakeRepositories,
):
    return lambda: FakeUnitOfWork(repositories)


__all__ = [
    "FakeRepositories",
    "NOW",
    "unit_of_work_factory",
]
