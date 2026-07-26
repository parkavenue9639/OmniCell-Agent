from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict

from omnicell_agent.capabilities.contracts import ArtifactRef
from omnicell_agent.agent.cancellation import CancellationToken
from omnicell_agent.agent.loop import (
    AgentExecution,
    AgentLoopConfig,
    AgentOutcomeStatus,
)
from omnicell_agent.agent.observer import NullAgentObserver
from omnicell_agent.agent.tooling import (
    AgentToolDefinition,
    AgentToolInvocation,
    AgentToolRegistry,
    AgentToolRegistryError,
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class _ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef


async def _complete(invocation: AgentToolInvocation) -> dict[str, Any]:
    value = _Input.model_validate(invocation.arguments).value
    return {
        "messages": [
            ToolMessage(
                content=f"computed:{value * 2}",
                tool_call_id=invocation.tool_call_id,
            )
        ],
        "task_status": "completed",
        "outcome_status": "completed",
        "final_response": str(value * 2),
    }


def _definition() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="double_value",
        description="Double an integer.",
        prompt_hint="Call only when an integer must be doubled.",
        input_model=_Input,
    )


def test_tool_registry_has_instance_owned_schema_and_prompt_inventory() -> None:
    first = AgentToolRegistry()
    second = AgentToolRegistry()
    first.register(_definition(), _complete)

    assert second.definitions == ()
    assert first.model_definitions()[0]["function"]["name"] == "double_value"
    assert first.model_definitions()[0]["function"]["parameters"]["required"] == [
        "value"
    ]
    assert "Call only when an integer must be doubled." in first.prompt_inventory()
    with pytest.raises(AgentToolRegistryError, match="已注册"):
        first.register(_definition(), _complete)


def test_model_schema_exposes_stable_artifact_handle_only() -> None:
    definition = AgentToolDefinition(
        name="consume_artifact",
        description="Consume one registered artifact.",
        prompt_hint="Call with the artifact_id returned by the prior Tool.",
        input_model=_ArtifactInput,
    )

    schema = definition.model_definition()["function"]["parameters"]
    artifact_schema = schema["$defs"]["ArtifactRef"]

    assert artifact_schema["title"] == "ArtifactHandle"
    assert artifact_schema["required"] == ["artifact_id"]
    assert set(artifact_schema["properties"]) == {"artifact_id"}
    assert "uri" not in json.dumps(schema)


@pytest.mark.asyncio
async def test_tool_registry_rejects_unknown_tool() -> None:
    registry = AgentToolRegistry()

    with pytest.raises(AgentToolRegistryError, match="未知"):
        await registry.invoke(
            AgentToolInvocation(
                name="missing",
                arguments={},
                tool_call_id="missing-1",
                state={},
            )
        )


def test_tool_definition_requires_behavior_hint() -> None:
    with pytest.raises(ValueError, match="prompt_hint"):
        AgentToolDefinition(
            name="bad_tool",
            description="Bad.",
            prompt_hint="",
            input_model=_Input,
        )


class _GenericModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tools: list[dict[str, Any]] = []

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    async def ainvoke(self, messages):
        del messages
        self.calls += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "double_value",
                    "args": {"value": 21},
                    "id": "double-1",
                    "type": "tool_call",
                }
            ],
        )


class _SingleResponseModel:
    def __init__(self, response: AIMessage) -> None:
        self.response = response

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        return self.response


async def _raise_unexpected(invocation: AgentToolInvocation) -> dict[str, Any]:
    del invocation
    raise RuntimeError("private unexpected failure")


@pytest.mark.asyncio
async def test_generic_agent_execution_runs_non_domain_tool_without_factory() -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), _complete)
    model = _GenericModel()
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=model,
        tools=registry,
        system_prompt="Solve the injected bounded task.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(),
    )

    outcome = await execution.start("double 21")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "42"
    assert outcome.tool_calls == 1
    assert {tool["function"]["name"] for tool in model.tools} == {
        "double_value"
    }


def test_required_skill_is_unlocked_only_by_body_resource() -> None:
    registry = AgentToolRegistry()
    definition = AgentToolDefinition(
        name="double_value",
        description="Double an integer.",
        prompt_hint="Load test-skill before using the composite method.",
        input_model=_Input,
        required_skills=("test-skill",),
    )
    registry.register(definition, _complete)

    reference = {
        "skill_name": "test-skill",
        "skill_version": "1.0",
        "resource_kind": "reference",
        "resource_name": "rules",
        "resource_sha256": "a" * 64,
    }
    body = {
        "skill_name": "test-skill",
        "skill_version": "1.0",
        "resource_kind": "body",
        "resource_name": None,
        "resource_sha256": "b" * 64,
    }

    assert registry.visible_definitions([reference]) == ()
    assert registry.visible_definitions([body]) == (definition,)


