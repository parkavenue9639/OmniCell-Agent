from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from typing import Any, Literal
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field

from omnicell_agent.agent import (
    AgentLoopConfig,
    AgentLoopFactory,
    AgentOutcomeStatus,
    DefaultToolPolicy,
    CooperativeInProcessCapabilityInvoker,
)
from omnicell_agent.agent.observer import AgentObserver
from omnicell_agent.agent.resource_boundary import (
    RUNTIME_CONTROL_ROOT,
    contains_internal_resource_locator,
)
from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer
from omnicell_agent.capabilities.catalog import SkillCatalog, SkillDefinition
from omnicell_agent.capabilities.contracts import (
    ArtifactRef,
    CapabilityEffect,
    CapabilityMode,
    CapabilityRequest,
    CapabilitySpec,
    CapabilityStatus,
)
from omnicell_agent.capabilities.errors import CapabilityExecutionError
from omnicell_agent.capabilities.registry import CapabilityContext, CapabilityRegistry
from omnicell_agent.runs.status import ReviewDecision


class EchoRequest(CapabilityRequest):
    text: str = Field(min_length=1, max_length=100)


class EchoResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class EchoCapability:
    spec = CapabilitySpec(
        name="echo_tool",
        mode=CapabilityMode.ATOMIC,
        effect=CapabilityEffect.CUSTOM,
        description="Return a controlled echo.",
        prompt_hint="Call only when the user requests an echo.",
    )
    request_model = EchoRequest
    result_model = EchoResult

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: CapabilityRequest, context: CapabilityContext) -> EchoResult:
        del context
        self.calls += 1
        return EchoResult(text=EchoRequest.model_validate(request).text)


class ProduceArtifactRequest(CapabilityRequest):
    content: str = Field(min_length=1, max_length=100)


class ProduceArtifactResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef


class ProduceArtifactCapability:
    spec = CapabilitySpec(
        name="produce_artifact",
        mode=CapabilityMode.ATOMIC,
        effect=CapabilityEffect.TRANSFORM,
        description="Produce one controlled text artifact.",
        prompt_hint="Call when a controlled artifact is required.",
        produces=("controlled_text",),
    )
    request_model = ProduceArtifactRequest
    result_model = ProduceArtifactResult

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> ProduceArtifactResult:
        typed = ProduceArtifactRequest.model_validate(request)
        return ProduceArtifactResult(
            artifact=context.artifacts.write_text(
                "artifacts/controlled.txt",
                typed.content,
                kind="controlled_text",
                media_type="text/plain",
            )
        )


class ConsumeArtifactRequest(CapabilityRequest):
    artifact: ArtifactRef


class ConsumeArtifactResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class ConsumeArtifactCapability:
    spec = CapabilitySpec(
        name="consume_artifact",
        mode=CapabilityMode.INSPECT,
        effect=CapabilityEffect.INSPECT,
        description="Read one controlled text artifact.",
        prompt_hint="Call with the artifact_id returned by produce_artifact.",
        consumes=("controlled_text",),
    )
    request_model = ConsumeArtifactRequest
    result_model = ConsumeArtifactResult

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> ConsumeArtifactResult:
        typed = ConsumeArtifactRequest.model_validate(request)
        with context.artifacts.open_verified(
            typed.artifact,
            expected_kind="controlled_text",
        ) as handle:
            return ConsumeArtifactResult(content=handle.read().decode("utf-8"))


class FlakyEchoCapability(EchoCapability):
    def invoke(self, request: CapabilityRequest, context: CapabilityContext) -> EchoResult:
        del context
        self.calls += 1
        if self.calls == 1:
            raise CapabilityExecutionError("transient controlled failure")
        return EchoResult(text=EchoRequest.model_validate(request).text)


class AlwaysFailEchoCapability(EchoCapability):
    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> EchoResult:
        del request, context
        self.calls += 1
        raise CapabilityExecutionError("controlled persistent failure")


class InspectEchoCapability(EchoCapability):
    spec = CapabilitySpec(
        name="inspect_echo",
        mode=CapabilityMode.INSPECT,
        effect=CapabilityEffect.INSPECT,
        description="Inspect a controlled value without changing it.",
        prompt_hint="Call only when the controlled value must be inspected.",
    )


class EchoCompositeCapability(EchoCapability):
    spec = CapabilitySpec(
        name="echo_composite",
        mode=CapabilityMode.COMPOSITE,
        effect=CapabilityEffect.CUSTOM,
        description="Run the controlled composite echo operation.",
        prompt_hint="Load test-skill before calling for a composite goal.",
        recommended_skills=("test-skill",),
        required_skills=("test-skill",),
    )


class AbortedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[CapabilityStatus.ABORTED] = CapabilityStatus.ABORTED
    diagnostic_summary: str


class AbortedCapability(EchoCapability):
    spec = CapabilitySpec(
        name="aborted_tool",
        mode=CapabilityMode.COMPOSITE,
        effect=CapabilityEffect.CUSTOM,
        description="Return a controlled non-completed scientific outcome.",
        prompt_hint="Only used to test aborted outcome handling.",
    )
    result_model = AbortedResult

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> AbortedResult:
        del request, context
        self.calls += 1
        return AbortedResult(diagnostic_summary="controlled abort")


class ScriptedModel:
    def __init__(self, responses: list[AIMessage | Exception]) -> None:
        self.responses = deque(responses)
        self.tool_definitions: list[dict[str, Any]] = []
        self.tool_definition_snapshots: list[set[str]] = []
        self.calls = 0

    def bind_tools(self, tools):
        self.tool_definitions = list(tools)
        self.tool_definition_snapshots.append(
            {
                tool["function"]["name"]
                for tool in self.tool_definitions
            }
        )
        return self

    async def ainvoke(self, messages):
        del messages
        self.calls += 1
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


class NeverReturningModel:
    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        await asyncio.Event().wait()


class ContextRecordingFinishModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.artifact_contexts: list[str] = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.artifact_contexts = [
            str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
            and "输入 artifact 句柄与有界描述" in str(message.content)
        ]
        return _finish(self.response)


class SkillContextRecordingFinishModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.loaded_skill_contexts: list[str] = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.loaded_skill_contexts = [
            str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
            and message.name == "loaded_skill_context"
        ]
        return _finish(self.response)


class ProviderIdentifiedFinishModel:
    def __init__(self, response: str, tool_call_id: str) -> None:
        self.response = response
        self.tool_call_id = tool_call_id

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        return AIMessage(
            id="provider-fixed-id",
            content="",
            tool_calls=[
                {
                    "name": "finish_task",
                    "args": {"final_response": self.response},
                    "id": self.tool_call_id,
                    "type": "tool_call",
                }
            ],
        )


class AbortedOutcomeRecordingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_outcome: dict[str, Any] | None = None

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "aborted_tool",
                        "args": {"text": "abort"},
                        "id": "aborted-call",
                        "type": "tool_call",
                    }
                ],
            )
        tool_message = next(
            message
            for message in reversed(messages)
            if isinstance(message, ToolMessage)
        )
        self.tool_outcome = json.loads(str(tool_message.content))
        return _finish("已说明未完成限制")


class UnknownReplacementModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        self.calls += 1
        capability = "echo_tool" if self.calls == 1 else "missing_capability"
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_task_plan",
                    "args": {
                        "rationale": "验证计划替换的原子性",
                        "steps": [
                            {
                                "title": "步骤一",
                                "objective": "完成第一项受控检查",
                                "success_criteria": "Tool 返回有效结果",
                                "capability_hint": capability,
                            },
                            {
                                "title": "步骤二",
                                "objective": "完成第二项受控检查",
                                "success_criteria": "Tool 返回有效结果",
                                "depends_on": [1],
                                "capability_hint": capability,
                            },
                        ],
                    },
                    "id": f"plan-{self.calls}",
                    "type": "tool_call",
                }
            ],
        )


class PlanningModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_definitions: list[dict[str, Any]] = []
        self.task_ids: list[str] = []

    def bind_tools(self, tools):
        self.tool_definitions = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_task_plan",
                        "args": {
                            "rationale": "目标包含两个可分别验证的步骤",
                            "steps": [
                                {
                                    "title": "检查输入",
                                    "objective": "回显第一步受控输入",
                                    "success_criteria": "echo_tool 返回有效结果",
                                    "capability_hint": "echo_tool",
                                },
                                {
                                    "title": "汇总结果",
                                    "objective": "回显第二步受控输入",
                                    "success_criteria": "第二次 echo_tool 返回有效结果",
                                    "depends_on": [1],
                                    "capability_hint": "echo_tool",
                                },
                            ],
                        },
                        "id": "plan-create",
                        "type": "tool_call",
                    }
                ],
            )
        if not self.task_ids:
            latest_tool = next(
                message for message in reversed(messages) if message.type == "tool"
            )
            self.task_ids = [
                step["task_id"]
                for step in json.loads(str(latest_tool.content))["result"]["steps"]
            ]
        if self.calls == 2:
            return _echo("plan-step-1")
        if self.calls == 3:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_task_plan",
                        "args": {
                            "task_id": self.task_ids[0],
                            "status": "completed",
                            "summary": "步骤 1 已验证",
                            "evidence_tool_call_ids": ["echo-plan-step-1"],
                        },
                        "id": "plan-update-1",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 4:
            return _echo("plan-step-2")
        if self.calls == 5:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_task_plan",
                        "args": {
                            "task_id": self.task_ids[1],
                            "status": "completed",
                            "summary": "步骤 2 已验证",
                            "evidence_tool_call_ids": ["echo-plan-step-2"],
                        },
                        "id": "plan-update-2",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="复合目标已完成")


class AutoReconciledPlanningModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_outcomes: list[dict[str, Any]] = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        latest_tool = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, ToolMessage)
            ),
            None,
        )
        if latest_tool is not None:
            self.tool_outcomes.append(json.loads(str(latest_tool.content)))
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_task_plan",
                        "args": {
                            "rationale": "两个按顺序执行的可验证步骤",
                            "steps": [
                                {
                                    "title": "第一步",
                                    "objective": "完成第一次回显",
                                    "success_criteria": "echo_tool 成功",
                                    "capability_hint": "echo_tool",
                                },
                                {
                                    "title": "第二步",
                                    "objective": "完成第二次回显",
                                    "success_criteria": "echo_tool 再次成功",
                                    "depends_on": [1],
                                    "capability_hint": "echo_tool",
                                },
                            ],
                        },
                        "id": "auto-plan-create",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 2:
            return _echo("auto-step-1")
        if self.calls == 3:
            return _echo("auto-step-2")
        return AIMessage(content="自动对账后的复合目标已完成")


class ReplayedEvidencePlanningModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_outcomes: list[dict[str, Any]] = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        latest_tool = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, ToolMessage)
            ),
            None,
        )
        if latest_tool is not None:
            self.tool_outcomes.append(json.loads(str(latest_tool.content)))
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_task_plan",
                        "args": {
                            "rationale": "验证同一证据只能消费一次",
                            "steps": [
                                {
                                    "title": "第一步",
                                    "objective": "完成第一次回显",
                                    "success_criteria": "echo_tool 成功",
                                    "capability_hint": "echo_tool",
                                },
                                {
                                    "title": "第二步",
                                    "objective": "完成第二次回显",
                                    "success_criteria": "echo_tool 独立成功",
                                    "depends_on": [1],
                                    "capability_hint": "echo_tool",
                                },
                            ],
                        },
                        "id": "replay-plan-create",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls in {2, 3}:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"text": "first evidence"},
                        "id": "replayed-evidence",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 4:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"text": "conflicting evidence"},
                        "id": "replayed-evidence",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 5:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"text": "second evidence"},
                        "id": "fresh-evidence",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="幂等证据计划已完成")


class ArtifactHandleChainingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.outcomes: list[dict[str, Any]] = []
        self.tool_definitions: list[dict[str, Any]] = []

    def bind_tools(self, tools):
        self.tool_definitions = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        latest_tool = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, ToolMessage)
            ),
            None,
        )
        if latest_tool is not None:
            self.outcomes.append(json.loads(str(latest_tool.content)))
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "produce_artifact",
                        "args": {"content": "controlled content"},
                        "id": "produce-handle",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 2:
            artifact_id = self.outcomes[-1]["result"]["artifact"]["artifact_id"]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "consume_artifact",
                        "args": {"artifact": {"artifact_id": artifact_id}},
                        "id": "consume-handle",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="句柄串联完成")


class SkillLoadingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.loaded_contents: list[str] = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.loaded_contents.extend(
            str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
            and "Use echo_tool only when an echo is required."
            in str(message.content)
        )
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "test-skill"},
                        "id": "load-test-skill",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="skill loaded")


class CompositeRoutingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.task_ids: list[str] = []

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_task_plan",
                        "args": {
                            "rationale": "先读取事实，再执行原子变换",
                            "steps": [
                                {
                                    "title": "读取受控值",
                                    "objective": "检查受控值",
                                    "success_criteria": "inspect_echo 返回结果",
                                    "capability_hint": "inspect_echo",
                                },
                                {
                                    "title": "执行受控原子 Tool",
                                    "objective": "执行受控变换",
                                    "success_criteria": "echo_tool 返回结果",
                                    "depends_on": [1],
                                    "capability_hint": "echo_tool",
                                },
                            ],
                        },
                        "id": "composite-plan",
                        "type": "tool_call",
                    }
                ],
            )
        if not self.task_ids:
            plan_message = next(
                message
                for message in reversed(messages)
                if message.type == "tool"
                and str(message.content).startswith("{")
            )
            self.task_ids = [
                step["task_id"]
                for step in json.loads(str(plan_message.content))["result"]["steps"]
            ]
        if self.calls == 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "inspect_echo",
                        "args": {"text": "inspect"},
                        "id": "inspect-composite",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 3:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_task_plan",
                        "args": {
                            "task_id": self.task_ids[0],
                            "status": "completed",
                            "summary": "只读检查完成",
                            "evidence_tool_call_ids": ["inspect-composite"],
                        },
                        "id": "complete-inspect",
                        "type": "tool_call",
                    }
                ],
            )
        if self.calls == 4:
            return _echo("atomic")
        if self.calls == 5:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_task_plan",
                        "args": {
                            "task_id": self.task_ids[1],
                            "status": "completed",
                            "summary": "原子操作完成",
                            "evidence_tool_call_ids": ["echo-atomic"],
                        },
                        "id": "complete-atomic",
                        "type": "tool_call",
                    }
                ],
            )
        return _finish("组合目标已完成")


class RecordingObserver(AgentObserver):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str]] = []

    async def emit(self, event_type, payload, *, dedupe_key):
        self.events.append((event_type, payload, dedupe_key))


def _finish(text: str = "done") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "finish_task",
                "args": {"final_response": text},
                "id": f"finish-{text}",
                "type": "tool_call",
            }
        ],
    )


def _echo(
    text: str = "hello",
    *,
    call_id: str | None = None,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "echo_tool",
                "args": {"text": text},
                "id": call_id or f"echo-{text}",
                "type": "tool_call",
            }
        ],
    )


def _layer(handler: EchoCapability) -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(handler)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled test skill.",
            tools=("echo_tool",),
            content="Use echo_tool only when an echo is required.",
        )
    )
    return DomainCapabilityLayer(registry=registry, skills=skills)


def _execution(
    tmp_path,
    model,
    *,
    policy=None,
    config=None,
    observer=None,
    handler=None,
):
    conversation_id = uuid4()
    handler = handler or EchoCapability()
    layer = _layer(handler)
    factory = AgentLoopFactory(
        layer,
        model_factory=lambda: model,
        policy=policy,
        config=config,
        capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
    )
    context = CapabilityContext(
        conversation_id=conversation_id,
        artifacts=ConversationArtifactStore(conversation_id, tmp_path / str(conversation_id)),
    )
    execution = factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        checkpointer=InMemorySaver(),
        observer=observer,
    )
    return execution, handler, layer


def _execution_for_layer(tmp_path, model, layer, *, observer=None):
    conversation_id = uuid4()
    factory = AgentLoopFactory(
        layer,
        model_factory=lambda: model,
        capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
    )
    context = CapabilityContext(
        conversation_id=conversation_id,
        artifacts=ConversationArtifactStore(
            conversation_id,
            tmp_path / str(conversation_id),
        ),
    )
    return factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        checkpointer=InMemorySaver(),
        observer=observer,
    )


async def _seed_pending_tool_call(
    execution,
    loaded_skill_resources,
    tool_call,
    *,
    state_updates: dict[str, Any] | None = None,
) -> None:
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[tool_call],
            )
        ],
        "run_id": str(execution.run_id),
        "task_status": "in_progress",
        "turn_count": 1,
        "model_calls": 1,
        "tool_calls": 0,
        "consecutive_no_tool": 0,
        "started_at_epoch": execution._clock(),  # noqa: SLF001
        "outcome_status": "",
        "final_response": "",
        "stop_reason": "",
        "plan_revision": 0,
        "plan_task_ids": [],
        "plan_task_statuses": {},
        "plan_step_definitions": {},
        "plan_step_evidence": {},
        "active_plan_task_id": "",
        "loaded_skill_resources": loaded_skill_resources,
        "tool_failure_counts": {},
        "tool_evidence": {},
        "tool_call_batch_rejected": False,
    }
    state.update(state_updates or {})
    await execution._graph.aupdate_state(  # noqa: SLF001
        execution._graph_config,  # noqa: SLF001
        state,
        as_node="agent",
    )


