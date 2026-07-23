"""Trusted runtime activity channel for public execution transcripts.

The executed container cannot write this channel directly.  A trusted runtime
adapter emits bounded records after removing host paths and common credential
forms; the parent process later binds them to the authoritative run identity.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


RuntimeActivitySink = Callable[[Mapping[str, Any]], None]

_ACTIVITY_SINK: ContextVar[RuntimeActivitySink | None] = ContextVar(
    "omnicell_runtime_activity_sink",
    default=None,
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (
        ["']?
        (?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)
        ["']?
        \s*[:=]\s*
    )
    (
        "(?:\\.|[^"\\])*"
        |
        '(?:\\.|[^'\\])*'
        |
        [^\s,;}\]]+
    )
    """
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OPENAI_STYLE_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")


@contextmanager
def runtime_activity_scope(sink: RuntimeActivitySink | None) -> Iterator[None]:
    token = _ACTIVITY_SINK.set(sink)
    try:
        yield
    finally:
        _ACTIVITY_SINK.reset(token)


def current_runtime_activity_sink() -> RuntimeActivitySink | None:
    return _ACTIVITY_SINK.get()


def sanitize_runtime_text(
    value: str,
    *,
    host_workspace: str | None = None,
    max_bytes: int,
) -> tuple[str, bool, bool]:
    """Return text plus explicit ``redacted`` and ``truncated`` flags."""

    if max_bytes < 0:
        raise ValueError("runtime public text max_bytes 不能为负数")
    text = value
    redacted = False
    if host_workspace and host_workspace in text:
        text = text.replace(host_workspace, "<conversation-workspace>")
        redacted = True

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal redacted
        redacted = True
        raw_value = match.group(2)
        if raw_value.startswith('"') and raw_value.endswith('"'):
            replacement = '"<redacted>"'
        elif raw_value.startswith("'") and raw_value.endswith("'"):
            replacement = "'<redacted>'"
        else:
            replacement = "<redacted>"
        return f"{match.group(1)}{replacement}"

    text = _SECRET_ASSIGNMENT.sub(replace_assignment, text)
    for pattern, replacement in (
        (_BEARER_TOKEN, "Bearer <redacted>"),
        (_OPENAI_STYLE_TOKEN, "sk-<redacted>"),
    ):
        updated, count = pattern.subn(replacement, text)
        if count:
            redacted = True
            text = updated

    encoded = text.encode("utf-8")
    truncated = len(encoded) > max_bytes
    if truncated:
        # 切片可能落在多字节字符中间；忽略不完整尾部可保持合法 UTF-8，
        # 同时保证公开 frame 与事件 payload 的限制按真实 wire bytes 生效。
        text = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return text, redacted, truncated


__all__ = [
    "RuntimeActivitySink",
    "current_runtime_activity_sink",
    "runtime_activity_scope",
    "sanitize_runtime_text",
]
