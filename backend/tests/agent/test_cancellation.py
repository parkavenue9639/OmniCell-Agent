from __future__ import annotations

import asyncio
import threading
from uuid import uuid4

import pytest
from pydantic import BaseModel

from omnicell_agent.agent.cancellation import CancellationToken, RunCancelledError
from omnicell_agent.agent.capability_process import (
    CooperativeInProcessCapabilityInvoker,
)
from omnicell_agent.agent.executor import AsyncCapabilityExecutor
from omnicell_agent.agent.observer import NullAgentObserver
from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.contracts import (
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
)
from omnicell_agent.capabilities.registry import CapabilityContext, CapabilityRegistry
from omnicell_agent.runtime import register_runtime_cancel


class BlockingRequest(CapabilityRequest):
    pass


class BlockingResult(BaseModel):
    ok: bool


class BlockingCapability:
    spec = CapabilitySpec(
        name="blocking_tool",
        kind=CapabilityKind.ATOMIC,
        description="Controlled blocking test tool.",
        prompt_hint="仅在取消传播测试中调用。",
    )
    request_model = BlockingRequest
    result_model = BlockingResult

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()

    def invoke(self, request, context):
        del request, context

        def cancel_active():
            self.cancelled.set()
            self.release.set()

        with register_runtime_cancel(cancel_active):
            self.started.set()
            assert self.release.wait(timeout=5)
        return BlockingResult(ok=True)


class _SlowInvoker:
    async def invoke(
        self,
        name,
        arguments,
        *,
        cancellation,
        on_activity=None,
    ):
        del name, arguments, cancellation, on_activity
        await asyncio.sleep(0.035)
        return BlockingResult(ok=True)


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    async def emit(self, event_type, payload, *, dedupe_key):
        self.events.append((event_type, payload, dedupe_key))


@pytest.mark.asyncio
async def test_cancellation_propagates_into_registered_runtime_callback(tmp_path) -> None:
    conversation_id = uuid4()
    registry = CapabilityRegistry()
    handler = BlockingCapability()
    registry.register(handler)
    token = CancellationToken()
    executor = AsyncCapabilityExecutor(
        CooperativeInProcessCapabilityInvoker(
            registry,
            CapabilityContext(
                conversation_id=conversation_id,
                artifacts=ConversationArtifactStore(conversation_id, tmp_path),
            ),
        ),
        token,
        NullAgentObserver(),
        max_retries=0,
    )

    invocation = asyncio.create_task(
        executor.invoke("blocking_tool", {}, tool_call_id="blocking-1")
    )
    await asyncio.to_thread(handler.started.wait, 2)
    token.cancel("test cancellation")

    with pytest.raises(RunCancelledError):
        await invocation
    assert handler.cancelled.is_set()


@pytest.mark.asyncio
async def test_long_capability_emits_replayable_progress_facts() -> None:
    observer = _RecordingObserver()
    executor = AsyncCapabilityExecutor(
        _SlowInvoker(),
        CancellationToken(),
        observer,
        max_retries=0,
        progress_interval_seconds=0.01,
    )

    result = await executor.invoke(
        "blocking_tool",
        {},
        tool_call_id="progress-1",
    )

    assert result.ok is True
    progress = [
        payload
        for event_type, payload, _ in observer.events
        if event_type == "capability.progress"
    ]
    assert len(progress) >= 2
    assert [item["current"] for item in progress] == list(
        range(1, len(progress) + 1)
    )
    assert all(item["stage"] == "isolated_execution" for item in progress)