async def _seed_pending_composite_call(
    execution,
    loaded_skill_resources,
) -> None:
    await _seed_pending_tool_call(
        execution,
        loaded_skill_resources,
        {
            "name": "echo_composite",
            "args": {"text": "must not execute"},
            "id": "stale-resume-call",
            "type": "tool_call",
        },
    )


async def _seed_agent_checkpoint(
    execution,
    loaded_skill_resources,
) -> None:
    await _seed_pending_tool_call(
        execution,
        loaded_skill_resources,
        {
            "name": "finish_task",
            "args": {"final_response": "seed only"},
            "id": "seed-agent-call",
            "type": "tool_call",
        },
    )
    await execution._graph.aupdate_state(  # noqa: SLF001
        execution._graph_config,  # noqa: SLF001
        {
            "messages": [
                ToolMessage(
                    content="seed response",
                    tool_call_id="seed-agent-call",
                )
            ]
        },
        as_node="tools",
    )


@pytest.mark.asyncio
async def test_direct_reply_finishes_without_domain_capability(tmp_path) -> None:
    model = ScriptedModel([AIMessage(content="可以直接回答")])
    observer = RecordingObserver()
    execution, handler, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("解释当前任务状态")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "可以直接回答"
    assert outcome.turn_count == 1
    assert outcome.tool_calls == 0
    assert handler.calls == 0
    assert not any(
        event_type.startswith("capability.")
        for event_type, _, _ in observer.events
    )


@pytest.mark.asyncio
async def test_text_content_blocks_finish_but_blank_blocks_do_not(tmp_path) -> None:
    completed_observer = RecordingObserver()
    completed, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                AIMessage(
                    content=[
                        {"type": "text", "text": "  第一段  "},
                        {"type": "text", "text": "第二段"},
                    ]
                )
            ]
        ),
        observer=completed_observer,
    )
    stalled_observer = RecordingObserver()
    stalled, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                AIMessage(
                    content=[
                        {"type": "text", "text": "   "},
                        {
                            "type": "reasoning",
                            "reasoning": "PRIVATE_REASONING_SENTINEL",
                        },
                    ]
                )
            ]
        ),
        config=AgentLoopConfig(max_empty_reprompts=0),
        observer=stalled_observer,
    )

    completed_outcome = await completed.start("返回分块文本")
    stalled_outcome = await stalled.start("不要把空白块当作答复")

    assert completed_outcome.status == AgentOutcomeStatus.COMPLETED
    assert completed_outcome.final_response == "第一段\n第二段"
    assert stalled_outcome.status == AgentOutcomeStatus.STALLED
    assert stalled_outcome.final_response is None
    assert next(
        payload["content"]
        for event_type, payload, _ in completed_observer.events
        if event_type == "message.completed"
    ) == "第一段\n第二段"
    assert not any(
        event_type == "message.completed"
        for event_type, _, _ in stalled_observer.events
    )
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(
        stalled_observer.events,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_internal_workspace_uri_is_reprompted_before_public_message(
    tmp_path,
) -> None:
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                AIMessage(content="报告在 workspace://internal/report.md"),
                AIMessage(content="报告产物已登记，请在页面中查看。"),
            ]
        ),
        observer=observer,
    )

    outcome = await execution.start("返回安全的报告说明")
    public_messages = [
        payload["content"]
        for event_type, payload, _ in observer.events
        if event_type == "message.completed"
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "报告产物已登记，请在页面中查看。"
    assert public_messages == ["报告产物已登记，请在页面中查看。"]
    assert "workspace://" not in json.dumps(
        observer.events,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_host_path_is_reprompted_before_public_message(
    tmp_path,
) -> None:
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                AIMessage(
                    content=(
                        "报告位于 "
                        "/Users/researcher/OmniCell/workspaces/report.md"
                    )
                ),
                AIMessage(content="报告产物已登记，请在页面中查看。"),
            ]
        ),
        observer=observer,
    )

    outcome = await execution.start("返回不含宿主路径的报告说明")
    public_messages = [
        payload["content"]
        for event_type, payload, _ in observer.events
        if event_type == "message.completed"
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "报告产物已登记，请在页面中查看。"
    assert public_messages == ["报告产物已登记，请在页面中查看。"]
    assert "/Users/researcher" not in json.dumps(
        observer.events,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_tool_bearing_text_is_not_published_before_completion_gate(
    tmp_path,
) -> None:
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                AIMessage(
                    content=(
                        "尚未验证的报告位于 "
                        "workspace://internal/report.md"
                    ),
                    tool_calls=[
                        {
                            "name": "echo_tool",
                            "args": {"text": "verify first"},
                            "id": "verify-before-publish",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="验证完成，结果已经登记。"),
            ]
        ),
        observer=observer,
    )

    outcome = await execution.start("先执行 Tool，再发布最终结果")
    public_messages = [
        payload["content"]
        for event_type, payload, _ in observer.events
        if event_type == "message.completed"
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "验证完成，结果已经登记。"
    assert public_messages == ["验证完成，结果已经登记。"]
    assert "workspace://" not in json.dumps(
        observer.events,
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    "locator",
    [
        "workspace://internal/report.md",
        "file:///tmp/report.md",
        "/Users/researcher/report.md",
        "/root/project/report.md",
        "/etc/omnicell/config.json",
        r"C:\Users\researcher\report.md",
        "C:/Users/researcher/report.md",
        r"\\server\share\report.md",
        "//server/share/report.md",
        f"{RUNTIME_CONTROL_ROOT}/claims/run.json",
        ".omnicell-invocations/invocation/result.json",
        "~/OmniCell/report.md",
    ],
)
def test_internal_resource_locator_gate_covers_private_path_forms(
    locator,
) -> None:
    assert contains_internal_resource_locator(locator)


@pytest.mark.parametrize(
    "public_text",
    [
        "报告产物 artifact_id 为 123e4567-e89b-12d3-a456-426614174000。",
        "请在 https://example.org/reports/summary 中查看公共说明。",
        "T cell / B cell 的 marker 需要分别核对。",
        "CD4/CD8 比值只用于领域解释。",
    ],
)
def test_internal_resource_locator_gate_allows_public_text(
    public_text,
) -> None:
    assert not contains_internal_resource_locator(public_text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "locator",
    [
        "file:///tmp/report.md",
        "/root/project/report.md",
        r"C:\Users\researcher\report.md",
        "C:/Users/researcher/report.md",
        r"\\server\share\report.md",
        f"{RUNTIME_CONTROL_ROOT}/claims/run.json",
    ],
)
async def test_finish_task_rejects_internal_resource_locator(
    tmp_path,
    locator,
) -> None:
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                _finish(f"报告位于 {locator}"),
                AIMessage(content="报告已经登记，请在页面中查看。"),
            ]
        ),
        observer=observer,
    )

    outcome = await execution.start("通过统一门禁发布最终结果")
    public_messages = [
        payload["content"]
        for event_type, payload, _ in observer.events
        if event_type == "message.completed"
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "报告已经登记，请在页面中查看。"
    assert public_messages == ["报告已经登记，请在页面中查看。"]
    assert locator not in json.dumps(observer.events, ensure_ascii=False)


@pytest.mark.asyncio
async def test_skill_body_is_absent_initially_and_loaded_on_demand(
    tmp_path,
) -> None:
    model = SkillLoadingModel()
    observer = RecordingObserver()
    execution, handler, _ = _execution(tmp_path, model, observer=observer)

    assert "Controlled test skill." in execution._system_prompt
    assert (
        "Use echo_tool only when an echo is required."
        not in execution._system_prompt
    )

    outcome = await execution.start("load the detailed method")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "skill loaded"
    assert outcome.tool_calls == 1
    assert handler.calls == 0
    assert len(model.loaded_contents) == 1
    assert model.loaded_contents[0].endswith(
        "Use echo_tool only when an echo is required."
    )
    skill_events = [
        (event_type, payload)
        for event_type, payload, _ in observer.events
        if event_type.startswith("skill.load_")
    ]
    assert [event_type for event_type, _ in skill_events] == [
        "skill.load_started",
        "skill.load_completed",
    ]
    assert skill_events[0][1] == {
        "tool_call_id": "load-test-skill",
        "skill_name": "test-skill",
        "resource_kind": "body",
        "resource_name": None,
        "purpose": "domain_method",
        "skill_version": "1.0",
        "resource_sha256": hashlib.sha256(
            b"Use echo_tool only when an echo is required."
        ).hexdigest(),
    }
    assert skill_events[1][1]["outcome"] == "loaded"
    assert skill_events[1][1]["content_bytes"] == len(
        b"Use echo_tool only when an echo is required."
    )


@pytest.mark.asyncio
async def test_skill_subresource_requires_same_version_body_before_loading(
    tmp_path,
) -> None:
    definition_root = tmp_path / "skill_definitions"
    skill_root = definition_root / "test-skill"
    references_root = skill_root / "references"
    references_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: test-skill",
                "description: Controlled workflow method.",
                "version: 1.0",
                "tools:",
                "  - echo_composite",
                "---",
                "Load the method body before any child resource.",
            ]
        ),
        encoding="utf-8",
    )
    (references_root / "rules.md").write_text(
        "REFERENCE_MUST_NOT_UNLOCK",
        encoding="utf-8",
    )
    registry = CapabilityRegistry()
    workflow = EchoCompositeCapability()
    registry.register(workflow)
    skills = SkillCatalog.load_from_directory(definition_root)
    observer = RecordingObserver()
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {
                            "skill_name": "test-skill",
                            "reference": "rules",
                            "purpose": "reference_lookup",
                        },
                        "id": "load-reference-before-body",
                        "type": "tool_call",
                    }
                ],
            ),
            _finish("已遵守 Skill 加载顺序"),
        ]
    )
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
        observer=observer,
    )

    outcome = await execution.start("先尝试加载 reference")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    failed = [
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and json.loads(str(message.content)).get("status") == "failed"
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert workflow.calls == 0
    assert snapshot.values["loaded_skill_resources"] == []
    assert all(
        "echo_composite" not in visible
        for visible in model.tool_definition_snapshots
    )
    assert failed[0]["error_code"] == "skill_body_required"
    assert [
        (event_type, payload.get("error_code"))
        for event_type, payload, _ in observer.events
        if event_type.startswith("skill.load_")
    ] == [
        ("skill.load_started", None),
        ("skill.load_failed", "skill_body_required"),
    ]


@pytest.mark.asyncio
async def test_skill_aggregate_limit_fails_before_checkpoint_or_completed_event(
    tmp_path,
) -> None:
    registry = CapabilityRegistry()
    registry.register(EchoCapability())
    skills = SkillCatalog()
    for name, marker in (
        ("large-method-one", "A"),
        ("large-method-two", "B"),
    ):
        skills.register(
            SkillDefinition(
                name=name,
                description=f"Controlled large method {name}.",
                tools=("echo_tool",),
                content=marker * (64 * 1024),
            )
        )
    observer = RecordingObserver()
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "large-method-one"},
                        "id": "load-large-one",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "large-method-two"},
                        "id": "load-large-two",
                        "type": "tool_call",
                    }
                ],
            ),
            _finish("只保留可重建的方法上下文"),
        ]
    )
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
        observer=observer,
    )

    outcome = await execution.start("验证 Skill 聚合上下文上限")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    resources = snapshot.values["loaded_skill_resources"]
    second_outcome = next(
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "load-large-two"
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert [resource["skill_name"] for resource in resources] == [
        "large-method-one"
    ]
    assert second_outcome["error_code"] == "skill_context_limit_exceeded"
    assert [
        event_type
        for event_type, payload, _ in observer.events
        if event_type.startswith("skill.load_")
        and payload["tool_call_id"] == "load-large-two"
    ] == ["skill.load_started", "skill.load_failed"]


@pytest.mark.asyncio
async def test_read_only_route_does_not_load_skill_or_run_composite_capability(
    tmp_path,
) -> None:
    inspection = InspectEchoCapability()
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(inspection)
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled workflow method.",
            tools=("echo_composite",),
            content="WORKFLOW_BODY_SENTINEL",
        )
    )
    execution = _execution_for_layer(
        tmp_path,
        ScriptedModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "inspect_echo",
                            "args": {"text": "read"},
                            "id": "inspect-read",
                            "type": "tool_call",
                        }
                    ],
                ),
                _finish("只读检查完成"),
            ]
        ),
        DomainCapabilityLayer(registry=registry, skills=skills),
    )

    outcome = await execution.start("只读取当前值")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert inspection.calls == 1
    assert workflow.calls == 0
    assert "WORKFLOW_BODY_SENTINEL" not in execution._system_prompt


