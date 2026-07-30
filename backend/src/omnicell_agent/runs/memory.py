"""Run-level protocol for preparing and resolving cross-conversation memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from omnicell_agent.agent.hooks import MemoryContextResolver
from omnicell_agent.agent.memory import AgentMemoryControlPort


class RunMemoryPreparationError(RuntimeError):
    def __init__(self, *, error_code: str, summary: str) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary


@dataclass(frozen=True, slots=True)
class PreparedMemoryInput:
    item_id: UUID
    version_id: UUID
    version_number: int
    content_sha256: str
    kind: str
    source_kind: str
    selection_reason: str

    def public_identity(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "kind": self.kind,
            "source_kind": self.source_kind,
            "selection_reason": self.selection_reason,
        }


@dataclass(frozen=True, slots=True)
class PreparedMemoryContext:
    snapshot_id: UUID
    mode: str
    outcome: str
    inputs: tuple[PreparedMemoryInput, ...]
    content_bytes: int
    degraded_code: str | None = None


class RunMemoryRuntime(Protocol):
    async def prepare_snapshot(
        self,
        *,
        repositories: Any,
        run: Any,
        goal: str,
        worker_id: str,
        expected_attempt: int,
    ) -> PreparedMemoryContext | None:
        """Prepare or replay an exact snapshot inside the caller's UoW."""

    def resolver(self, run_id: UUID) -> MemoryContextResolver: ...

    async def control_port(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        expected_attempt: int,
    ) -> AgentMemoryControlPort | None: ...


__all__ = [
    "PreparedMemoryContext",
    "PreparedMemoryInput",
    "RunMemoryPreparationError",
    "RunMemoryRuntime",
]
