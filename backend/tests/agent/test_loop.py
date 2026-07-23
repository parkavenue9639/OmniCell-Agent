from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, SystemMessage
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
from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer
from omnicell_agent.capabilities.catalog import SkillCatalog, SkillDefinition
from omnicell_agent.capabilities.contracts import (
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
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
        kind=CapabilityKind.ATOMIC,
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


class FlakyEchoCapability(EchoCapability):
    def invoke(self, request: CapabilityRequest, context: CapabilityContext) -> EchoResult:
        del context
        self.calls += 1
        if self.calls == 1:
            raise CapabilityExecutionError("transient controlled failure")
        return EchoResult(text=EchoRequest.model_validate(request).text)


class InspectEchoCapability(EchoCapability):
    spec = CapabilitySpec(
        name="inspect_echo",
        kind=CapabilityKind.READ_ONLY,
        description="Inspect a controlled value without changing it.",
        prompt_hint="Call only when the controlled value must be inspected.",
    )


class EchoWorkflowCapability(EchoCapability):
    spec = CapabilitySpec(
        name="echo_workflow",
        kind=CapabilityKind.WORKFLOW,
        description="Run the controlled complete echo workflow.",
        prompt_hint="Load test-skill before calling for a complete workflow goal.",
    )


class ScriptedModel:
    def __init__(self, responses: list[AIMessage | Exception]) -> None:
        self.responses = deque(responses)
        self.tool_definitions: list[dict[str, Any]] = []
        self.calls = 0

    def bind_tools(self, tools):
        self.tool_definitions = list(tools)
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
            and "输入 artifact 权威描述" in str(message.content)
        ]
        return _finish(self.response)


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
                                    "capability_name": "echo_tool",
                                },
                                {
                                    "title": "汇总结果",
                                    "description": "基于前一步形成答复",
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
                step["task_id"] for step in json.loads(str(latest_tool.content))["steps"]
            ]
        if self.calls <= 3:
            task_id = self.task_ids[self.calls - 2]
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_task_plan",
                        "args": {
                            "task_id": task_id,
                            "status": "completed",
                            "summary": f"步骤 {self.calls - 1} 已验证",
                        },
                        "id": f"plan-update-{self.calls}",
                        "type": "tool_call",
                    }
                ],
            )
        return _finish("复合目标已完成")


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
            if message.type == "tool"
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
        return _finish("skill loaded")


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
                                {"title": "读取受控值"},
                                {"title": "执行受控原子 Tool"},
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
                for step in json.loads(str(plan_message.content))["steps"]
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


def _echo(text: str = "hello") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "echo_tool",
                "args": {"text": text},
                "id": f"echo-{text}",
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


def _execution_for_layer(tmp_path, model, layer):
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
    )


@pytest.mark.asyncio
async def test_direct_reply_finishes_without_domain_capability(tmp_path) -> None:
    model = ScriptedModel([_finish("可以直接回答")])
    observer = RecordingObserver()
    execution, handler, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("解释当前任务状态")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "可以直接回答"
    assert outcome.turn_count == 1
    assert outcome.tool_calls == 1
    assert handler.calls == 0
    assert not any(
        event_type.startswith("capability.")
        for event_type, _, _ in observer.events
    )


@pytest.mark.asyncio
async def test_skill_body_is_absent_initially_and_loaded_on_demand(
    tmp_path,
) -> None:
    model = SkillLoadingModel()
    observer = RecordingObserver()
    execution, _, _ = _execution(tmp_path, model, observer=observer)

    assert "Controlled test skill." in execution._system_prompt
    assert (
        "Use echo_tool only when an echo is required."
        not in execution._system_prompt
    )

    outcome = await execution.start("load the detailed method")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "skill loaded"
    assert model.loaded_contents == [
        "Use echo_tool only when an echo is required."
    ]
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
        "purpose": "workflow_guidance",
    }
    assert skill_events[1][1]["outcome"] == "loaded"
    assert skill_events[1][1]["content_bytes"] == len(
        b"Use echo_tool only when an echo is required."
    )


@pytest.mark.asyncio
async def test_read_only_route_does_not_load_skill_or_run_workflow(
    tmp_path,
) -> None:
    inspection = InspectEchoCapability()
    workflow = EchoWorkflowCapability()
    registry = CapabilityRegistry()
    registry.register(inspection)
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled workflow method.",
            tools=("echo_workflow",),
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
async def test_agent_routes_capability_then_requires_explicit_finish(tmp_path) -> None:
    model = ScriptedModel([_echo(), _finish("analysis complete")])
    observer = RecordingObserver()
    execution, handler, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("echo and finish")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "analysis complete"
    assert outcome.turn_count == 2
    assert outcome.tool_calls == 2
    assert handler.calls == 1
    assert {event[0] for event in observer.events} >= {
        "agent.turn_started",
        "capability.started",
        "capability.completed",
        "task.updated",
    }
    assert {tool["function"]["name"] for tool in model.tool_definitions} == {
        "echo_tool",
        "load_skill",
        "create_task_plan",
        "update_task_plan",
        "finish_task",
    }


@pytest.mark.asyncio
async def test_workflow_route_loads_skill_before_complete_workflow(
    tmp_path,
) -> None:
    workflow = EchoWorkflowCapability()
    registry = CapabilityRegistry()
    registry.register(workflow)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="test-skill",
            description="Controlled workflow method.",
            tools=("echo_workflow",),
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
                        "name": "echo_workflow",
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


@pytest.mark.asyncio
async def test_composite_goal_uses_bounded_observable_plan(tmp_path) -> None:
    model = PlanningModel()
    observer = RecordingObserver()
    execution, _, _ = _execution(tmp_path, model, observer=observer)

    outcome = await execution.start("先检查输入，再汇总回答")

    assert outcome.status == AgentOutcomeStatus.COMPLETED
    assert outcome.final_response == "复合目标已完成"
    assert outcome.tool_calls == 4
    assert len([event for event in observer.events if event[0] == "task.created"]) == 2
    updates = [event for event in observer.events if event[0] == "task.updated"]
    assert [event[1]["status"] for event in updates[:2]] == [
        "completed",
        "completed",
    ]
    assert "【动态路由】" in execution._system_prompt
    assert all(
        term in execution._system_prompt
        for term in ("简单问答", "单能力任务", "计划")
    )
    assert "八个字段共同构成不可改写的权威引用" in execution._system_prompt


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
async def test_pending_task_backpressure_is_finite(tmp_path) -> None:
    model = ScriptedModel(
        [AIMessage(content="maybe"), AIMessage(content="still maybe")]
    )
    execution, _, _ = _execution(
        tmp_path,
        model,
        config=AgentLoopConfig(max_empty_reprompts=1),
    )

    outcome = await execution.start("do not stop early")

    assert outcome.status == AgentOutcomeStatus.STALLED
    assert outcome.turn_count == 2
    assert "有限提醒" in (outcome.stop_reason or "")


@pytest.mark.asyncio
async def test_model_budget_routes_to_explicit_terminal_outcome(tmp_path) -> None:
    model = ScriptedModel([AIMessage(content="not finished")])
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