@pytest.mark.asyncio
async def test_agent_routes_capability_then_finishes_with_natural_text(tmp_path) -> None:
    model = ScriptedModel([_echo(), AIMessage(content="analysis complete")])
    observer = RecordingObserver()
    execution, handler, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("echo and finish")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "analysis complete"
    assert outcome.turn_count == 2
    assert outcome.tool_calls == 1
    assert handler.calls == 1
    assert {event[0] for event in observer.events} >= {
        "agent.turn_started",
        "agent.tool_started",
        "agent.tool_completed",
        "capability.started",
        "capability.completed",
    }
    assert not any(event[0] == "task.updated" for event in observer.events)
    assert {tool["function"]["name"] for tool in model.tool_definitions} == {
        "echo_tool",
        "load_skill",
        "create_task_plan",
        "update_task_plan",
        "finish_task",
    }


@pytest.mark.asyncio
async def test_non_completed_capability_is_not_recorded_as_plan_evidence(
    tmp_path,
) -> None:
    model = AbortedOutcomeRecordingModel()
    handler = AbortedCapability()
    execution, _, _ = _execution(
        tmp_path,
        model,
        handler=handler,
    )

    outcome = await execution.start("执行会受控中止的能力")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert handler.calls == 1
    assert model.tool_outcome == {
        "status": "failed",
        "capability": "aborted_tool",
        "summary": "能力调用已结束，但没有达到可作为完成证据的科学终态。",
        "error_code": "capability_not_completed",
        "retryable": False,
        "recovery_hint": (
            "检查诊断与输入前置条件，改用其他能力或向用户说明限制。"
        ),
    }
    assert snapshot.values["tool_evidence"] == {}


