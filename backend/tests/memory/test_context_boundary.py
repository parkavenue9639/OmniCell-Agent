from __future__ import annotations

import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from omnicell_agent.agent.hooks import (
    AgentTurnContext,
    MemoryContextHook,
    MemoryOutputLeakError,
    MemoryTurnResolution,
    ResolvedMemory,
)


IDENTITY = {
    "item_id": "00000000-0000-0000-0000-000000000001",
    "version_id": "00000000-0000-0000-0000-000000000002",
    "version_number": 1,
    "content_sha256": "a" * 64,
    "kind": "profile_fact",
    "source_kind": "explicit",
    "selection_reason": "tool_search",
}
BODY = "用户长期偏好中文说明。"


class _Resolver:
    async def resolve(
        self,
        extra_resources: list[dict[str, object]],
    ) -> MemoryTurnResolution:
        assert extra_resources == [IDENTITY]
        return MemoryTurnResolution(
            memories=(
                ResolvedMemory(
                    content=BODY,
                    dataset_scope={},
                    provenance=(),
                    **IDENTITY,
                ),
            ),
            valid_extra_resources=(IDENTITY,),
        )


@pytest.mark.asyncio
async def test_memory_body_exists_only_in_transient_model_message_view() -> None:
    persisted_state = {
        "loaded_memory_resources": [deepcopy(IDENTITY)],
        "tool_evidence": {},
        "plan_task_statuses": {"step-1": "in_progress"},
    }
    persisted_messages = [
        SystemMessage(content="base policy"),
        HumanMessage(content="当前用户消息"),
    ]
    context = AgentTurnContext(
        state=deepcopy(persisted_state),
        messages=list(persisted_messages),
        model=object(),
    )

    await MemoryContextHook(_Resolver()).pre_invoke(context)

    policy_message = next(
        message
        for message in context.messages
        if isinstance(message, SystemMessage)
        and message.name == "cross_conversation_memory_policy"
    )
    memory_message = next(
        message
        for message in context.messages
        if isinstance(message, HumanMessage)
        and message.name == "cross_conversation_memory_data"
    )
    assert BODY in str(memory_message.content)
    assert BODY not in str(policy_message.content)
    assert context.messages.index(memory_message) < context.messages.index(
        persisted_messages[-1]
    )
    assert context.state == persisted_state
    assert persisted_messages == [
        SystemMessage(content="base policy"),
        HumanMessage(content="当前用户消息"),
    ]
    assert BODY not in json.dumps(
        context.output_updates,
        ensure_ascii=False,
    )
    assert BODY not in json.dumps(
        context.state,
        ensure_ascii=False,
    )
    assert context.output_updates == {}


@pytest.mark.asyncio
async def test_memory_body_cannot_cross_tool_control_plane_boundary() -> None:
    context = AgentTurnContext(
        state={"loaded_memory_resources": [deepcopy(IDENTITY)]},
        messages=[HumanMessage(content="当前用户消息")],
        model=object(),
    )
    hook = MemoryContextHook(_Resolver())
    await hook.pre_invoke(context)
    context.result = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "echo_tool",
                "args": {"text": BODY},
                "id": "leak-memory-body",
                "type": "tool_call",
            }
        ],
    )

    with pytest.raises(MemoryOutputLeakError):
        await hook.post_invoke(context)


@pytest.mark.asyncio
async def test_short_memory_fact_may_be_applied_in_user_visible_answer() -> None:
    context = AgentTurnContext(
        state={"loaded_memory_resources": [deepcopy(IDENTITY)]},
        messages=[HumanMessage(content="我偏好哪种语言？")],
        model=object(),
    )
    hook = MemoryContextHook(_Resolver())
    await hook.pre_invoke(context)
    context.result = AIMessage(content=f"你之前明确偏好：{BODY}")

    await hook.post_invoke(context)

    assert context.result.content == f"你之前明确偏好：{BODY}"
