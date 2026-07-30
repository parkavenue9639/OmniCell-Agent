from __future__ import annotations

from uuid import uuid4

import pytest

from omnicell_agent.agent.memory import (
    AgentMemoryControlError,
    AgentMemoryControlFatalError,
)
from omnicell_agent.memory.agent_adapter import RunBoundMemoryControlAdapter
from omnicell_agent.memory.errors import (
    MemoryAttemptFenceError,
    MemoryContentRejectedError,
    MemoryProposalLimitError,
)


class FenceLosingMemoryService:
    async def search_for_run(self, **kwargs):
        del kwargs
        raise MemoryAttemptFenceError()

    async def propose_from_run(self, **kwargs):
        del kwargs
        raise MemoryAttemptFenceError()

    async def request_forget_from_run(self, **kwargs):
        del kwargs
        raise MemoryAttemptFenceError()


class ProposalRejectingMemoryService:
    def __init__(self, error) -> None:
        self._error = error

    async def propose_from_run(self, **kwargs):
        del kwargs
        raise self._error


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["search", "propose", "forget"])
async def test_attempt_fence_loss_is_fatal_for_every_memory_control_tool(
    operation: str,
) -> None:
    adapter = RunBoundMemoryControlAdapter(
        FenceLosingMemoryService(),  # type: ignore[arg-type]
        run_id=uuid4(),
        worker_id="stale-owner",
        expected_attempt=3,
    )

    with pytest.raises(AgentMemoryControlFatalError) as captured:
        if operation == "search":
            await adapter.search(
                kinds=("project_context",),
                limit=1,
                tool_call_id="memory-search",
            )
        elif operation == "propose":
            await adapter.propose(
                kind="project_context",
                source_message_id=uuid4(),
                tool_call_id="memory-propose",
            )
        else:
            await adapter.request_forget(
                item_id=uuid4(),
                version_id=uuid4(),
                tool_call_id="memory-forget",
            )

    assert captured.value.error_code == "memory_attempt_fence_lost"
    assert captured.value.retryable is False
    assert "当前 owner" in captured.value.recovery_hint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_code", "recovery_fragment"),
    [
        (
            MemoryProposalLimitError(),
            "memory_proposal_limit_reached",
            "本 Run 不再创建候选",
        ),
        (
            MemoryContentRejectedError(),
            "memory_content_rejected",
            "不要改写、拆分",
        ),
    ],
)
async def test_proposal_failures_expose_the_real_non_retryable_recovery(
    error,
    error_code: str,
    recovery_fragment: str,
) -> None:
    adapter = RunBoundMemoryControlAdapter(
        ProposalRejectingMemoryService(error),  # type: ignore[arg-type]
        run_id=uuid4(),
        worker_id="owner",
        expected_attempt=1,
    )

    with pytest.raises(AgentMemoryControlError) as captured:
        await adapter.propose(
            kind="project_context",
            source_message_id=uuid4(),
            tool_call_id="memory-propose",
        )

    assert captured.value.error_code == error_code
    assert captured.value.retryable is False
    assert recovery_fragment in captured.value.recovery_hint