@pytest.mark.asyncio
async def test_composite_route_loads_skill_before_composite_capability(
    tmp_path,
) -> None:
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled workflow method.",
            tools=("echo_composite",),
            content="Load this method before the workflow.",
        )
    )
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "test-skill"},
                        "id": "load-workflow-skill",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_composite",
                        "args": {"text": "workflow"},
                        "id": "run-workflow",
                        "type": "tool_call",
                    }
                ],
            ),
            _finish("完整工作流完成"),
        ]
    )
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )

    outcome = await execution.start("执行完整受控工作流")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert workflow.calls == 1
    assert outcome.tool_calls == 3
    assert "echo_composite" not in model.tool_definition_snapshots[0]
    assert "echo_composite" in model.tool_definition_snapshots[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_field", "stale_value"),
    [
        ("skill_version", "0.9"),
        ("resource_sha256", "0" * 64),
    ],
)
async def test_resume_to_tools_revalidates_required_skill_body_identity(
    tmp_path,
    identity_field,
    stale_value,
) -> None:
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled workflow method.",
            tools=("echo_composite",),
            content="Current workflow method body.",
        )
    )
    identity, _ = skills.resolve_resource("test-skill")
    stale_resource = identity.model_dump(mode="json")
    stale_resource[identity_field] = stale_value
    model = ScriptedModel([_finish("已拒绝过期 Skill 上下文")])
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    await _seed_pending_composite_call(
        execution,
        [stale_resource],
    )
    before_resume = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )

    assert before_resume.next == ("tools",)
    outcome = await execution.continue_from_checkpoint()
    after_resume = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    stale_outcome = next(
        json.loads(str(message.content))
        for message in after_resume.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "stale-resume-call"
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert workflow.calls == 0
    assert stale_outcome["error_code"] == "skill_context_stale"
    assert stale_outcome["retryable"] is False
    assert "test-skill" in stale_outcome["recovery_hint"]
    assert after_resume.values["loaded_skill_resources"] == []
    assert all(
        "echo_composite" not in visible
        for visible in model.tool_definition_snapshots
    )


@pytest.mark.asyncio
async def test_resume_to_tools_rejects_stale_child_resource_before_execution(
    tmp_path,
) -> None:
    definition_root = tmp_path / "resume_skill_definitions"
    skill_root = definition_root / "test-skill"
    references_root = skill_root / "references"
    references_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: test-skill",
                "description: Controlled workflow method.",
                "version: 1.0",
                "tools:",
                "  - echo_composite",
                "---",
                "Current workflow method body.",
            ]
        ),
        encoding="utf-8",
    )
    (references_root / "rules.md").write_text(
        "Current reference rules.",
        encoding="utf-8",
    )
    skills = SkillCatalog.load_from_directory(definition_root)
    body, _ = skills.resolve_resource("test-skill")
    reference, _ = skills.resolve_resource(
        "test-skill",
        reference="rules",
    )
    stale_reference = reference.model_dump(mode="json")
    stale_reference["resource_sha256"] = "0" * 64
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_composite",
                        "args": {"text": "retry after child cleanup"},
                        "id": "retry-after-stale-child",
                        "type": "tool_call",
                    }
                ],
            ),
            _finish("已清理过期 Skill 子资源"),
        ]
    )
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    current_body = body.model_dump(mode="json")
    await _seed_pending_composite_call(
        execution,
        [current_body, stale_reference],
    )

    before_resume = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    assert before_resume.next == ("tools",)
    outcome = await execution.continue_from_checkpoint()
    after_resume = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    stale_outcome = next(
        json.loads(str(message.content))
        for message in after_resume.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "stale-resume-call"
    )
    retried_outcome = next(
        json.loads(str(message.content))
        for message in after_resume.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "retry-after-stale-child"
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert workflow.calls == 1
    assert stale_outcome["error_code"] == "skill_context_stale"
    assert stale_outcome["retryable"] is True
    assert retried_outcome["status"] == "completed"
    assert after_resume.values["loaded_skill_resources"] == [
        current_body
    ]
    assert "echo_composite" in model.tool_definition_snapshots[0]


@pytest.mark.asyncio
async def test_pending_skill_independent_domain_tool_retries_after_stale_cleanup(
    tmp_path,
) -> None:
    handler = EchoCapability()
    layer = _layer(handler)
    identity, _ = layer.skills.resolve_resource("test-skill")
    stale_body = identity.model_dump(mode="json")
    stale_body["resource_sha256"] = "0" * 64
    model = ScriptedModel(
        [
            _echo(
                "retry after cleanup",
                call_id="retry-after-stale-cleanup",
            ),
            _finish("无 Skill 依赖 Tool 重试完成"),
        ]
    )
    execution = _execution_for_layer(tmp_path, model, layer)
    await _seed_pending_tool_call(
        execution,
        [stale_body],
        {
            "name": "echo_tool",
            "args": {"text": "retry after cleanup"},
            "id": "pending-stale-echo",
            "type": "tool_call",
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    tool_outcomes = {
        str(message.tool_call_id): json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    }

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "无 Skill 依赖 Tool 重试完成"
    assert handler.calls == 1
    assert tool_outcomes["pending-stale-echo"]["error_code"] == (
        "skill_context_stale"
    )
    assert tool_outcomes["pending-stale-echo"]["retryable"] is True
    assert tool_outcomes["retry-after-stale-cleanup"]["status"] == "completed"
    assert snapshot.values["loaded_skill_resources"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_field", "stale_value"),
    [
        ("skill_version", "0.9"),
        ("resource_sha256", "0" * 64),
    ],
)
async def test_pending_load_skill_cleans_stale_body_before_retry(
    tmp_path,
    identity_field,
    stale_value,
) -> None:
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled reload method.",
            tools=("echo_composite",),
            content="Current reload method body.",
        )
    )
    identity, _ = skills.resolve_resource("test-skill")
    current_body = identity.model_dump(mode="json")
    stale_body = dict(current_body)
    stale_body[identity_field] = stale_value
    retry_call = {
        "name": "load_skill",
        "args": {"skill_name": "test-skill"},
        "id": "retry-current-load",
        "type": "tool_call",
    }
    model = ScriptedModel(
        [
            AIMessage(content="", tool_calls=[retry_call]),
            _finish("重新加载当前 Skill 正文后完成"),
        ]
    )
    observer = RecordingObserver()
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
        observer=observer,
    )
    await _seed_pending_tool_call(
        execution,
        [stale_body],
        {
            "name": "load_skill",
            "args": {"skill_name": "test-skill"},
            "id": "pending-stale-load",
            "type": "tool_call",
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    tool_outcomes = {
        str(message.tool_call_id): json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    }
    skill_events = [
        event_type
        for event_type, _, _ in observer.events
        if event_type.startswith("skill.load_")
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert workflow.calls == 0
    assert tool_outcomes["pending-stale-load"]["error_code"] == (
        "skill_context_stale"
    )
    assert tool_outcomes["pending-stale-load"]["retryable"] is True
    assert "重新调用 load_skill" in (
        tool_outcomes["pending-stale-load"]["recovery_hint"]
    )
    assert tool_outcomes["retry-current-load"]["status"] == "completed"
    assert snapshot.values["loaded_skill_resources"] == [current_body]
    assert skill_events == [
        "skill.load_started",
        "skill.load_failed",
        "skill.load_started",
        "skill.load_completed",
    ]


@pytest.mark.asyncio
async def test_pending_load_skill_cleans_stale_child_before_retry(
    tmp_path,
) -> None:
    definition_root = tmp_path / "pending_load_skill_definitions"
    skill_root = definition_root / "test-skill"
    references_root = skill_root / "references"
    references_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: test-skill",
                "description: Controlled child reload method.",
                "version: 1.0",
                "tools:",
                "  - echo_composite",
                "---",
                "Current child reload method body.",
            ]
        ),
        encoding="utf-8",
    )
    (references_root / "rules.md").write_text(
        "Current child reference rules.",
        encoding="utf-8",
    )
    skills = SkillCatalog.load_from_directory(definition_root)
    body, _ = skills.resolve_resource("test-skill")
    reference, _ = skills.resolve_resource(
        "test-skill",
        reference="rules",
    )
    current_body = body.model_dump(mode="json")
    current_reference = reference.model_dump(mode="json")
    stale_reference = dict(current_reference)
    stale_reference["resource_sha256"] = "0" * 64
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    retry_call = {
        "name": "load_skill",
        "args": {
            "skill_name": "test-skill",
            "reference": "rules",
        },
        "id": "retry-current-child",
        "type": "tool_call",
    }
    model = ScriptedModel(
        [
            AIMessage(content="", tool_calls=[retry_call]),
            _finish("重新加载当前 Skill 子资源后完成"),
        ]
    )
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    await _seed_pending_tool_call(
        execution,
        [current_body, stale_reference],
        {
            "name": "load_skill",
            "args": {
                "skill_name": "test-skill",
                "reference": "rules",
            },
            "id": "pending-stale-child-load",
            "type": "tool_call",
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    tool_outcomes = {
        str(message.tool_call_id): json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    }

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert workflow.calls == 0
    assert tool_outcomes["pending-stale-child-load"]["error_code"] == (
        "skill_context_stale"
    )
    assert tool_outcomes["pending-stale-child-load"]["retryable"] is True
    assert "重新调用 load_skill" in (
        tool_outcomes["pending-stale-child-load"]["recovery_hint"]
    )
    assert tool_outcomes["retry-current-child"]["status"] == "completed"
    assert snapshot.values["loaded_skill_resources"] == [
        current_body,
        current_reference,
    ]


@pytest.mark.asyncio
async def test_agent_node_cleans_stale_skill_context_before_model_call(
    tmp_path,
) -> None:
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled agent recovery method.",
            tools=("echo_composite",),
            content="Current agent recovery body.",
        )
    )
    identity, _ = skills.resolve_resource("test-skill")
    stale_body = identity.model_dump(mode="json")
    stale_body["resource_sha256"] = "0" * 64
    model = ScriptedModel([_finish("agent 节点清理后完成")])
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    await _seed_agent_checkpoint(execution, [stale_body])

    before_resume = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    assert before_resume.next == ("agent",)
    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    recoveries = [
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, SystemMessage)
        and message.name == "skill_context_recovery"
    ]

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "agent 节点清理后完成"
    assert model.calls == 1
    assert snapshot.values["loaded_skill_resources"] == []
    assert recoveries[0]["error_code"] == "skill_context_stale"
    assert recoveries[0]["retryable"] is True


