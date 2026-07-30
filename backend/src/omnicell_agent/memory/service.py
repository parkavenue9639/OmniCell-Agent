"""Transactional application service for versioned cross-conversation memory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid4

from omnicell_agent.persistence.models import (
    LOCAL_DEFAULT_MEMORY_SCOPE,
    MemoryItem,
    MemorySettings,
    MemorySuppression,
    MemoryVersion,
    RunMemoryForgetIntent,
    RunMemoryProposal,
    RunMemorySearch,
)
from omnicell_agent.runs.event_log import UnitOfWorkFactory
from omnicell_agent.runs.status import RunStatus

from .errors import (
    MemoryAttemptFenceError,
    MemoryConflictError,
    MemoryDisabledError,
    MemoryNotFoundError,
    MemoryProviderConsentRequiredError,
    MemoryProposalLimitError,
    MemorySourceInvalidError,
    MemoryStateError,
    MemorySuppressedError,
)
from .ranking import rank_memory_candidates
from .types import (
    MEMORY_PROVIDER_CONSENT_VERSION,
    MemoryCandidate,
    MemoryKind,
    MemoryRecord,
    MemoryResourceIdentity,
    MemorySelectionReason,
    MemorySettingsState,
    MemorySourceKind,
    MemoryStatus,
)
from .validation import (
    validate_memory_content,
    validate_search_query,
    validate_stable_key,
)


_SOURCE_MESSAGE_SUPPRESSION_DOMAIN = b"omnicell-memory-source-message:v1\0"


def _source_message_fingerprint(message_id: UUID) -> str:
    """Build a body-free, domain-separated tombstone key."""

    return hashlib.sha256(
        _SOURCE_MESSAGE_SUPPRESSION_DOMAIN + message_id.bytes
    ).hexdigest()


def _source_message_ids(
    source_refs: Sequence[Mapping[str, Any]],
) -> set[UUID]:
    """Extract only typed message identities from validated provenance."""

    selected: set[UUID] = set()
    for source in source_refs:
        values = source.get("message_ids")
        if not isinstance(values, list):
            continue
        for value in values[:32]:
            try:
                selected.add(UUID(str(value)))
            except (TypeError, ValueError):
                continue
    return selected


def _aware_now() -> datetime:
    return datetime.now(UTC)


class MemoryService:
    """Own all writes so explicit and Agent-originated paths share one guard."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Any | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock or _aware_now

    async def get_settings(self) -> MemorySettingsState:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            row = await repositories.memory_settings.get()
            if row is None:
                row = await repositories.memory_settings.get_or_create_for_update()
            return self._settings_state(row)

    async def update_settings(
        self,
        *,
        use_enabled: bool | None = None,
        generation_enabled: bool | None = None,
        tools_enabled: bool | None = None,
        expected_version: int | None = None,
    ) -> MemorySettingsState:
        if all(
            value is None
            for value in (use_enabled, generation_enabled, tools_enabled)
        ):
            raise ValueError("at least one memory setting must be provided")
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            row = await repositories.memory_settings.get_or_create_for_update()
            self._check_settings_version(row, expected_version)
            target_use = row.use_enabled if use_enabled is None else use_enabled
            if target_use and not self._consent_granted(row):
                raise MemoryProviderConsentRequiredError()
            disclosure_changed = target_use != row.use_enabled
            if use_enabled is not None:
                row.use_enabled = use_enabled
            if generation_enabled is not None:
                row.generation_enabled = generation_enabled
            if tools_enabled is not None:
                row.tools_enabled = tools_enabled
            if disclosure_changed:
                row.disclosure_epoch += 1
            row.version += 1
            row.updated_at = self._clock()
            return self._settings_state(row)

    async def set_provider_consent(
        self,
        *,
        granted: bool,
        statement_version: str,
        confirmed: bool,
        expected_version: int | None = None,
    ) -> MemorySettingsState:
        if not confirmed:
            raise ValueError("provider consent requires explicit confirmation")
        if not statement_version or len(statement_version) > 64:
            raise ValueError("invalid provider consent statement version")
        if granted and statement_version != MEMORY_PROVIDER_CONSENT_VERSION:
            raise MemoryProviderConsentRequiredError(
                "Provider consent 声明版本已失效，请重新确认当前披露说明。"
            )
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            row = await repositories.memory_settings.get_or_create_for_update()
            self._check_settings_version(row, expected_version)
            prior_consent = (
                row.provider_consent_version,
                row.provider_consent_at,
                row.use_enabled,
            )
            if granted:
                row.provider_consent_version = statement_version
                row.provider_consent_at = self._clock()
            else:
                row.provider_consent_version = None
                row.provider_consent_at = None
                row.use_enabled = False
            if prior_consent != (
                row.provider_consent_version,
                row.provider_consent_at,
                row.use_enabled,
            ):
                row.disclosure_epoch += 1
            row.version += 1
            row.updated_at = self._clock()
            return self._settings_state(row)

    async def create_memory(
        self,
        *,
        kind: MemoryKind | str,
        content: str,
        stable_key: str | None = None,
        dataset_scope: Mapping[str, Any] | None = None,
        provenance: Sequence[Mapping[str, Any]] | None = None,
        expires_at: datetime | None = None,
        status: MemoryStatus | str = MemoryStatus.ACTIVE,
        source_kind: MemorySourceKind | str = MemorySourceKind.EXPLICIT,
    ) -> MemoryRecord:
        normalized_kind = MemoryKind(str(kind))
        normalized_status = MemoryStatus(str(status))
        normalized_source = MemorySourceKind(str(source_kind))
        if normalized_status not in {
            MemoryStatus.ACTIVE,
            MemoryStatus.PROPOSED,
        }:
            raise MemoryStateError("新建记忆只能是 active 或 proposed。")
        if (
            normalized_status is MemoryStatus.PROPOSED
            and normalized_source is not MemorySourceKind.PROPOSED
        ):
            raise MemoryStateError("proposed 记忆必须使用 proposed 来源。")
        validated = validate_memory_content(
            content,
            kind=normalized_kind,
            dataset_scope=dataset_scope,
            provenance=provenance,
        )
        key = validate_stable_key(
            stable_key,
            kind=normalized_kind,
            content_sha256=validated.sha256,
        )
        self._validate_expiry(expires_at)
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.memory_settings.get_or_create_for_update()
            await self._require_content_available(
                repositories,
                fingerprint=validated.fingerprint,
            )
            if await repositories.memory_items.get_by_stable_key_for_update(
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                stable_key=key,
            ) is not None:
                raise MemoryConflictError("stable key 已存在。")
            item = await repositories.memory_items.add(
                MemoryItem(
                    scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                    kind=normalized_kind.value,
                    stable_key=key,
                    status=normalized_status.value,
                    current_version=1,
                    dataset_scope=validated.dataset_scope,
                    expires_at=expires_at,
                )
            )
            version = await repositories.memory_versions.add(
                MemoryVersion(
                    item_id=item.id,
                    version_number=1,
                    content=validated.content,
                    sha256=validated.sha256,
                    fingerprint=validated.fingerprint,
                    source_kind=normalized_source.value,
                    dataset_scope=validated.dataset_scope,
                    source_refs=list(validated.provenance),
                )
            )
            return self._record(item, version)

    async def list_memories(
        self,
        *,
        kind: MemoryKind | str | None = None,
        status: MemoryStatus | str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]:
        normalized_kind = MemoryKind(str(kind)).value if kind is not None else None
        normalized_status = (
            MemoryStatus(str(status)).value if status is not None else None
        )
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            rows = await repositories.memory_items.list(
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                kind=normalized_kind,
                status=normalized_status,
                offset=offset,
                limit=limit,
            )
            records: list[MemoryRecord] = []
            for item in rows:
                records.append(
                    self._record(
                        item,
                        await self._current_version(repositories, item),
                    )
                )
            return tuple(records)

    async def get_memory(self, item_id: UUID) -> MemoryRecord:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            item = await repositories.memory_items.get(
                item_id,
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            )
            if item is None:
                raise MemoryNotFoundError()
            return self._record(
                item,
                await self._current_version(repositories, item),
            )

    async def approve_memory(
        self,
        item_id: UUID,
        *,
        expected_version: int,
    ) -> MemoryRecord:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.memory_settings.get_or_create_for_update()
            item = await self._locked_item(repositories, item_id)
            self._check_item_version(item, expected_version)
            if item.status != MemoryStatus.PROPOSED.value:
                raise MemoryStateError("只有 proposed 记忆可以批准。")
            item.status = MemoryStatus.ACTIVE.value
            item.updated_at = self._clock()
            await repositories.memory_items.update(item)
            return self._record(
                item,
                await self._current_version(repositories, item),
            )

    async def correct_memory(
        self,
        item_id: UUID,
        *,
        expected_version: int,
        content: str,
        dataset_scope: Mapping[str, Any] | None = None,
        provenance: Sequence[Mapping[str, Any]] | None = None,
    ) -> MemoryRecord:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.memory_settings.get_or_create_for_update()
            item = await self._locked_item(repositories, item_id)
            self._check_item_version(item, expected_version)
            if item.status not in {
                MemoryStatus.ACTIVE.value,
                MemoryStatus.PROPOSED.value,
            }:
                raise MemoryStateError()
            current = await self._current_version(repositories, item)
            if current is None:
                raise MemoryStateError("当前记忆版本不存在。")
            lineage = [
                {
                    "memory_item_id": str(item.id),
                    "replaces_version": expected_version,
                },
                *(dict(value) for value in current.source_refs),
                *(dict(value) for value in (provenance or ())),
            ]
            target_scope = (
                current.dataset_scope
                if dataset_scope is None
                else dict(dataset_scope)
            )
            if (
                MemoryKind(item.kind)
                is MemoryKind.SCIENTIFIC_OBSERVATION
                and dict(target_scope) != dict(current.dataset_scope)
            ):
                raise MemorySourceInvalidError(
                    "科学观测记忆不能通过 correction 迁移到另一数据集。"
                )
            validated = validate_memory_content(
                content,
                kind=MemoryKind(item.kind),
                dataset_scope=target_scope,
                provenance=lineage,
            )
            await self._require_content_available(
                repositories,
                fingerprint=validated.fingerprint,
                exclude_item_id=item.id,
            )
            next_version = expected_version + 1
            version = await repositories.memory_versions.add(
                MemoryVersion(
                    item_id=item.id,
                    version_number=next_version,
                    content=validated.content,
                    sha256=validated.sha256,
                    fingerprint=validated.fingerprint,
                    source_kind=MemorySourceKind.CORRECTED.value,
                    dataset_scope=validated.dataset_scope,
                    source_refs=list(validated.provenance),
                )
            )
            item.current_version = next_version
            item.dataset_scope = validated.dataset_scope
            item.updated_at = self._clock()
            await repositories.memory_items.update(item)
            return self._record(item, version)

    async def forget_memory(
        self,
        item_id: UUID,
        *,
        expected_version: int,
    ) -> MemoryRecord:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get_or_create_for_update()
            item = await self._locked_item(repositories, item_id)
            self._check_item_version(item, expected_version)
            if item.status == MemoryStatus.PURGED.value:
                raise MemoryStateError()
            if item.status != MemoryStatus.REVOKED.value:
                settings.disclosure_epoch += 1
            item.status = MemoryStatus.REVOKED.value
            item.updated_at = self._clock()
            await repositories.memory_items.update(item)
            return self._record(
                item,
                await self._current_version(repositories, item),
            )

    async def purge_memory(
        self,
        item_id: UUID,
        *,
        expected_version: int,
    ) -> MemoryRecord:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get_or_create_for_update()
            item = await self._locked_item(repositories, item_id)
            self._check_item_version(item, expected_version)
            if item.status == MemoryStatus.PURGED.value:
                raise MemoryStateError()
            settings.disclosure_epoch += 1
            versions = await repositories.memory_versions.list_for_item(item.id)
            fingerprints = {version.fingerprint for version in versions}
            source_message_ids = {
                message_id
                for version in versions
                for message_id in _source_message_ids(version.source_refs)
            }
            for fingerprint in fingerprints:
                if not await repositories.memory_suppressions.exists(
                    scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                    fingerprint=fingerprint,
                ):
                    await repositories.memory_suppressions.add(
                        MemorySuppression(
                            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                            fingerprint=fingerprint,
                            item_id=item.id,
                            reason="user_purge",
                        )
                    )
            for message_id in source_message_ids:
                fingerprint = _source_message_fingerprint(message_id)
                if not await repositories.memory_suppressions.exists(
                    scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                    fingerprint=fingerprint,
                ):
                    await repositories.memory_suppressions.add(
                        MemorySuppression(
                            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                            fingerprint=fingerprint,
                            item_id=item.id,
                            reason="user_purge_source",
                        )
                    )
            await repositories.memory_versions.delete_for_item(item.id)
            item.status = MemoryStatus.PURGED.value
            item.current_version = None
            item.stable_key = f"purged:{item.id}"
            item.dataset_scope = {}
            item.updated_at = self._clock()
            await repositories.memory_items.update(item)
            return self._record(item, None)

    async def search_identities(
        self,
        *,
        query: str,
        kinds: Sequence[MemoryKind | str] = (),
        limit: int = 8,
    ) -> tuple[MemoryResourceIdentity, ...]:
        normalized_query = validate_search_query(query)
        requested_kinds = (
            {MemoryKind(str(value)) for value in kinds}
            if kinds
            else {
                MemoryKind.RESPONSE_PREFERENCE,
                MemoryKind.PROFILE_FACT,
                MemoryKind.PROJECT_CONTEXT,
            }
        )
        if MemoryKind.SCIENTIFIC_OBSERVATION in requested_kinds:
            raise MemorySourceInvalidError(
                "scientific_observation 只能由用户按精确版本显式选择。"
            )
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get()
            self._require_search_enabled(settings)
            candidates = await self._active_candidates(
                repositories,
                kinds=requested_kinds,
                selection_reason=MemorySelectionReason.TOOL_SEARCH,
            )
            return tuple(
                candidate.identity
                for candidate in rank_memory_candidates(
                    normalized_query,
                    candidates,
                    limit=limit,
                    include_always_on_preferences=False,
                )
            )

    async def search_for_run(
        self,
        *,
        run_id: UUID,
        worker_id: str,
        expected_attempt: int,
        tool_call_id: str,
        kinds: Sequence[MemoryKind | str] = (),
        limit: int = 8,
    ) -> tuple[MemoryResourceIdentity, ...]:
        """Persist one attempt-fenced, identity-only Tool search result."""

        if (
            not worker_id
            or len(worker_id) > 255
            or expected_attempt < 0
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", tool_call_id) is None
            or not 1 <= limit <= 32
        ):
            raise MemorySourceInvalidError("记忆检索调用身份或上限非法。")
        requested_kinds = (
            {MemoryKind(str(value)) for value in kinds}
            if kinds
            else {
                MemoryKind.RESPONSE_PREFERENCE,
                MemoryKind.PROFILE_FACT,
                MemoryKind.PROJECT_CONTEXT,
            }
        )
        if MemoryKind.SCIENTIFIC_OBSERVATION in requested_kinds:
            raise MemorySourceInvalidError(
                "scientific_observation 只能由用户按精确版本显式选择。"
            )
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            now = self._clock()
            if (
                run is None
                or run.worker_id != worker_id
                or run.attempt != expected_attempt
                or run.status != RunStatus.RUNNING.value
                or run.lease_expires_at is None
                or run.lease_expires_at <= now
            ):
                raise MemoryAttemptFenceError()
            goal = validate_search_query(
                str(run.request_payload.get("goal") or "")
            )
            snapshot = await repositories.memory_snapshots.get_by_run(run_id)
            if snapshot is None or snapshot.mode == "off":
                raise MemoryDisabledError("当前 run 未启用跨会话记忆读取。")
            settings = await repositories.memory_settings.get_for_share()
            request_sha256 = hashlib.sha256(
                json.dumps(
                    {
                        "goal": goal,
                        "kinds": sorted(value.value for value in requested_kinds),
                        "limit": limit,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            existing = await repositories.memory_searches.get_for_update(
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise MemoryConflictError(
                        "同一 Tool call 已绑定不同的记忆检索参数。"
                    )
                try:
                    identities = tuple(
                        MemoryResourceIdentity.from_mapping(value)
                        for value in existing.result_identities
                    )
                except ValueError as exc:
                    raise MemoryStateError(
                        "持久化的记忆检索 identity 非法。"
                    ) from exc
                if any(
                    value.selection_reason
                    is not MemorySelectionReason.TOOL_SEARCH
                    for value in identities
                ):
                    raise MemoryStateError(
                        "持久化的记忆检索来源非法。"
                    )
                return identities
            self._require_search_enabled(settings)
            candidates = await self._active_candidates(
                repositories,
                kinds=requested_kinds,
                selection_reason=MemorySelectionReason.TOOL_SEARCH,
            )
            identities = tuple(
                candidate.identity
                for candidate in rank_memory_candidates(
                    goal,
                    candidates,
                    limit=limit,
                    include_always_on_preferences=False,
                )
            )
            await repositories.memory_searches.add(
                RunMemorySearch(
                    run_id=run_id,
                    snapshot_id=snapshot.id,
                    tool_call_id=tool_call_id,
                    request_sha256=request_sha256,
                    worker_id=worker_id,
                    attempt=expected_attempt,
                    result_count=len(identities),
                    result_identities=[
                        value.to_checkpoint_dict() for value in identities
                    ],
                )
            )
            return identities

    async def propose_from_run(
        self,
        *,
        run_id: UUID,
        worker_id: str,
        expected_attempt: int,
        tool_call_id: str,
        kind: MemoryKind | str,
        source_message_id: UUID,
        dataset_scope: Mapping[str, Any] | None = None,
    ) -> MemoryResourceIdentity:
        normalized_kind = MemoryKind(str(kind))
        if normalized_kind is MemoryKind.SCIENTIFIC_OBSERVATION:
            raise MemorySourceInvalidError(
                "Agent 不能从当前对话自动提议 scientific_observation。"
            )
        if not isinstance(source_message_id, UUID):
            raise MemorySourceInvalidError(
                "Agent 记忆候选必须引用一条用户 message identity。"
            )
        if (
            not worker_id
            or len(worker_id) > 255
            or expected_attempt < 0
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", tool_call_id) is None
        ):
            raise MemorySourceInvalidError()
        request_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "kind": normalized_kind.value,
                    "source_message_id": str(source_message_id),
                    "dataset_scope": dict(dataset_scope or {}),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            now = self._clock()
            if (
                run is None
                or run.worker_id != worker_id
                or run.attempt != expected_attempt
                or run.status != RunStatus.RUNNING.value
                or run.lease_expires_at is None
                or run.lease_expires_at <= now
            ):
                raise MemoryAttemptFenceError()
            existing = await repositories.memory_proposals.get_for_update(
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise MemoryConflictError(
                        "同一 Tool call 已绑定不同的记忆提议。"
                    )
                try:
                    return MemoryResourceIdentity.from_mapping(
                        existing.memory_identity
                    )
                except ValueError as exc:
                    raise MemoryStateError(
                        "持久化的记忆提议 identity 非法。"
                    ) from exc
            settings = (
                await repositories.memory_settings.get_or_create_for_update()
            )
            # Purge takes the same settings row lock before creating source
            # tombstones.  Holding it from this check through proposal commit
            # makes "proposal before purge" and "purge before proposal"
            # linearisable; no new call can check before purge and write after.
            if await repositories.memory_suppressions.exists(
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                fingerprint=_source_message_fingerprint(source_message_id),
            ):
                raise MemorySuppressedError(
                    "该来源已被永久清除，不能从旧会话重新生成记忆。"
                )
            if (
                await repositories.memory_proposals.get_any_for_run(
                    run_id=run_id
                )
                is not None
            ):
                raise MemoryProposalLimitError()
            events = await repositories.events.replay(
                run_id,
                after_sequence=0,
                limit=5_000,
            )
            messages: dict[UUID, str] = {}
            for event in events:
                if event.event_type != "message.completed":
                    continue
                payload = event.payload
                try:
                    message_id = UUID(str(payload["message_id"]))
                    role = str(payload["role"])
                    body = payload["content"]
                except (KeyError, TypeError, ValueError):
                    continue
                if role == "user" and isinstance(body, str):
                    messages[message_id] = body
            if source_message_id not in messages:
                raise MemorySourceInvalidError()
            body = messages[source_message_id]
            provenance = [
                {
                    "conversation_id": str(run.conversation_id),
                    "run_id": str(run.id),
                    "message_ids": [str(source_message_id)],
                }
            ]
            validated = validate_memory_content(
                body,
                kind=normalized_kind,
                dataset_scope=dataset_scope,
                provenance=provenance,
                preserve_original=True,
            )
            key = validate_stable_key(
                None,
                kind=normalized_kind,
                content_sha256=validated.sha256,
            )
            if (
                not settings.tools_enabled
                or not settings.generation_enabled
            ):
                raise MemoryDisabledError()
            await self._require_content_available(
                repositories,
                fingerprint=validated.fingerprint,
            )
            if await repositories.memory_items.get_by_stable_key_for_update(
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                stable_key=key,
            ) is not None:
                raise MemoryConflictError("stable key 已存在。")
            item = await repositories.memory_items.add(
                MemoryItem(
                    scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                    kind=normalized_kind.value,
                    stable_key=key,
                    status=MemoryStatus.PROPOSED.value,
                    current_version=1,
                    dataset_scope=validated.dataset_scope,
                    origin_run_id=run_id,
                    origin_attempt=expected_attempt,
                    origin_tool_call_id=tool_call_id,
                )
            )
            version = await repositories.memory_versions.add(
                MemoryVersion(
                    item_id=item.id,
                    version_number=1,
                    content=validated.content,
                    sha256=validated.sha256,
                    fingerprint=validated.fingerprint,
                    source_kind=MemorySourceKind.PROPOSED.value,
                    dataset_scope=validated.dataset_scope,
                    source_refs=list(validated.provenance),
                )
            )
            identity = self._identity(
                item,
                version,
                MemorySelectionReason.SELECTED,
            )
            await repositories.memory_proposals.add(
                RunMemoryProposal(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    request_sha256=request_sha256,
                    worker_id=worker_id,
                    attempt=expected_attempt,
                    memory_identity=identity.to_checkpoint_dict(),
                )
            )
            return identity

    async def verify_forget_intent(
        self,
        *,
        item_id: UUID,
        version_id: UUID,
    ) -> MemoryResourceIdentity:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            settings = await repositories.memory_settings.get()
            if settings is None or not settings.tools_enabled:
                raise MemoryDisabledError()
            item = await repositories.memory_items.get(
                item_id,
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            )
            if (
                item is None
                or item.status == MemoryStatus.PURGED.value
                or item.current_version is None
            ):
                raise MemoryNotFoundError()
            version = await repositories.memory_versions.get_by_id(version_id)
            if (
                version is None
                or version.item_id != item.id
                or version.version_number != item.current_version
            ):
                raise MemoryConflictError()
            return self._identity(
                item,
                version,
                MemorySelectionReason.SELECTED,
            )

    async def request_forget_from_run(
        self,
        *,
        run_id: UUID,
        worker_id: str,
        expected_attempt: int,
        tool_call_id: str,
        item_id: UUID,
        version_id: UUID,
    ) -> MemoryResourceIdentity:
        if (
            not worker_id
            or len(worker_id) > 255
            or expected_attempt < 0
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", tool_call_id) is None
        ):
            raise MemorySourceInvalidError(
                "记忆遗忘请求的调用身份非法。"
            )
        request_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "item_id": str(item_id),
                    "version_id": str(version_id),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            now = self._clock()
            if (
                run is None
                or run.worker_id != worker_id
                or run.attempt != expected_attempt
                or run.status != RunStatus.RUNNING.value
                or run.lease_expires_at is None
                or run.lease_expires_at <= now
            ):
                raise MemoryAttemptFenceError()
            existing = (
                await repositories.memory_forget_intents.get_for_update(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                )
            )
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise MemoryConflictError(
                        "同一 Tool call 已绑定不同的遗忘目标。"
                    )
                try:
                    return MemoryResourceIdentity.from_mapping(
                        existing.memory_identity
                    )
                except ValueError as exc:
                    raise MemoryStateError(
                        "持久化的遗忘请求 identity 非法。"
                    ) from exc
            settings = await repositories.memory_settings.get_for_share()
            if settings is None or not settings.tools_enabled:
                raise MemoryDisabledError()
            item = await repositories.memory_items.get(
                item_id,
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            )
            if (
                item is None
                or item.status == MemoryStatus.PURGED.value
                or item.current_version is None
            ):
                raise MemoryNotFoundError()
            version = await repositories.memory_versions.get_by_id(version_id)
            if (
                version is None
                or version.item_id != item.id
                or version.version_number != item.current_version
            ):
                raise MemoryConflictError()
            identity = self._identity(
                item,
                version,
                MemorySelectionReason.SELECTED,
            )
            await repositories.memory_forget_intents.add(
                RunMemoryForgetIntent(
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    request_sha256=request_sha256,
                    worker_id=worker_id,
                    attempt=expected_attempt,
                    memory_identity=identity.to_checkpoint_dict(),
                )
            )
            return identity

    @staticmethod
    def _check_settings_version(
        settings: MemorySettings,
        expected_version: int | None,
    ) -> None:
        if expected_version is not None and settings.version != expected_version:
            raise MemoryConflictError()

    @staticmethod
    def _consent_granted(settings: MemorySettings) -> bool:
        return bool(
            settings.provider_consent_version
            == MEMORY_PROVIDER_CONSENT_VERSION
            and settings.provider_consent_at
        )

    def _require_search_enabled(self, settings: MemorySettings | None) -> None:
        if settings is None or not settings.use_enabled or not settings.tools_enabled:
            raise MemoryDisabledError()
        if not self._consent_granted(settings):
            raise MemoryProviderConsentRequiredError()

    async def _active_candidates(
        self,
        repositories: Any,
        *,
        kinds: set[MemoryKind],
        selection_reason: MemorySelectionReason,
    ) -> tuple[MemoryCandidate, ...]:
        rows = await repositories.memory_items.list(
            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            status=MemoryStatus.ACTIVE.value,
            offset=0,
            limit=5_000,
        )
        now = self._clock()
        candidates: list[MemoryCandidate] = []
        for item in rows:
            kind = MemoryKind(item.kind)
            if kind not in kinds or (
                item.expires_at is not None and item.expires_at <= now
            ):
                continue
            version = await self._current_version(repositories, item)
            if version is None:
                continue
            if await repositories.memory_suppressions.exists(
                scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
                fingerprint=version.fingerprint,
            ):
                continue
            try:
                validated = validate_memory_content(
                    version.content,
                    kind=kind,
                    dataset_scope=version.dataset_scope,
                    provenance=version.source_refs,
                )
            except MemoryError:
                raise
            if (
                validated.sha256 != version.sha256
                or validated.fingerprint != version.fingerprint
            ):
                raise MemoryStateError(
                    "记忆正文与持久化 identity 不一致。"
                )
            candidates.append(
                MemoryCandidate(
                    identity=self._identity(item, version, selection_reason),
                    stable_key=item.stable_key,
                    content=validated.content,
                    dataset_scope=validated.dataset_scope,
                    provenance=validated.provenance,
                    updated_at=item.updated_at,
                )
            )
        return tuple(candidates)

    async def _locked_item(self, repositories: Any, item_id: UUID) -> MemoryItem:
        item = await repositories.memory_items.get_for_update(
            item_id,
            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
        )
        if item is None:
            raise MemoryNotFoundError()
        return item

    @staticmethod
    async def _require_content_available(
        repositories: Any,
        *,
        fingerprint: str,
        exclude_item_id: UUID | None = None,
    ) -> None:
        if await repositories.memory_suppressions.exists(
            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            fingerprint=fingerprint,
        ):
            raise MemorySuppressedError()
        if await repositories.memory_versions.current_exists_by_fingerprint(
            scope_key=LOCAL_DEFAULT_MEMORY_SCOPE,
            fingerprint=fingerprint,
            exclude_item_id=exclude_item_id,
        ):
            raise MemoryConflictError("相同正文的活动记忆已经存在。")

    @staticmethod
    def _check_item_version(item: MemoryItem, expected_version: int) -> None:
        if item.current_version != expected_version:
            raise MemoryConflictError()

    @staticmethod
    async def _current_version(
        repositories: Any,
        item: MemoryItem,
    ) -> MemoryVersion | None:
        if item.current_version is None:
            return None
        return await repositories.memory_versions.get_exact(
            item_id=item.id,
            version_number=item.current_version,
        )

    @staticmethod
    def _identity(
        item: MemoryItem,
        version: MemoryVersion,
        selection_reason: MemorySelectionReason,
    ) -> MemoryResourceIdentity:
        return MemoryResourceIdentity(
            item_id=item.id,
            version_id=version.id,
            version_number=version.version_number,
            content_sha256=version.sha256,
            kind=MemoryKind(item.kind),
            source_kind=MemorySourceKind(version.source_kind),
            selection_reason=selection_reason,
        )

    @staticmethod
    def _record(
        item: MemoryItem,
        version: MemoryVersion | None,
    ) -> MemoryRecord:
        now = _aware_now()
        return MemoryRecord(
            item_id=item.id,
            scope_key=item.scope_key,
            stable_key=item.stable_key,
            kind=MemoryKind(item.kind),
            status=MemoryStatus(item.status),
            current_version=item.current_version,
            version_id=version.id if version is not None else None,
            content_sha256=version.sha256 if version is not None else None,
            content=version.content if version is not None else None,
            source_kind=(
                MemorySourceKind(version.source_kind)
                if version is not None
                else None
            ),
            source_refs=(
                tuple(dict(value) for value in version.source_refs)
                if version is not None
                else ()
            ),
            dataset_scope=dict(item.dataset_scope or {}),
            expires_at=item.expires_at,
            created_at=item.created_at or now,
            updated_at=item.updated_at or now,
        )

    @staticmethod
    def _settings_state(settings: MemorySettings) -> MemorySettingsState:
        now = _aware_now()
        return MemorySettingsState(
            scope_key=settings.scope_key,
            use_enabled=settings.use_enabled,
            generation_enabled=settings.generation_enabled,
            tools_enabled=settings.tools_enabled,
            provider_consent_version=settings.provider_consent_version,
            provider_consented_at=settings.provider_consent_at,
            version=settings.version,
            updated_at=settings.updated_at or now,
        )

    def _validate_expiry(self, expires_at: datetime | None) -> None:
        if expires_at is None:
            return
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("memory expires_at must be timezone-aware")
        if expires_at <= self._clock():
            raise ValueError("memory expires_at must be in the future")


__all__ = ["MemoryService"]
