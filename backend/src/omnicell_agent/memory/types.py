"""Value types shared by Memory Plane services and Agent adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID


LOCAL_DEFAULT_MEMORY_SCOPE = "local-default"
MEMORY_PROVIDER_CONSENT_VERSION = "memory-provider-v1"


class MemoryScope(StrEnum):
    LOCAL_DEFAULT = LOCAL_DEFAULT_MEMORY_SCOPE


class MemoryKind(StrEnum):
    RESPONSE_PREFERENCE = "response_preference"
    PROFILE_FACT = "profile_fact"
    PROJECT_CONTEXT = "project_context"
    SCIENTIFIC_OBSERVATION = "scientific_observation"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REVOKED = "revoked"
    PURGED = "purged"


class MemoryRunMode(StrEnum):
    OFF = "off"
    DEFAULT = "default"
    SELECTED = "selected"


class MemorySourceKind(StrEnum):
    EXPLICIT = "explicit"
    PROPOSED = "proposed"
    CORRECTED = "corrected"


class MemorySelectionReason(StrEnum):
    DEFAULT = "default"
    SELECTED = "selected"
    TOOL_SEARCH = "tool_search"


@dataclass(frozen=True, slots=True)
class MemorySelectionRef:
    item_id: UUID
    version_id: UUID

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MemorySelectionRef":
        try:
            return cls(
                item_id=UUID(str(value["item_id"])),
                version_id=UUID(str(value["version_id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid memory selection identity") from exc


@dataclass(frozen=True, slots=True)
class MemoryResourceIdentity:
    """Exact identity that may cross checkpoint and ToolMessage boundaries."""

    item_id: UUID
    version_id: UUID
    version_number: int
    content_sha256: str
    kind: MemoryKind
    source_kind: MemorySourceKind
    selection_reason: MemorySelectionReason

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("memory version_number must be positive")
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.content_sha256
        ):
            raise ValueError("memory content_sha256 must be a lowercase sha256")

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return {
            "item_id": str(self.item_id),
            "version_id": str(self.version_id),
            "version_number": self.version_number,
            "content_sha256": self.content_sha256,
            "kind": self.kind.value,
            "source_kind": self.source_kind.value,
            "selection_reason": self.selection_reason.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MemoryResourceIdentity":
        try:
            return cls(
                item_id=UUID(str(value["item_id"])),
                version_id=UUID(str(value["version_id"])),
                version_number=int(value["version_number"]),
                content_sha256=str(value["content_sha256"]),
                kind=MemoryKind(str(value["kind"])),
                source_kind=MemorySourceKind(str(value["source_kind"])),
                selection_reason=MemorySelectionReason(
                    str(value["selection_reason"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid memory resource identity") from exc


@dataclass(frozen=True, slots=True)
class MemorySettingsState:
    scope_key: str
    use_enabled: bool
    generation_enabled: bool
    tools_enabled: bool
    provider_consent_version: str | None
    provider_consented_at: datetime | None
    version: int
    updated_at: datetime

    @property
    def provider_consent_granted(self) -> bool:
        return bool(
            self.provider_consent_version == MEMORY_PROVIDER_CONSENT_VERSION
            and self.provider_consented_at
        )


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    item_id: UUID
    scope_key: str
    stable_key: str
    kind: MemoryKind
    status: MemoryStatus
    current_version: int | None
    version_id: UUID | None
    content_sha256: str | None
    content: str | None
    source_kind: MemorySourceKind | None
    source_refs: tuple[dict[str, Any], ...]
    dataset_scope: dict[str, Any]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def identity(self) -> MemoryResourceIdentity | None:
        if (
            self.current_version is None
            or self.version_id is None
            or self.content_sha256 is None
            or self.source_kind is None
        ):
            return None
        return MemoryResourceIdentity(
            item_id=self.item_id,
            version_id=self.version_id,
            version_number=self.current_version,
            content_sha256=self.content_sha256,
            kind=self.kind,
            source_kind=self.source_kind,
            selection_reason=MemorySelectionReason.SELECTED,
        )


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    identity: MemoryResourceIdentity
    stable_key: str
    content: str
    dataset_scope: dict[str, str]
    provenance: tuple[dict[str, Any], ...]
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResolvedMemoryBody:
    identity: MemoryResourceIdentity
    content: str


@dataclass(frozen=True, slots=True)
class MemorySnapshotResult:
    snapshot_id: UUID
    mode: MemoryRunMode
    outcome: str
    identities: tuple[MemoryResourceIdentity, ...]
    content_bytes: int
    degraded_code: str | None = None


__all__ = [
    "LOCAL_DEFAULT_MEMORY_SCOPE",
    "MEMORY_PROVIDER_CONSENT_VERSION",
    "MemoryCandidate",
    "MemoryKind",
    "MemoryRecord",
    "MemoryResourceIdentity",
    "MemoryRunMode",
    "MemoryScope",
    "MemorySelectionReason",
    "MemorySelectionRef",
    "MemorySettingsState",
    "MemorySnapshotResult",
    "MemorySourceKind",
    "MemoryStatus",
    "ResolvedMemoryBody",
]
