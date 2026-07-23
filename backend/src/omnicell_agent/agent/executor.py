"""Agent capability retry、事件与取消编排。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from omnicell_agent.capabilities.errors import CapabilityExecutionError
from .cancellation import CancellationToken
from .capability_process import CapabilityInvoker, RuntimeCleanupError
from .observer import AgentObserver


logger = logging.getLogger(__name__)
_DEFAULT_PROGRESS_INTERVAL_SECONDS = 5.0


class AsyncCapabilityExecutor:
    def __init__(
        self,
        invoker: CapabilityInvoker,
        cancellation: CancellationToken,
        observer: AgentObserver,
        *,
        max_retries: int,
        progress_interval_seconds: float = _DEFAULT_PROGRESS_INTERVAL_SECONDS,
    ) -> None:
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries 必须在 0..5 之间")
        if progress_interval_seconds <= 0 or progress_interval_seconds > 60:
            raise ValueError("progress_interval_seconds 必须在 (0, 60] 之间")
        self._invoker = invoker
        self._cancellation = cancellation
        self._observer = observer
        self._max_retries = max_retries
        self._progress_interval_seconds = progress_interval_seconds

    async def _invoke_with_progress(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
        attempt: int,
    ) -> BaseModel:
        invocation = asyncio.create_task(
            self._invoker.invoke(
                name,
                arguments,
                cancellation=self._cancellation,
                on_activity=lambda activity: self._emit_runtime_activity(
                    activity,
                    capability=name,
                    tool_call_id=tool_call_id,
                    attempt=attempt,
                ),
            )
        )
        heartbeat = 0
        try:
            while True:
                done, _ = await asyncio.wait(
                    {invocation},
                    timeout=self._progress_interval_seconds,
                )
                if invocation in done:
                    return invocation.result()
                heartbeat += 1
                self._cancellation.raise_if_cancelled()
                await self._observer.emit(
                    "capability.progress",
                    {
                        "capability": name,
                        "tool_call_id": tool_call_id,
                        "attempt": attempt,
                        "stage": "isolated_execution",
                        "current": heartbeat,
                        "message": "能力仍在隔离执行环境中运行",
                    },
                    dedupe_key=(
                        f"capability:{tool_call_id}:attempt:{attempt}:"
                        f"progress:{heartbeat}"
                    ),
                )
        except BaseException:
            if not invocation.done():
                invocation.cancel()
            try:
                await invocation
            except BaseException:
                pass
            raise

    async def _emit_runtime_activity(
        self,
        activity: Mapping[str, Any],
        *,
        capability: str,
        tool_call_id: str,
        attempt: int,
    ) -> None:
        event_type = str(activity.get("kind") or "")
        command_id = str(activity.get("command_id") or "")
        payload = {
            key: value
            for key, value in activity.items()
            if key not in {"kind", "capability", "tool_call_id", "attempt"}
        }
        payload.update(
            {
                "capability": capability,
                "tool_call_id": tool_call_id,
                "attempt": attempt,
            }
        )
        if event_type == "runtime.output":
            suffix = (
                f"{activity.get('stream')}:{activity.get('index')}"
            )
        elif event_type == "runtime.command_started":
            suffix = "started"
        elif event_type == "runtime.command_completed":
            suffix = "completed"
        else:
            raise ValueError("未知 runtime activity 类型")
        await self._observer.emit(
            event_type,
            payload,
            dedupe_key=(
                f"capability:{tool_call_id}:attempt:{attempt}:"
                f"runtime:{command_id}:{suffix}"
            ),
        )

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> BaseModel:
        self._cancellation.raise_if_cancelled()
        await self._observer.emit(
            "capability.started",
            {"capability": name, "tool_call_id": tool_call_id},
            dedupe_key=f"capability:{tool_call_id}:started",
        )
        attempt = 0
        while True:
            self._cancellation.raise_if_cancelled()
            try:
                result = await self._invoke_with_progress(
                    name,
                    arguments,
                    tool_call_id=tool_call_id,
                    attempt=attempt + 1,
                )
            except RuntimeCleanupError:
                raise
            except CapabilityExecutionError as exc:
                if attempt >= self._max_retries:
                    await self._observer.emit(
                        "capability.failed",
                        {
                            "capability": name,
                            "tool_call_id": tool_call_id,
                            "error": str(exc)[:1_000],
                            "retryable": True,
                            "attempt": attempt + 1,
                        },
                        dedupe_key=f"capability:{tool_call_id}:failed",
                    )
                    raise
                attempt += 1
                await self._observer.emit(
                    "capability.retrying",
                    {
                        "capability": name,
                        "tool_call_id": tool_call_id,
                        "attempt": attempt + 1,
                    },
                    dedupe_key=f"capability:{tool_call_id}:retry:{attempt}",
                )
                continue
            except Exception as exc:
                await self._observer.emit(
                    "capability.failed",
                    {
                        "capability": name,
                        "tool_call_id": tool_call_id,
                        "error": str(exc)[:1_000],
                        "retryable": False,
                        "attempt": attempt + 1,
                    },
                    dedupe_key=f"capability:{tool_call_id}:failed",
                )
                raise
            self._cancellation.raise_if_cancelled()
            await self._observer.emit(
                "capability.completed",
                {
                    "capability": name,
                    "tool_call_id": tool_call_id,
                    "result": result.model_dump(mode="json"),
                    "attempt": attempt + 1,
                },
                dedupe_key=f"capability:{tool_call_id}:completed",
            )
            return result


__all__ = ["AsyncCapabilityExecutor"]