@pytest.mark.asyncio
async def test_agent_node_removes_orphan_child_when_skill_body_is_stale(
    tmp_path,
) -> None:
    definition_root = tmp_path / "agent_orphan_child_definitions"
    skill_root = definition_root / "test-skill"
    references_root = skill_root / "references"
    references_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: test-skill",
                "description: Controlled orphan cleanup method.",
                "version: 1.0",
                "tools:",
                "  - echo_composite",
                "---",
                "Current orphan cleanup body.",
            ]
        ),
        encoding="utf-8",
    )
    (references_root / "rules.md").write_text(
        "ORPHAN_CHILD_MUST_NOT_BE_VISIBLE",
        encoding="utf-8",
    )
    skills = SkillCatalog.load_from_directory(definition_root)
    body, _ = skills.resolve_resource("test-skill")
    reference, _ = skills.resolve_resource(
        "test-skill",
        reference="rules",
    )
    stale_body = body.model_dump(mode="json")
    stale_body["resource_sha256"] = "0" * 64
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    model = SkillContextRecordingFinishModel("orphan child 已清理")
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    await _seed_agent_checkpoint(
        execution,
        [reference.model_dump(mode="json"), stale_body],
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert model.loaded_skill_contexts == []
    assert snapshot.values["loaded_skill_resources"] == []
    assert "ORPHAN_CHILD_MUST_NOT_BE_VISIBLE" not in json.dumps(
        [message.content for message in snapshot.values["messages"]],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_pending_load_cleans_orphan_child_then_reloads_body_first(
    tmp_path,
) -> None:
    definition_root = tmp_path / "pending_orphan_child_definitions"
    skill_root = definition_root / "test-skill"
    references_root = skill_root / "references"
    references_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: test-skill",
                "description: Controlled body-first reload method.",
                "version: 1.0",
                "tools:",
                "  - echo_composite",
                "---",
                "Current body-first reload body.",
            ]
        ),
        encoding="utf-8",
    )
    (references_root / "rules.md").write_text(
        "Current body-first reference.",
        encoding="utf-8",
    )
    skills = SkillCatalog.load_from_directory(definition_root)
    body, _ = skills.resolve_resource("test-skill")
    reference, _ = skills.resolve_resource(
        "test-skill",
        reference="rules",
    )
    current_body = body.model_dump(mode="json")
    current_reference = reference.model_dump(mode="json")
    stale_body = dict(current_body)
    stale_body["resource_sha256"] = "0" * 64
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "test-skill"},
                        "id": "retry-body-first",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {
                            "skill_name": "test-skill",
                            "reference": "rules",
                        },
                        "id": "retry-child-after-body",
                        "type": "tool_call",
                    }
                ],
            ),
            _finish("按正文优先顺序重新加载完成"),
        ]
    )
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    await _seed_pending_tool_call(
        execution,
        [current_reference, stale_body],
        {
            "name": "load_skill",
            "args": {"skill_name": "test-skill"},
            "id": "pending-orphan-load",
            "type": "tool_call",
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    outcomes = {
        str(message.tool_call_id): json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    }

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcomes["pending-orphan-load"]["error_code"] == (
        "skill_context_stale"
    )
    assert outcomes["retry-body-first"]["status"] == "completed"
    assert outcomes["retry-child-after-body"]["status"] == "completed"
    assert snapshot.values["loaded_skill_resources"] == [
        current_body,
        current_reference,
    ]


@pytest.mark.asyncio
async def test_pending_control_tool_rejects_and_cleans_stale_skill_context(
    tmp_path,
) -> None:
    workflow = EchoCompositeCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled pending control recovery method.",
            tools=("echo_composite",),
            content="Current pending control recovery body.",
        )
    )
    identity, _ = skills.resolve_resource("test-skill")
    stale_body = identity.model_dump(mode="json")
    stale_body["resource_sha256"] = "0" * 64
    model = ScriptedModel([_finish("控制 Tool 清理后重试完成")])
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=skills),
    )
    await _seed_pending_tool_call(
        execution,
        [stale_body],
        {
            "name": "finish_task",
            "args": {"final_response": "不应直接完成"},
            "id": "pending-stale-finish",
            "type": "tool_call",
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    stale_outcome = next(
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "pending-stale-finish"
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "控制 Tool 清理后重试完成"
    assert model.calls == 1
    assert stale_outcome["error_code"] == "skill_context_stale"
    assert stale_outcome["retryable"] is True
    assert snapshot.values["loaded_skill_resources"] == []


@pytest.mark.asyncio
async def test_composite_goal_uses_bounded_observable_plan(tmp_path) -> None:
    model = PlanningModel()
    observer = RecordingObserver()
    execution, _, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("先检查输入，再汇总回答")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "复合目标已完成"
    assert outcome.tool_calls == 5
    assert len([event for event in observer.events if event[0] == "task.created"]) == 2
    updates = [
        event
        for event in observer.events
        if event[0] == "task.updated" and event[1].get("task_id")
    ]
    assert [event[1]["status"] for event in updates] == [
        "in_progress",
        "completed",
        "in_progress",
        "completed",
    ]
    assert "【动态路由】" in execution._system_prompt
    assert "【响应契约】" in execution._system_prompt
    assert all(
        term in execution._system_prompt
        for term in ("简单问答", "单能力任务", "计划")
    )
    assert all(
        term in execution._system_prompt
        for term in ("最小充分表达", "当前用户", "科学真实性", "直接观测")
    )
    assert all(
        term in execution._system_prompt
        for term in (
            "领域术语或方法",
            "操作定义",
            "统计假设",
            "答复篇幅不是",
            "基于 Skill 方法上下文回答",
            "不得因此读取数据或执行领域 Tool",
        )
    )
    assert execution._system_prompt.index("【响应契约】") > (
        execution._system_prompt.index("【可用 Tool 与调用提示】")
    )
    assert "Agent-facing Tool 只接收" in execution._system_prompt


@pytest.mark.asyncio
async def test_successful_domain_tools_auto_reconcile_active_plan_steps(
    tmp_path,
) -> None:
    model = AutoReconciledPlanningModel()
    observer = RecordingObserver()
    execution, _, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("依次完成两个回显步骤")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "自动对账后的复合目标已完成"
    assert outcome.tool_calls == 3
    assert set(snapshot.values["plan_task_statuses"].values()) == {"completed"}
    assert snapshot.values["active_plan_task_id"] == ""
    assert {
        handle
        for handles in snapshot.values["plan_step_evidence"].values()
        for handle in handles
    } == {"echo-auto-step-1", "echo-auto-step-2"}
    completed_outcomes = [
        item for item in model.tool_outcomes if item["capability"] == "echo_tool"
    ]
    assert [item["evidence_handle"] for item in completed_outcomes] == [
        "echo-auto-step-1",
        "echo-auto-step-2",
    ]
    assert all(item.get("plan_task_id") for item in completed_outcomes)
    assert [
        event[1]["status"]
        for event in observer.events
        if event[0] == "task.updated"
    ] == ["in_progress", "completed", "in_progress", "completed"]


@pytest.mark.asyncio
async def test_replayed_tool_call_evidence_does_not_advance_next_plan_step(
    tmp_path,
) -> None:
    model = ReplayedEvidencePlanningModel()
    observer = RecordingObserver()
    handler = EchoCapability()
    execution, _, _ = _execution(
        tmp_path,
        model,
        observer=observer,
        handler=handler,
    )

    outcome = await execution.start("用独立证据依次完成两个回显步骤")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "幂等证据计划已完成"
    assert handler.calls == 2
    assert set(snapshot.values["plan_task_statuses"].values()) == {
        "completed"
    }
    evidence_by_task = snapshot.values["plan_step_evidence"]
    assert sorted(evidence_by_task.values()) == [
        ["fresh-evidence"],
        ["replayed-evidence"],
    ]
    assert (
        snapshot.values["tool_evidence"]["replayed-evidence"][
            "plan_task_id"
        ]
        != snapshot.values["tool_evidence"]["fresh-evidence"][
            "plan_task_id"
        ]
    )
    assert any(
        item.get("error_code") == "tool_call_id_conflict"
        for item in model.tool_outcomes
    )
    assert [
        event[1]["status"]
        for event in observer.events
        if event[0] == "task.updated"
    ] == ["in_progress", "completed", "in_progress", "completed"]


@pytest.mark.asyncio
async def test_artifact_handle_is_hydrated_without_exposing_workspace_uri(
    tmp_path,
) -> None:
    registry = CapabilityRegistry()
    registry.register(ProduceArtifactCapability())
    registry.register(ConsumeArtifactCapability())
    model = ArtifactHandleChainingModel()
    execution = _execution_for_layer(
        tmp_path,
        model,
        DomainCapabilityLayer(registry=registry, skills=SkillCatalog()),
    )

    outcome = await execution.start("生成产物后通过句柄读取")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "句柄串联完成"
    assert model.outcomes[-1]["result"] == {"content": "controlled content"}
    assert "workspace://" not in json.dumps(model.outcomes, ensure_ascii=False)
    consume_definition = next(
        item
        for item in model.tool_definitions
        if item["function"]["name"] == "consume_artifact"
    )
    artifact_schema = consume_definition["function"]["parameters"]["$defs"][
        "ArtifactRef"
    ]
    assert artifact_schema["required"] == ["artifact_id"]
    assert set(artifact_schema["properties"]) == {"artifact_id"}


@pytest.mark.asyncio
async def test_invalid_replacement_plan_does_not_cancel_checkpoint_plan(
    tmp_path,
) -> None:
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        UnknownReplacementModel(),
        observer=observer,
        config=AgentLoopConfig(max_turns=2),
    )

    await execution.start("先创建计划，再提交无效修订")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )

    replacement_cancellations = [
        payload
        for event_type, payload, _ in observer.events
        if event_type == "task.updated"
        and payload.get("summary") == "计划已被新修订替换"
    ]
    assert replacement_cancellations == []
    assert set(snapshot.values["plan_task_statuses"].values()) == {
        "in_progress",
        "pending",
    }
    tool_message = next(
        message
        for message in reversed(snapshot.values["messages"])
        if isinstance(message, ToolMessage)
    )
    assert json.loads(str(tool_message.content))["error_code"] == (
        "plan_capability_unknown"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "expected_error"),
    [
        (
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "update_task_plan",
                            "args": {
                                "task_id": str(uuid4()),
                                "status": "failed",
                                "summary": "不存在的步骤",
                            },
                            "id": "unknown-plan-task",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="停止并保留失败结果"),
            ],
            "plan_task_unknown",
        ),
        (
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_task_plan",
                            "args": {
                                "rationale": "验证完成门禁",
                                "steps": [
                                    {
                                        "title": "待完成步骤",
                                        "objective": "产生可验证结果",
                                        "success_criteria": "Tool 返回证据",
                                        "capability_hint": "echo_tool",
                                    },
                                    {
                                        "title": "待汇总步骤",
                                        "objective": "汇总第一步结果",
                                        "success_criteria": "完成结果说明",
                                        "depends_on": [1],
                                        "capability_hint": "echo_tool",
                                    },
                                ],
                            },
                            "id": "pending-plan",
                            "type": "tool_call",
                        }
                    ],
                ),
                _finish("不能提前完成"),
                AIMessage(content="停止并保留失败结果"),
            ],
            "plan_incomplete",
        ),
    ],
)
async def test_control_tool_failures_use_structured_outcome(
    tmp_path,
    responses,
    expected_error,
) -> None:
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel(responses),
        config=AgentLoopConfig(
            max_turns=len(responses) + 1,
            max_empty_reprompts=0,
        ),
    )

    await execution.start("验证控制 Tool 失败契约")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    outcome = next(
        json.loads(str(message.content))
        for message in reversed(snapshot.values["messages"])
        if isinstance(message, ToolMessage)
        and json.loads(str(message.content)).get("error_code")
        == expected_error
    )

    assert set(outcome) >= {
        "status",
        "capability",
        "summary",
        "error_code",
        "retryable",
        "recovery_hint",
    }
    assert outcome["status"] == "failed"