def test_required_skill_view_uses_injected_body_identity_validator() -> None:
    registry = AgentToolRegistry(
        skill_body_validator=lambda skill_name, resource: (
            resource.get("skill_name") == skill_name
            and resource.get("skill_version") == "2.0"
            and resource.get("resource_sha256") == "c" * 64
        )
    )
    definition = AgentToolDefinition(
        name="double_value",
        description="Double an integer.",
        prompt_hint="Load the current test-skill body before using this method.",
        input_model=_Input,
        required_skills=("test-skill",),
    )
    registry.register(definition, _complete)
    stale_body = {
        "skill_name": "test-skill",
        "skill_version": "1.0",
        "resource_kind": "body",
        "resource_name": None,
        "resource_sha256": "b" * 64,
    }
    current_body = {
        **stale_body,
        "skill_version": "2.0",
        "resource_sha256": "c" * 64,
    }

    assert registry.visible_definitions([stale_body]) == ()
    assert registry.visible_definitions([current_body]) == (definition,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_calls", "handler", "expected_error"),
    [
        (
            [
                {
                    "name": "missing_tool",
                    "args": {},
                    "id": "missing-1",
                    "type": "tool_call",
                }
            ],
            _complete,
            "tool_unavailable",
        ),
        (
            [
                {
                    "name": "double_value",
                    "args": {},
                    "id": "invalid-1",
                    "type": "tool_call",
                }
            ],
            _complete,
            "tool_arguments_invalid",
        ),
        (
            [
                {
                    "name": "double_value",
                    "args": {"value": 1},
                    "id": "internal-1",
                    "type": "tool_call",
                }
            ],
            _raise_unexpected,
            "tool_internal_error",
        ),
        (
            [
                {
                    "name": "double_value",
                    "args": {"value": 1},
                    "id": "multi-1",
                    "type": "tool_call",
                },
                {
                    "name": "double_value",
                    "args": {"value": 2},
                    "id": "multi-2",
                    "type": "tool_call",
                },
            ],
            _complete,
            "multiple_tool_calls",
        ),
    ],
)
async def test_generic_tool_failures_use_structured_outcome(
    tool_calls,
    handler,
    expected_error,
) -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), handler)
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=_SingleResponseModel(
            AIMessage(content="", tool_calls=tool_calls)
        ),
        tools=registry,
        system_prompt="Exercise the bounded failure route.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(max_turns=1),
    )

    await execution.start("exercise one Tool failure")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    outcomes = [
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert outcomes
    assert {outcome["error_code"] for outcome in outcomes} == {
        expected_error
    }
    assert all(
        set(outcome) >= {
            "status",
            "capability",
            "summary",
            "error_code",
            "retryable",
            "recovery_hint",
        }
        and outcome["status"] == "failed"
        for outcome in outcomes
    )


@pytest.mark.asyncio
async def test_excess_tool_calls_are_bounded_and_protocol_complete() -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), _complete)
    raw_calls = [
        {
            "name": "double_value",
            "args": {"value": index},
            "id": f"excess-{index}",
            "type": "tool_call",
        }
        for index in range(9)
    ]
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=_SingleResponseModel(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["args"]),
                            },
                        }
                        for call in raw_calls
                    ]
                },
                tool_calls=raw_calls,
            )
        ),
        tools=registry,
        system_prompt="Exercise bounded multi-Tool normalization.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(max_turns=1),
    )

    outcome = await execution.start("return too many Tool calls")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    ai_message = next(
        message
        for message in snapshot.values["messages"]
        if isinstance(message, AIMessage)
    )
    tool_messages = [
        message
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    ]
    persisted_ids = {
        str(call["id"]) for call in ai_message.tool_calls
    }
    response_ids = {
        str(message.tool_call_id) for message in tool_messages
    }

    assert len(ai_message.tool_calls) == 8
    assert ai_message.additional_kwargs.get("tool_calls") is None
    assert ai_message.invalid_tool_calls == []
    assert len(tool_messages) == 8
    assert persisted_ids == response_ids
    assert len(response_ids) == len(tool_messages)
    assert outcome.tool_calls == 8
    assert all(
        json.loads(str(message.content))["error_code"]
        == "multiple_tool_calls"
        for message in tool_messages
    )


