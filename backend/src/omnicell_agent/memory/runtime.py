"""PostgreSQL-backed Run memory preparation and per-turn body resolution."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from omnicell_agent.agent.hooks import (
    DispatchAuthorizationInvalidatedError,
    MemoryTurnResolution,
    ResolvedMemory,
    encode_memory_context,
)
from omnicell_agent.persistence.models import (
    LOCAL_DEFAULT_MEMORY_SCOPE,
    RunMemoryInput,
    RunMemorySnapshot,
)
from omnicell_agent.runs.event_log import UnitOfWorkFactory
from omnicell_agent.runs.memory import (
    PreparedMemoryContext,
    PreparedMemoryInput,
    RunMemoryPreparationError,
)

from .agent_adapter import RunBoundMemoryControlAdapter
from .errors import (
    MemoryAttemptFenceError,
    MemoryContextLimitError,
    MemoryDisabledError,
    MemoryError,
    MemoryProviderConsentRequiredError,
    MemorySelectionInvalidError,
    MemorySnapshotConflictError,
)
from .ranking import rank_memory_candidates
from .service import MemoryService
from .types import (
    MEMORY_PROVIDER_CONSENT_VERSION,
    MemoryKind,
    MemoryCandidate,
    MemoryResourceIdentity,
    MemoryRunMode,
    MemorySelectionReason,
    MemorySelectionRef,
    MemorySourceKind,
    MemoryStatus,
)
from .validation import validate_memory_content


def _now() -> datetime:
    return datetime.now(UTC)


class PostgresMemoryRuntime:
    """Composition adapter implementing ``RunMemoryRuntime``."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        service: MemoryService | None = None,
        clock: Any | None = None,
        default_limit: int = 8,
        max_context_bytes: int = 48 * 1024,
    ) -> None:
        if not 1 <= default_limit <= 32:
            raise ValueError("default memory limit must be between 1 and 32")
        if not 1 <= max_context_bytes <= 256 * 1024:
            raise ValueError("memory context bytes must be between 1 and 262144")
        self._unit_of_work = unit_of_work
        self._clock = clock or _now
        self._service = service or MemoryService(unit_of_work, clock=self._clock)
        self._default_limit = default_limit
        self._max_context_bytes = max_context_bytes

    async def prepare_snapshot(
        self,
        *,
        repositories: Any,
        run: Any,
        goal: str,
        worker_id: str,
        expected_attempt: int,
    ) -> PreparedMemoryContext | None:
        try:
            return await self._prepare_snapshot(
                repositories=repositories,
                run=run,
                goal=goal,
                worker_id=worker_id,
                expected_attempt=expected_attempt,
            )
        except MemoryError as exc:
            raise RunMemoryPreparationError(
                error_code=exc.error_code,
                summary=exc.summary,
            ) from exc

    async def _prepare_snapshot(
        self,
        *,
        repositories: Any,
        run: Any,
        goal: str,
        worker_id: str,
        expected_attempt: int,
    ) -> PreparedMemoryContext | None:
        mode = MemoryRunMode(str(run.request_payload.get("memory_mode", "off")))
        if mode is MemoryRunMode.OFF:
            return None
        if run.worker_id != worker_id or run.attempt != expected_attempt:
            raise MemoryAttemptFenceError()
        now = self._clock()
        if run.lease_expires_at is None or run.lease_expires_at <= now:
            raise MemoryAttemptFenceError()

        query_sha256 = hashlib.sha256(goal.strip().encode("utf-8")).hexdigest()
        existing = await repositories.memory_snapshots.get_by_run_for_update(
            run.id
        )
        if existing is not None:
            if existing.mode != mode.value or existing.query_sha256 != query_sha256:
                raise MemorySnapshotConflictError()
            rows = await repositories.memory_inputs.list_for_snapshot(existing.id)
            if mode is MemoryRunMode.SELECTED:
                requested = {
                    (
                        str(value["item_id"]),
                        str(value["version_id"]),
                    )
                    for value in run.request_payload.get("selected_memories", [])
                }
                frozen = {
                    (str(value.item_id), str(value.version_id))
                    for value in rows
                }
                if requested != frozen:
                    raise MemorySnapshotConflictError()
            return self._prepared_context(existing, rows)

        settings = await repositories.memory_settings.get_for_share()
        if settings is None:
            settings = (
                await repositories.memory_settings.get_or_create_for_update()
            )

        selected: list[tuple[MemoryResourceIdentity, str]] = []
        context_view: list[ResolvedMemory] = []
        outcome = "empty"
        degraded_code: str | None = None
        if mode is MemoryRunMode.DEFAULT:
            if (
                not settings.use_enabled
                or settings.provider_consent_version
                != MEMORY_PROVIDER_CONSENT_VERSION
                or not settings.provider_consent_at
            ):
                outcome = "degraded"
                degraded_code = "memory_retrieval_unavailable"
            else:
                try:
                    candidates = await self._service._active_candidates(  # noqa: SLF001
                        repositories,
                        kinds={
                            MemoryKind.RESPONSE_PREFERENCE,
                            MemoryKind.PROFILE_FACT,
                            MemoryKind.PROJECT_CONTEXT,
                        },
                        selection_reason=MemorySelectionReason.DEFAULT,
                    )
                    ranked = rank_memory_candidates(
                        goal,
                        candidates,
                        limit=self._default_limit,
                    )
                    skipped_for_limit = False
                    for candidate in ranked:
                        resolved = self._resolved_candidate(candidate)
                        proposed_view = [*context_view, resolved]
                        if self._encoded_size(proposed_view) > self._max_context_bytes:
                            skipped_for_limit = True
                            continue
                        context_view = proposed_view
                        selected.append(
                            (candidate.identity, candidate.content)
                        )
                    if selected:
                        outcome = "loaded"
                    elif skipped_for_limit:
                        outcome = "degraded"
                        degraded_code = "memory_context_limit_exceeded"
                except MemoryError:
                    selected = []
                    context_view = []
                    outcome = "degraded"
                    degraded_code = "memory_retrieval_unavailable"
        else:
            if not settings.use_enabled:
                raise MemoryDisabledError()
            if not (
                settings.provider_consent_version
                == MEMORY_PROVIDER_CONSENT_VERSION
                and settings.provider_consent_at
            ):
                raise MemoryProviderConsentRequiredError()
            raw_refs = run.request_payload.get("selected_memories", [])
            try:
                refs = tuple(
                    MemorySelectionRef.from_mapping(value)
                    for value in raw_refs
                )
            except ValueError as exc:
                raise MemorySelectionInvalidError() from exc
            if not refs or len(refs) > 32:
                raise MemorySelectionInvalidError()
            for ref in refs:
                item = await repositories.memory_items.get(
                    ref.item_id,
                    scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                )
                version = await repositories.memory_versions.get_by_id(
                    ref.version_id
                )
                if (
                    item is None
                    or item.status != MemoryStatus.ACTIVE.value
                    or item.current_version is None
                    or (
                        item.expires_at is not None
                        and item.expires_at <= now
                    )
                    or version is None
                    or version.item_id != item.id
                    or version.version_number != item.current_version
                ):
                    raise MemorySelectionInvalidError()
                validated = validate_memory_content(
                    version.content,
                    kind=MemoryKind(item.kind),
                    dataset_scope=version.dataset_scope,
                    provenance=version.source_refs,
                    preserve_original=True,
                )
                if (
                    validated.sha256 != version.sha256
                    or validated.fingerprint != version.fingerprint
                    or await repositories.memory_suppressions.exists(
                        scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                        fingerprint=version.fingerprint,
                    )
                ):
                    raise MemorySelectionInvalidError()
                identity = self._service._identity(  # noqa: SLF001
                    item,
                    version,
                    MemorySelectionReason.SELECTED,
                )
                context_view.append(
                    ResolvedMemory(
                        item_id=str(identity.item_id),
                        version_id=str(identity.version_id),
                        version_number=identity.version_number,
                        content_sha256=identity.content_sha256,
                        kind=identity.kind.value,
                        source_kind=identity.source_kind.value,
                        selection_reason=identity.selection_reason.value,
                        dataset_scope=validated.dataset_scope,
                        provenance=validated.provenance,
                        content=validated.content,
                    )
                )
                selected.append(
                    (
                        identity,
                        validated.content,
                    )
                )
            if self._encoded_size(context_view) > self._max_context_bytes:
                raise MemoryContextLimitError()
            outcome = "loaded"

        snapshot = await repositories.memory_snapshots.add(
            RunMemorySnapshot(
                run_id=run.id,
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                mode=mode.value,
                outcome=outcome,
                query_sha256=query_sha256,
                policy_version=settings.version,
                worker_id=worker_id,
                attempt=expected_attempt,
                content_bytes=self._encoded_size(context_view),
                degraded_code=degraded_code,
            )
        )
        inputs = [
            RunMemoryInput(
                snapshot_id=snapshot.id,
                item_id=identity.item_id,
                version_id=identity.version_id,
                version_number=identity.version_number,
                content_sha256=identity.content_sha256,
                kind=identity.kind.value,
                source_kind=identity.source_kind.value,
                selection_reason=identity.selection_reason.value,
                ordinal=index,
            )
            for index, (identity, _) in enumerate(selected)
        ]
        if inputs:
            await repositories.memory_inputs.add_many(inputs)
        return self._prepared_context(snapshot, inputs)

    @staticmethod
    def _resolved_candidate(candidate: MemoryCandidate) -> ResolvedMemory:
        identity = candidate.identity
        return ResolvedMemory(
            item_id=str(identity.item_id),
            version_id=str(identity.version_id),
            version_number=identity.version_number,
            content_sha256=identity.content_sha256,
            kind=identity.kind.value,
            source_kind=identity.source_kind.value,
            selection_reason=identity.selection_reason.value,
            dataset_scope=dict(candidate.dataset_scope),
            provenance=tuple(dict(value) for value in candidate.provenance),
            content=candidate.content,
        )

    @staticmethod
    def _encoded_size(memories: list[ResolvedMemory]) -> int:
        if not memories:
            return 0
        return len(encode_memory_context(memories).encode("utf-8"))

    def resolver(self, run_id: UUID) -> "PostgresMemoryContextResolver":
        return PostgresMemoryContextResolver(
            self._unit_of_work,
            run_id=run_id,
            clock=self._clock,
            max_context_bytes=self._max_context_bytes,
        )

    async def control_port(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        expected_attempt: int,
    ) -> RunBoundMemoryControlAdapter | None:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get()
            if settings is None or not settings.tools_enabled:
                return None
        return RunBoundMemoryControlAdapter(
            self._service,
            run_id=run_id,
            worker_id=worker_id,
            expected_attempt=expected_attempt,
        )

    @staticmethod
    def _prepared_context(
        snapshot: Any,
        inputs: Any,
    ) -> PreparedMemoryContext:
        return PreparedMemoryContext(
            snapshot_id=snapshot.id,
            mode=snapshot.mode,
            outcome=snapshot.outcome,
            inputs=tuple(
                PreparedMemoryInput(
                    item_id=value.item_id,
                    version_id=value.version_id,
                    version_number=value.version_number,
                    content_sha256=value.content_sha256,
                    kind=value.kind,
                    source_kind=value.source_kind,
                    selection_reason=value.selection_reason,
                )
                for value in inputs
            ),
            content_bytes=snapshot.content_bytes,
            degraded_code=snapshot.degraded_code,
        )