@pytest.mark.asyncio
async def test_composite_plan_combines_read_only_and_atomic_tools(
    tmp_path,
) -> None:
    inspection = InspectEchoCapability()
    atomic = EchoCapability()
    registry = CapabilityRegistry()
    registry.register(inspection)
    registry.register(atomic)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled combined method.",
            tools=("inspect_echo", "echo_tool"),
            content="Combined method body.",
        )
    )
    execution = _execution_for_layer(
        tmp_path,
        CompositeRoutingModel(),
        DomainCapabilityLayer(registry=registry, skills=skills),
    )

    outcome = await execution.start("先检查，再执行原子操作")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "组合目标已完成"
    assert outcome.tool_calls == 6
    assert inspection.calls == 1
    assert atomic.calls == 1


@pytest.mark.asyncio
async def test_new_run_resets_terminal_state_and_selected_input_context(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(
        conversation_id,
        tmp_path / str(conversation_id),
    )
    dataset = store.write_bytes(
        "uploads/selected.h5ad",
        b"selected",
        kind="dataset",
        media_type="application/x-hdf5",
    )
    models = deque(
        [
            ContextRecordingFinishModel("first run"),
            ContextRecordingFinishModel("second run"),
        ]
    )
    first_model, second_model = tuple(models)
    factory = AgentLoopFactory(
        _layer(EchoCapability()),
        model_factory=models.popleft,
        capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
    )
    checkpointer = InMemorySaver()
    context = CapabilityContext(conversation_id=conversation_id, artifacts=store)
    first = factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        input_artifacts=(dataset,),
        checkpointer=checkpointer,
    )
    second = factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        checkpointer=checkpointer,
    )

    first_outcome = await first.start("use selected input")
    second_outcome = await second.start("continue without selected input")

    assert first_outcome.final_response == "first run"
    assert second_outcome.final_response == "second run"
    assert len(first_model.artifact_contexts) == 1
    assert str(dataset.artifact_id) in first_model.artifact_contexts[0]
    assert second_model.artifact_contexts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_tool_call_id",
    ["shared-finish-call", "second-finish-call"],
)
async def test_provider_message_id_is_namespaced_across_consecutive_runs(
    tmp_path,
    second_tool_call_id,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(
        conversation_id,
        tmp_path / str(conversation_id),
    )
    models = deque(
        [
            ProviderIdentifiedFinishModel("first run", "shared-finish-call"),
            ProviderIdentifiedFinishModel("second run", second_tool_call_id),
        ]
    )
    factory = AgentLoopFactory(
        _layer(EchoCapability()),
        model_factory=models.popleft,
        capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
    )
    checkpointer = InMemorySaver()
    context = CapabilityContext(conversation_id=conversation_id, artifacts=store)
    first = factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        checkpointer=checkpointer,
    )
    second = factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        checkpointer=checkpointer,
    )

    first_outcome = await first.start("first provider-identified run")
    second_outcome = await second.start("second provider-identified run")
    snapshot = await second._graph.aget_state(  # noqa: SLF001
        second._graph_config  # noqa: SLF001
    )
    ai_messages = [
        message
        for message in snapshot.values["messages"]
        if isinstance(message, AIMessage)
    ]
    tool_messages = [
        message
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert first_outcome.status == AgentOutcomeStatus.COMPLETED
    assert second_outcome.status == AgentOutcomeStatus.COMPLETED
    assert second_outcome.final_response == "second run"
    assert len(ai_messages) == 2
    assert len({message.id for message in ai_messages}) == 2
    assert all(message.id != "provider-fixed-id" for message in ai_messages)
    assert sorted(
        str(call["id"])
        for message in ai_messages
        for call in message.tool_calls
    ) == sorted(["shared-finish-call", second_tool_call_id])
    assert sorted(
        str(message.tool_call_id) for message in tool_messages
    ) == sorted(["shared-finish-call", second_tool_call_id])


@pytest.mark.asyncio
async def test_pending_task_backpressure_is_finite(tmp_path) -> None:
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_task_plan",
                        "args": {
                            "rationale": "目标包含两个依赖步骤",
                            "steps": [
                                {
                                    "title": "第一步",
                                    "objective": "执行第一步",
                                    "success_criteria": "echo_tool 返回证据",
                                    "capability_hint": "echo_tool",
                                },
                                {
                                    "title": "第二步",
                                    "objective": "执行第二步",
                                    "success_criteria": "echo_tool 返回证据",
                                    "depends_on": [1],
                                    "capability_hint": "echo_tool",
                                },
                            ],
                        },
                        "id": "backpressure-plan",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="maybe"),
            AIMessage(content="still maybe"),
        ]
    )
    execution, _, _ = _execution(
        tmp_path,
        model,
        config=AgentLoopConfig(max_empty_reprompts=1),
    )

    outcome = await execution.start("do not stop early")

    assert outcome.status == AgentOutcomeStatus.STALLED
    assert outcome.turn_count == 3
    assert outcome.tool_calls == 1
    assert "显式计划仍未完成" in (outcome.stop_reason or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", None])
async def test_non_completed_plan_blocks_natural_text_completion(
    tmp_path,
    status,
) -> None:
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel([AIMessage(content="这不是可接受的最终答复")]),
        config=AgentLoopConfig(max_empty_reprompts=0),
    )
    await _seed_agent_checkpoint(execution, [])
    task_id = str(uuid4())
    statuses = {} if status is None else {task_id: status}
    await execution._graph.aupdate_state(  # noqa: SLF001
        execution._graph_config,  # noqa: SLF001
        {
            "plan_revision": 1,
            "plan_task_ids": [task_id],
            "plan_task_statuses": statuses,
        },
        as_node="tools",
    )

    outcome = await execution.continue_from_checkpoint()

    assert outcome.status == AgentOutcomeStatus.STALLED
    assert outcome.final_response is None
    assert "显式计划仍未完成" in (outcome.stop_reason or "")