@pytest.mark.asyncio
async def test_multi_tool_rejection_respects_remaining_tool_budget() -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), _complete)
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=_SingleResponseModel(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "double_value",
                        "args": {"value": index},
                        "id": f"budgeted-{index}",
                        "type": "tool_call",
                    }
                    for index in range(9)
                ],
            )
        ),
        tools=registry,
        system_prompt="Exercise a bounded multi-Tool rejection budget.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(max_turns=2, max_tool_calls=3),
    )

    outcome = await execution.start("respect the remaining Tool budget")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    ai_message = next(
        message
        for message in snapshot.values["messages"]
        if isinstance(message, AIMessage)
    )
    tool_messages = [
        message
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert outcome.status == AgentOutcomeStatus.BUDGET_EXHAUSTED
    assert outcome.stop_reason == "Agent budget exhausted: tool_calls"
    assert len(ai_message.tool_calls) == 3
    assert len(tool_messages) == 3
    assert {
        str(call["id"]) for call in ai_message.tool_calls
    } == {
        str(message.tool_call_id) for message in tool_messages
    }
    assert outcome.tool_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declared_ids",
    [
        ["duplicate", "duplicate"],
        [None, None],
        ["x" * 256, "x" * 256],
    ],
)
async def test_canonical_tool_call_ids_are_bounded_unique_and_paired(
    declared_ids,
) -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), _complete)
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=_SingleResponseModel(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "double_value",
                        "args": {"value": index},
                        "id": call_id,
                        "type": "tool_call",
                    }
                    for index, call_id in enumerate(declared_ids)
                ],
            )
        ),
        tools=registry,
        system_prompt="Normalize canonical Tool call identities.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(max_turns=1),
    )

    outcome = await execution.start("normalize Tool call IDs")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    ai_message = next(
        message
        for message in snapshot.values["messages"]
        if isinstance(message, AIMessage)
    )
    tool_messages = [
        message
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    ]
    persisted_ids = [str(call["id"]) for call in ai_message.tool_calls]
    response_ids = [str(message.tool_call_id) for message in tool_messages]

    assert len(persisted_ids) == 2
    assert len(set(persisted_ids)) == 2
    assert all(0 < len(call_id) <= 255 for call_id in persisted_ids)
    assert sorted(persisted_ids) == sorted(response_ids)
    assert len(response_ids) == len(set(response_ids))
    assert all(
        json.loads(str(message.content))["error_code"]
        == "multiple_tool_calls"
        for message in tool_messages
    )
    assert outcome.tool_calls == 2


@pytest.mark.asyncio
async def test_hidden_raw_tool_call_is_removed_and_rejects_canonical_call() -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), _complete)
    canonical = {
        "name": "double_value",
        "args": {"value": 1},
        "id": "parsed-1",
        "type": "tool_call",
    }
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=_SingleResponseModel(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "parsed-1",
                            "type": "function",
                            "function": {
                                "name": "double_value",
                                "arguments": '{"value": 1}',
                            },
                        },
                        {
                            "id": "hidden-2",
                            "type": "function",
                            "function": {
                                "name": "double_value",
                                "arguments": '{"value": 2}',
                            },
                        },
                    ]
                },
                tool_calls=[canonical],
            )
        ),
        tools=registry,
        system_prompt="Remove non-canonical raw Tool calls.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(max_turns=1),
    )

    outcome = await execution.start("reject hidden Tool calls")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    ai_message = next(
        message
        for message in snapshot.values["messages"]
        if isinstance(message, AIMessage)
    )
    tool_messages = [
        message
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert [call["id"] for call in ai_message.tool_calls] == ["parsed-1"]
    assert ai_message.additional_kwargs.get("tool_calls") is None
    assert ai_message.invalid_tool_calls == []
    assert [message.tool_call_id for message in tool_messages] == ["parsed-1"]
    assert (
        json.loads(str(tool_messages[0].content))["error_code"]
        == "multiple_tool_calls"
    )
    assert outcome.tool_calls == 1


@pytest.mark.asyncio
async def test_invalid_tool_calls_are_not_persisted_or_replayed() -> None:
    registry = AgentToolRegistry()
    registry.register(_definition(), _complete)
    execution = AgentExecution(
        run_id=uuid4(),
        conversation_id=uuid4(),
        model=_SingleResponseModel(
            AIMessage(
                content="",
                invalid_tool_calls=[
                    {
                        "name": "double_value",
                        "args": "{not-json",
                        "id": "invalid-1",
                        "error": "invalid JSON",
                        "type": "invalid_tool_call",
                    }
                ],
            )
        ),
        tools=registry,
        system_prompt="Remove invalid Tool calls before persistence.",
        context_messages=(),
        checkpointer=InMemorySaver(),
        cancellation=CancellationToken(),
        observer=NullAgentObserver(),
        config=AgentLoopConfig(max_turns=1),
    )

    outcome = await execution.start("drop invalid Tool calls")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    ai_message = next(
        message
        for message in snapshot.values["messages"]
        if isinstance(message, AIMessage)
    )

    assert ai_message.tool_calls == []
    assert ai_message.additional_kwargs.get("tool_calls") is None
    assert ai_message.invalid_tool_calls == []
    assert not any(
        isinstance(message, ToolMessage)
        for message in snapshot.values["messages"]
    )
    assert outcome.tool_calls == 0
