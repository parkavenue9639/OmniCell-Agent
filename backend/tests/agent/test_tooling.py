from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict

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