@pytest.mark.asyncio
async def test_finish_task_rejects_plan_task_with_missing_status(tmp_path) -> None:
    task_id = str(uuid4())
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel([AIMessage(content="仍然不能结束")]),
        config=AgentLoopConfig(max_empty_reprompts=0),
    )
    await _seed_pending_tool_call(
        execution,
        [],
        {
            "name": "finish_task",
            "args": {"final_response": "不应完成"},
            "id": "finish-missing-plan-status",
            "type": "tool_call",
        },
        state_updates={
            "plan_revision": 1,
            "plan_task_ids": [task_id],
            "plan_task_statuses": {},
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    failure = next(
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "finish-missing-plan-status"
    )

    assert outcome.status == AgentOutcomeStatus.STALLED
    assert failure["status"] == "failed"
    assert failure["error_code"] == "plan_incomplete"


@pytest.mark.asyncio
async def test_finish_task_rejects_blank_final_response(tmp_path) -> None:
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel(
            [
                _finish("   "),
                AIMessage(content="改为有效的自然文本答复"),
            ]
        ),
        config=AgentLoopConfig(max_empty_reprompts=0),
    )

    outcome = await execution.start("拒绝空白结构化答复")
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    failure = next(
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and json.loads(str(message.content)).get("error_code")
        == "tool_arguments_invalid"
    )

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "改为有效的自然文本答复"
    assert failure["status"] == "failed"


@pytest.mark.asyncio
async def test_finish_task_rejects_blank_failed_plan_limitation(tmp_path) -> None:
    task_id = str(uuid4())
    execution, _, _ = _execution(
        tmp_path,
        ScriptedModel([AIMessage(content="仍未提供有效限制说明")]),
        config=AgentLoopConfig(max_empty_reprompts=0),
    )
    await _seed_pending_tool_call(
        execution,
        [],
        {
            "name": "finish_task",
            "args": {
                "final_response": "不能用空白限制完成",
                "limitations": ["   "],
            },
            "id": "finish-blank-limitation",
            "type": "tool_call",
        },
        state_updates={
            "plan_revision": 1,
            "plan_task_ids": [task_id],
            "plan_task_statuses": {task_id: "failed"},
        },
    )

    outcome = await execution.continue_from_checkpoint()
    snapshot = await execution._graph.aget_state(  # noqa: SLF001
        execution._graph_config  # noqa: SLF001
    )
    failure = next(
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.tool_call_id) == "finish-blank-limitation"
    )

    assert outcome.status == AgentOutcomeStatus.STALLED
    assert failure["status"] == "failed"
    assert failure["error_code"] == "tool_arguments_invalid"


@pytest.mark.asyncio
async def test_model_budget_routes_to_explicit_terminal_outcome(tmp_path) -> None:
    model = ScriptedModel([AIMessage(content="")])
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        model,
        config=AgentLoopConfig(max_turns=1, max_empty_reprompts=2),
        observer=observer,
    )

    outcome = await execution.start("bounded")

    assert outcome.status == AgentOutcomeStatus.BUDGET_EXHAUSTED
    assert outcome.stop_reason == "Agent budget exhausted: turns"
    assert any(event_type == "budget.exhausted" for event_type, _, _ in observer.events)


@pytest.mark.asyncio
async def test_wall_clock_budget_interrupts_a_stalled_model_call(tmp_path) -> None:
    observer = RecordingObserver()
    execution, _, _ = _execution(
        tmp_path,
        NeverReturningModel(),
        config=AgentLoopConfig(timeout_seconds=0.05),
        observer=observer,
    )

    outcome = await asyncio.wait_for(execution.start("time bounded"), timeout=2)

    assert outcome.status == AgentOutcomeStatus.BUDGET_EXHAUSTED
    assert outcome.stop_reason == "Agent budget exhausted: wall_clock"
    assert any(
        event_type == "budget.exhausted" and payload["reason"] == "wall_clock"
        for event_type, payload, _ in observer.events
    )


@pytest.mark.asyncio
async def test_model_retry_is_bounded_and_reaches_completion(tmp_path) -> None:
    model = ScriptedModel([RuntimeError("transient provider failure"), _finish("retried")])
    execution, _, _ = _execution(
        tmp_path,
        model,
        config=AgentLoopConfig(max_model_retries=1),
    )

    outcome = await execution.start("retry model once")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "retried"
    assert outcome.model_calls == 2
    assert model.calls == 2


@pytest.mark.asyncio
async def test_capability_retry_emits_fact_and_runs_once_more(tmp_path) -> None:
    model = ScriptedModel([_echo("retry"), _finish("capability retried")])
    observer = RecordingObserver()
    flaky = FlakyEchoCapability()
    execution, handler, _ = _execution(
        tmp_path,
        model,
        config=AgentLoopConfig(max_tool_retries=1),
        observer=observer,
        handler=flaky,
    )

    outcome = await execution.start("retry capability once")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert handler.calls == 2
    assert any(
        event_type == "capability.retrying" and payload["attempt"] == 2
        for event_type, payload, _ in observer.events
    )
    assert any(event_type == "capability.completed" for event_type, _, _ in observer.events)


@pytest.mark.asyncio
async def test_equivalent_failed_tool_call_is_blocked_after_two_failures(
    tmp_path,
) -> None:
    failing = AlwaysFailEchoCapability()
    observer = RecordingObserver()
    model = ScriptedModel(
        [
            _echo("same", call_id="same-attempt-1"),
            _echo("same", call_id="same-attempt-2"),
            _echo("same", call_id="same-attempt-3"),
            _finish("已停止无变化重试"),
        ]
    )
    execution, handler, _ = _execution(
        tmp_path,
        model,
        config=AgentLoopConfig(max_tool_retries=0),
        handler=failing,
        observer=observer,
    )

    outcome = await execution.start("不要无限重复失败调用")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert handler.calls == 2
    failed_events = [
        payload
        for event_type, payload, _ in observer.events
        if event_type == "capability.failed"
    ]
    assert failed_events
    assert all(payload["retryable"] is False for payload in failed_events)
    snapshot = await execution._graph.aget_state(execution._graph_config)
    tool_payloads = [
        json.loads(str(message.content))
        for message in snapshot.values["messages"]
        if isinstance(message, ToolMessage)
        and str(message.content).startswith("{")
    ]
    assert [
        payload.get("error_code")
        for payload in tool_payloads
        if payload.get("status") == "failed"
    ] == [
        "capability_execution_failed",
        "capability_execution_failed",
        "repeated_failure_blocked",
    ]


@pytest.mark.asyncio
async def test_review_interrupt_is_checkpointed_and_resumed(tmp_path) -> None:
    model = ScriptedModel([_echo("reviewed"), _finish("approved")])
    execution, handler, _ = _execution(
        tmp_path,
        model,
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )

    interrupted = await execution.start("needs review")
    assert interrupted.status == AgentOutcomeStatus.REVIEW_REQUIRED
    assert interrupted.review is not None
    assert handler.calls == 0

    completed = await execution.resume_review(
        interrupted.review.review_id,
        ReviewDecision.APPROVE,
    )
    assert completed.status == AgentOutcomeStatus.COMPLETED
    assert completed.final_response == "approved"
    assert handler.calls == 1


@pytest.mark.asyncio
async def test_rejected_review_returns_to_agent_without_execution(tmp_path) -> None:
    model = ScriptedModel([_echo("blocked"), _finish("handled without tool")])
    execution, handler, _ = _execution(
        tmp_path,
        model,
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )

    interrupted = await execution.start("needs review")
    assert interrupted.review is not None
    completed = await execution.resume_review(
        interrupted.review.review_id,
        ReviewDecision.REJECT,
        comment="not allowed",
    )

    assert completed.status == AgentOutcomeStatus.COMPLETED
    assert handler.calls == 0


def test_agent_loop_factory_uses_agent_primary_alias(tmp_path) -> None:
    model = ScriptedModel([_finish()])

    class RecordingFactory:
        def __init__(self) -> None:
            self.aliases = []

        def create(self, alias):
            self.aliases.append(alias)
            return model

    llm_factory = RecordingFactory()
    conversation_id = uuid4()
    handler = EchoCapability()
    factory = AgentLoopFactory(
        _layer(handler),
        llm_factory=llm_factory,  # type: ignore[arg-type]
        capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
    )
    context = CapabilityContext(
        conversation_id=conversation_id,
        artifacts=ConversationArtifactStore(conversation_id, tmp_path / "workspace"),
    )

    factory.create(
        run_id=uuid4(),
        conversation_id=conversation_id,
        capability_context=context,
        checkpointer=InMemorySaver(),
    )

    assert llm_factory.aliases == ["agent_primary"]
