"""Agent-facing, identity-only boundary for cross-conversation memory.

The generic Loop depends on these protocols rather than PostgreSQL.  Memory
bodies are resolved by a per-turn hook and never cross the ToolMessage or
checkpoint boundary.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from .tooling import AgentToolFatalError


class AgentMemoryControlError(RuntimeError):
    def __init__(
        self,
        *,
        error_code: str,
        summary: str,
        retryable: bool,
        recovery_hint: str,
    ) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary
        self.retryable = retryable
        self.recovery_hint = recovery_hint


class AgentMemoryControlFatalError(AgentToolFatalError):
    """A run-ownership failure that must abort the current Agent execution."""

    def __init__(
        self,
        *,
        error_code: str,
        summary: str,
        recovery_hint: str,
    ) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary
        self.retryable = False
        self.recovery_hint = recovery_hint


class AgentMemoryControlPort(Protocol):
    async def search(
        self,
        *,
        kinds: tuple[str, ...],
        limit: int,
        tool_call_id: str,
    ) -> tuple[dict[str, Any], ...]: ...

    async def propose(
        self,
        *,
        kind: str,
        source_message_id: UUID,
        tool_call_id: str,
    ) -> dict[str, Any]: ...

    async def request_forget(
        self,
        *,
        item_id: UUID,
        version_id: UUID,
        tool_call_id: str,
    ) -> dict[str, Any]: ...


__all__ = [
    "AgentMemoryControlError",
    "AgentMemoryControlFatalError",
    "AgentMemoryControlPort",
]
