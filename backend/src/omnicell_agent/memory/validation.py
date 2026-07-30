"""Server-side memory validation shared by every write path."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .errors import MemoryContentRejectedError
from .types import MemoryKind


MAX_MEMORY_CHARS = 8_000
MAX_METADATA_BYTES = 16 * 1024

_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"password|passwd)\s*[:=]\s*[\"']?[^\s\"']{6,}",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@", re.IGNORECASE),
)

_HOST_PATH_PATTERNS = (
    re.compile(
        r"(?<![\w.])/(?:Users|home|root|private|var/folders|etc|opt|tmp)/"
        r"[^\s<>'\"]+"
    ),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\(?:Users|Windows|Program Files)\\[^\r\n]+"),
    re.compile(r"\bfile:///[^\s<>'\"]+", re.IGNORECASE),
)

_IDENTITY_PATTERNS = (
    re.compile(
        r"\b(?:patient|subject|donor|sample)[_-]?(?:id|name)\s*[:=]\s*"
        r"[\"']?[A-Za-z0-9._-]{3,}",
        re.IGNORECASE,
    ),
    re.compile(r"(?:患者\s*(?:ID|编号|姓名)|病例号|住院号|身份证号?)\s*[:：=]\s*\S{2,}"),
    re.compile(r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
               r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
)

_EXECUTION_OUTPUT_PATTERNS = (
    re.compile(r"%%MatrixMarket\s+matrix", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:stdout|stderr)\s*[:=]", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*Traceback \(most recent call last\):"),
    re.compile(r"[\"'](?:stdout|stderr|environment|env)[\"']\s*:\s*[\"'{[]", re.IGNORECASE),
)

_MATRIX_ROW = re.compile(
    r"(?m)^(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?[,	 ]){11,}"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*$"
)


@dataclass(frozen=True, slots=True)
class ValidatedMemoryContent:
    content: str
    sha256: str
    fingerprint: str
    dataset_scope: dict[str, str]
    provenance: tuple[dict[str, Any], ...]


def _reject_if_sensitive(value: str) -> None:
    for patterns in (
        _CREDENTIAL_PATTERNS,
        _HOST_PATH_PATTERNS,
        _IDENTITY_PATTERNS,
        _EXECUTION_OUTPUT_PATTERNS,
    ):
        if any(pattern.search(value) for pattern in patterns):
            raise MemoryContentRejectedError()
    if _MATRIX_ROW.search(value):
        raise MemoryContentRejectedError()


def _normalize_content(content: str) -> str:
    if not isinstance(content, str):
        raise MemoryContentRejectedError()
    if not 1 <= len(content) <= MAX_MEMORY_CHARS:
        raise MemoryContentRejectedError()
    normalized = unicodedata.normalize("NFC", content).strip()
    if not 1 <= len(normalized) <= MAX_MEMORY_CHARS:
        raise MemoryContentRejectedError()
    if "\x00" in normalized or any(
        ord(character) < 32 and character not in "\n\r\t"
        for character in normalized
    ):
        raise MemoryContentRejectedError()
    _reject_if_sensitive(normalized)
    return normalized


def memory_fingerprint(content: str) -> str:
    normalized = " ".join(
        unicodedata.normalize("NFKC", content).casefold().split()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_dataset_scope(
    value: Mapping[str, Any] | None,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > 16:
        raise MemoryContentRejectedError()
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise MemoryContentRejectedError()
        key = unicodedata.normalize("NFC", raw_key).strip()
        item = unicodedata.normalize("NFC", raw_value).strip()
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key)
            or not 1 <= len(item) <= 512
        ):
            raise MemoryContentRejectedError()
        _reject_if_sensitive(f"{key}:{item}")
        normalized[key] = item
    return normalized


def _validate_provenance(
    value: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or len(value) > 32:
        raise MemoryContentRejectedError()
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise MemoryContentRejectedError()
        candidate = dict(item)
        try:
            encoded = json.dumps(
                candidate,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError) as exc:
            raise MemoryContentRejectedError() from exc
        _reject_if_sensitive(encoded)
        normalized.append(candidate)
    if len(
        json.dumps(normalized, ensure_ascii=False, default=str).encode("utf-8")
    ) > MAX_METADATA_BYTES:
        raise MemoryContentRejectedError()
    return tuple(normalized)


def validate_memory_content(
    content: str,
    *,
    kind: MemoryKind | str,
    dataset_scope: Mapping[str, Any] | None = None,
    provenance: Sequence[Mapping[str, Any]] | None = None,
    preserve_original: bool = False,
) -> ValidatedMemoryContent:
    try:
        normalized_kind = MemoryKind(str(kind))
    except ValueError as exc:
        raise MemoryContentRejectedError() from exc
    normalized = _normalize_content(content)
    stored_content = content if preserve_original else normalized
    normalized_scope = _validate_dataset_scope(dataset_scope)
    normalized_provenance = _validate_provenance(provenance)
    if normalized_kind is MemoryKind.SCIENTIFIC_OBSERVATION:
        scoped_artifact_id = normalized_scope.get("artifact_id")
        verified_source = any(
            item.get("source_verified") is True
            and item.get("conversation_id")
            and item.get("run_id")
            and str(item.get("artifact_id")) == scoped_artifact_id
            and isinstance(item.get("message_ids"), list)
            and bool(item["message_ids"])
            for item in normalized_provenance
        )
        if not scoped_artifact_id or not verified_source:
            raise MemoryContentRejectedError(
                "科学观测记忆必须具有 dataset scope 和经权威校验的来源。"
            )
    return ValidatedMemoryContent(
        content=stored_content,
        sha256=hashlib.sha256(stored_content.encode("utf-8")).hexdigest(),
        fingerprint=memory_fingerprint(normalized),
        dataset_scope=normalized_scope,
        provenance=normalized_provenance,
    )


def validate_stable_key(
    stable_key: str | None,
    *,
    kind: MemoryKind,
    content_sha256: str,
) -> str:
    if stable_key is None:
        return f"{kind.value}:{content_sha256[:24]}"
    normalized = unicodedata.normalize("NFC", stable_key).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", normalized):
        raise MemoryContentRejectedError()
    _reject_if_sensitive(normalized)
    return normalized


def validate_search_query(query: str) -> str:
    if not isinstance(query, str):
        raise MemoryContentRejectedError()
    normalized = unicodedata.normalize("NFC", query).strip()
    if not 1 <= len(normalized) <= 1_000:
        raise MemoryContentRejectedError()
    _reject_if_sensitive(normalized)
    return normalized


__all__ = [
    "MAX_MEMORY_CHARS",
    "ValidatedMemoryContent",
    "memory_fingerprint",
    "validate_memory_content",
    "validate_search_query",
    "validate_stable_key",
]
