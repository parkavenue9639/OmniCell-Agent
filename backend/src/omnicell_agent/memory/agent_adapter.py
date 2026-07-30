"""Run-bound identity-only implementation of Agent memory control tools."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from omnicell_agent.agent.memory import (
    AgentMemoryControlError,
    AgentMemoryControlFatalError,
)

from .errors import (
    MemoryAttemptFenceError,
    MemoryContentRejectedError,
    MemoryError,
)
from .service import MemoryService
from .types import MemoryKind, MemoryResourceIdentity


class RunBoundMemoryControlAdapter:
    def __init__(
        self,
        service: MemoryService,
        *,
        run_id: UUID,
        worker_id: str,
        expected_attempt: int,
    ) -> None:
        self._service = service
        self._run_id = run_id
        self._worker_id = worker_id
        self._expected_attempt = expected_attempt

    async def search(
        self,
        *,
        kinds: tuple[str, ...],
        limit: int,
        tool_call_id: str,
    ) -> tuple[dict[str, Any], ...]:
        try:
            identities = await self._service.search_for_run(
                run_id=self._run_id,
                worker_id=self._worker_id,
                expected_attempt=self._expected_attempt,
                tool_call_id=tool_call_id,
                kinds=kinds,
                limit=limit,
            )
            return tuple(identity.to_checkpoint_dict() for identity in identities)
        except MemoryAttemptFenceError as exc:
            raise self._agent_fatal_error(exc) from None
        except MemoryError as exc:
            raise self._agent_error(exc) from exc

    async def propose(
        self,
        *,
        kind: str,
        source_message_id: UUID,
        tool_call_id: str,
    ) -> dict[str, Any]:
        try:
            identity = await self._service.propose_from_run(
                run_id=self._run_id,
                worker_id=self._worker_id,
                expected_attempt=self._expected_attempt,
                tool_call_id=tool_call_id,
                kind=MemoryKind(kind),
                source_message_id=source_message_id,
            )
            payload = identity.to_checkpoint_dict()
            payload["status"] = "proposed"
            return payload
        except MemoryAttemptFenceError as exc:
            raise self._agent_fatal_error(exc) from None
        except MemoryContentRejectedError as exc:
            raise AgentMemoryControlError(
                error_code=exc.error_code,
                summary=exc.summary,
                retryable=False,
                recovery_hint=(
                    "不要改写、拆分或重新提交当前来源消息；继续或结束当前请求。"
                ),
            ) from exc
        except MemoryError as exc:
            raise self._agent_error(exc) from exc

    async def request_forget(
        self,
        *,
        item_id: UUID,
        version_id: UUID,
        tool_call_id: str,
    ) -> dict[str, Any]:
        try:
            identity = await self._service.request_forget_from_run(
                run_id=self._run_id,
                worker_id=self._worker_id,
                expected_attempt=self._expected_attempt,
                tool_call_id=tool_call_id,
                item_id=item_id,
                version_id=version_id,
            )
            return {
                "status": "confirmation_required",
                **identity.to_checkpoint_dict(),
            }
        except MemoryAttemptFenceError as exc:
            raise self._agent_fatal_error(exc) from None
        except MemoryError as exc:
            raise self._agent_error(exc) from exc

    @staticmethod
    def _agent_error(error: MemoryError) -> AgentMemoryControlError:
        return AgentMemoryControlError(
            error_code=error.error_code,
            summary=error.summary,
            retryable=error.retryable,
            recovery_hint=error.recovery_hint,
        )

    @staticmethod
    def _agent_fatal_error(
        error: MemoryAttemptFenceError,
    ) -> AgentMemoryControlFatalError:
        return AgentMemoryControlFatalError(
            error_code=error.error_code,
            summary=error.summary,
            recovery_hint=error.recovery_hint,
        )


__all__ = ["RunBoundMemoryControlAdapter"]
