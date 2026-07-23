"""Cooperative cancellation shared by the Agent and capability adapter."""

from __future__ import annotations

import asyncio
import threading

from omnicell_agent.runtime.cancellation import RuntimeCancellationRegistry


class RunCancelledError(asyncio.CancelledError):
    """A requested product-level cancellation, distinct from client disconnect."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._thread_event = threading.Event()
        self._reason = "run cancellation requested"
        self._lease_generation = 0
        self._lease_event = asyncio.Event()
        self._lease_watchdog_timeout_seconds: float | None = None
        self.runtime = RuntimeCancellationRegistry()

    @property
    def is_cancelled(self) -> bool:
        return self._thread_event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "run cancellation requested") -> bool:
        if self._thread_event.is_set():
            return False
        normalized = reason.strip()
        self._reason = normalized[:500] or "run cancellation requested"
        self._thread_event.set()
        self._event.set()
        return True

    @property
    def lease_watchdog_timeout_seconds(self) -> float | None:
        return self._lease_watchdog_timeout_seconds

    @property
    def lease_generation(self) -> int:
        return self._lease_generation

    def enable_lease_watchdog(self, *, timeout_seconds: float) -> None:
        if timeout_seconds < 0.1:
            raise ValueError("lease watchdog timeout 不能小于 0.1 秒")
        self._lease_watchdog_timeout_seconds = timeout_seconds

    def renew_lease(self) -> int:
        """只应在 DB lease claim/heartbeat 成功提交后调用。"""

        if self._lease_watchdog_timeout_seconds is None:
            return self._lease_generation
        self._lease_generation += 1
        self._lease_event.set()
        return self._lease_generation

    async def wait_for_lease_renewal(self, after_generation: int) -> int:
        while self._lease_generation <= after_generation:
            self._lease_event.clear()
            if self._lease_generation > after_generation:
                break
            await self._lease_event.wait()
        return self._lease_generation

    async def wait(self) -> None:
        await self._event.wait()

    async def propagate(self) -> None:
        await self.runtime.cancel_all()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise RunCancelledError(self.reason)


__all__ = ["CancellationToken", "RunCancelledError"]
