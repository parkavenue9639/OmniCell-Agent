"""Agent event observer port; persistence is owned by the run coordinator."""

from __future__ import annotations

from typing import Any, Protocol


class AgentObserver(Protocol):
    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> None: ...


class NullAgentObserver:
    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> None:
        del event_type, payload, dedupe_key


__all__ = ["AgentObserver", "NullAgentObserver"]