class PostgresMemoryContextResolver:
    """Rebuild bodies on every turn and honor revoke/purge immediately."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        run_id: UUID,
        clock: Any | None = None,
        max_context_bytes: int = 48 * 1024,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._run_id = run_id
        self._clock = clock or _now
        self._max_context_bytes = max_context_bytes

    async def resolve(
        self,
        extra_resources: list[dict[str, Any]],
    ) -> MemoryTurnResolution:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get_for_share()
            snapshot = await repositories.memory_snapshots.get_by_run(
                self._run_id
            )
            source_ids = await self._source_message_ids(
                repositories,
                settings,
            )
            if (
                snapshot is None
                or settings is None
                or not settings.use_enabled
                or settings.provider_consent_version
                != MEMORY_PROVIDER_CONSENT_VERSION
                or not settings.provider_consent_at
            ):
                return MemoryTurnResolution(source_message_ids=source_ids)
            disclosure_epoch = settings.disclosure_epoch

            rows = await repositories.memory_inputs.list_for_snapshot(snapshot.id)
            base_identities = [
                MemoryResourceIdentity(
                    item_id=row.item_id,
                    version_id=row.version_id,
                    version_number=row.version_number,
                    content_sha256=row.content_sha256,
                    kind=MemoryKind(row.kind),
                    source_kind=MemorySourceKind(row.source_kind),
                    selection_reason=MemorySelectionReason(row.selection_reason),
                )
                for row in rows
            ]
            parsed_extras: list[MemoryResourceIdentity] = []
            for raw in extra_resources[:32]:
                try:
                    identity = MemoryResourceIdentity.from_mapping(raw)
                except ValueError:
                    continue
                if identity.selection_reason is MemorySelectionReason.TOOL_SEARCH:
                    parsed_extras.append(identity)

            seen: set[UUID] = set()
            resolved: list[ResolvedMemory] = []
            authorized_identities: list[MemoryResourceIdentity] = []
            valid_extras: list[dict[str, Any]] = []
            for identity in (*base_identities, *parsed_extras):
                if identity.version_id in seen:
                    continue
                memory = await self._resolve_one(repositories, identity)
                if memory is None:
                    continue
                proposed = [*resolved, memory]
                size = len(encode_memory_context(proposed).encode("utf-8"))
                if size > self._max_context_bytes:
                    continue
                seen.add(identity.version_id)
                resolved.append(memory)
                authorized_identities.append(identity)
                if identity in parsed_extras:
                    valid_extras.append(identity.to_checkpoint_dict())

        async def pre_dispatch() -> None:
            await self._preflight(
                expected_disclosure_epoch=disclosure_epoch,
                identities=tuple(authorized_identities),
            )

        callback = pre_dispatch if resolved else None
        return MemoryTurnResolution(
            memories=tuple(resolved),
            valid_extra_resources=tuple(valid_extras),
            source_message_ids=source_ids,
            pre_dispatch=callback,
        )

    async def _preflight(
        self,
        *,
        expected_disclosure_epoch: int,
        identities: tuple[MemoryResourceIdentity, ...],
    ) -> None:
        """Authorize one provider attempt at a short DB linearization point."""

        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get_for_share()
            if (
                settings is None
                or settings.disclosure_epoch != expected_disclosure_epoch
                or not settings.use_enabled
                or settings.provider_consent_version
                != MEMORY_PROVIDER_CONSENT_VERSION
                or not settings.provider_consent_at
            ):
                raise DispatchAuthorizationInvalidatedError()
            for identity in identities:
                if await self._resolve_one(repositories, identity) is None:
                    raise DispatchAuthorizationInvalidatedError()

    async def _resolve_one(
        self,
        repositories: Any,
        identity: MemoryResourceIdentity,
    ) -> ResolvedMemory | None:
        item = await repositories.memory_items.get(
            identity.item_id,
            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
        )
        now = self._clock()
        if (
            item is None
            or item.status != MemoryStatus.ACTIVE.value
            or (
                item.expires_at is not None
                and item.expires_at <= now
            )
            or item.kind != identity.kind.value
        ):
            return None
        version = await repositories.memory_versions.get_by_id(
            identity.version_id
        )
        if (
            version is None
            or version.item_id != identity.item_id
            or version.version_number != identity.version_number
            or version.sha256 != identity.content_sha256
            or version.source_kind != identity.source_kind.value
        ):
            return None
        if await repositories.memory_suppressions.exists(
            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            fingerprint=version.fingerprint,
        ):
            return None
        try:
            validated = validate_memory_content(
                version.content,
                kind=identity.kind,
                dataset_scope=version.dataset_scope,
                provenance=version.source_refs,
                preserve_original=True,
            )
        except MemoryError:
            return None
        if (
            validated.sha256 != identity.content_sha256
            or validated.fingerprint != version.fingerprint
        ):
            return None
        return ResolvedMemory(
            item_id=str(identity.item_id),
            version_id=str(identity.version_id),
            version_number=identity.version_number,
            content_sha256=identity.content_sha256,
            kind=identity.kind.value,
            source_kind=identity.source_kind.value,
            selection_reason=identity.selection_reason.value,
            dataset_scope=validated.dataset_scope,
            provenance=validated.provenance,
            content=validated.content,
        )

    async def _source_message_ids(
        self,
        repositories: Any,
        settings: Any,
    ) -> tuple[str, ...]:
        if (
            settings is None
            or not settings.tools_enabled
            or not settings.generation_enabled
        ):
            return ()
        rows = await repositories.events.replay(
            self._run_id,
            after_sequence=0,
            limit=5_000,
        )
        identities: list[str] = []
        for event in rows:
            if event.event_type != "message.completed":
                continue
            try:
                message_id = str(UUID(str(event.payload["message_id"])))
                role = str(event.payload["role"])
            except (KeyError, TypeError, ValueError):
                continue
            if role != "user":
                continue
            identities.append(message_id)
        return tuple(identities[-32:])


__all__ = [
    "PostgresMemoryContextResolver",
    "PostgresMemoryRuntime",
]
