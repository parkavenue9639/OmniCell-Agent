from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import uuid
from collections import deque
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel

from omnicell_agent.agent import (
    AgentLoopConfig,
    AgentLoopFactory,
    CooperativeInProcessCapabilityInvoker,
    DefaultToolPolicy,
)
from omnicell_agent.agent.capability_process import (
    RuntimeCleanupError,
    _runtime_claim_path,
)
from omnicell_agent.api.app import create_app
from omnicell_agent.api.service import ApiService
from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer
from omnicell_agent.capabilities.artifacts import (
    ArtifactBoundaryError,
    ConversationArtifactStore,
)
from omnicell_agent.capabilities.catalog import SkillCatalog, SkillDefinition
from omnicell_agent.capabilities.contracts import (
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
)
from omnicell_agent.capabilities.graph_a import SingleCellAnalysisCapability
from omnicell_agent.capabilities.registry import CapabilityContext, CapabilityRegistry
from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.models import Artifact
from omnicell_agent.runtime import DockerCLI
from omnicell_agent.runs.coordinator import (
    ArtifactUploadTooLargeError,
    ReviewConflictError,
    RunCoordinator,
    RunHeartbeatError,
)
from omnicell_agent.runs.status import (
    ReviewDecision,
    ReviewStatus,
    RunStatus,
    TaskStatus,
)


TEST_DSN = os.environ.get("OMNICELL_TEST_POSTGRES_DSN", "").strip()

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="设置 OMNICELL_TEST_POSTGRES_DSN 后运行 coordinator PostgreSQL 测试",
    ),
]


class EchoRequest(CapabilityRequest):
    text: str


class EchoResult(BaseModel):
    text: str


class EchoCapability:
    spec = CapabilitySpec(
        name="echo_tool",
        kind=CapabilityKind.ATOMIC,
        description="Controlled echo for coordinator tests.",
        prompt_hint="Call only for the controlled coordinator echo.",
    )
    request_model = EchoRequest
    result_model = EchoResult

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: CapabilityRequest, context: CapabilityContext) -> EchoResult:
        del context
        self.calls += 1
        return EchoResult(text=EchoRequest.model_validate(request).text)


class SecretFailingCapability(EchoCapability):
    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> EchoResult:
        del request, context
        raise RuntimeError("token=checker-secret host=/Users/example/private")


def _layer(handler=None) -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    capabilities: tuple[str, ...] = ()
    if handler is not None:
        registry.register(handler)
        capabilities = (handler.spec.name,)
    skills = SkillCatalog()
    if capabilities:
        skills.register(
            SkillDefinition(
                name="test-skill",
                description="Controlled test skill.",
                tools=capabilities,
                content="Use the controlled echo tool.",
            )
        )
    return DomainCapabilityLayer(registry=registry, skills=skills)


def _finish(text: str) -> AIMessage:
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


def _echo(text: str) -> AIMessage:
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


class SharedScriptModel:
    def __init__(self, responses: deque[AIMessage]) -> None:
        self._responses = responses

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        return self._responses.popleft()


class SecretFailingModel:
    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        raise RuntimeError("token=checker-secret host=/Users/example/private")


class EchoingCapabilityFailureModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_feedback: str | None = None

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return _echo("trigger controlled failure")
        tool_message = next(
            message for message in reversed(messages) if isinstance(message, ToolMessage)
        )
        self.tool_feedback = str(tool_message.content)
        return _finish(f"模型观察到的失败反馈：{self.tool_feedback}")


class BlockingFinishModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def bind_tools(self, tools):
        del tools
        return self

    async def ainvoke(self, messages):
        del messages
        self.started.set()
        await self.release.wait()
        return _finish("released")


class ActiveCancelCleanupGateExecution:
    def __init__(self, cancellation) -> None:
        self._cancellation = cancellation
        self.started = asyncio.Event()
        self.cancellation_seen = asyncio.Event()
        self.release_cleanup = asyncio.Event()

    async def start(self, _goal):
        self.started.set()
        await self._cancellation.wait()
        self.cancellation_seen.set()
        await self.release_cleanup.wait()
        raise RuntimeCleanupError("controlled active cancellation cleanup gate")


class ShutdownCleanupGateExecution:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def start(self, _goal):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeCleanupError(
                "controlled graceful shutdown cleanup gate"
            ) from exc


class CleanupGateAgentFactory:
    config = SimpleNamespace(max_turns=1)

    def __init__(self, execution_factory) -> None:
        self._execution_factory = execution_factory
        self.execution = None

    def create(self, **kwargs):
        self.execution = self._execution_factory(kwargs["cancellation"])
        return self.execution


class ArtifactRoutingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_descriptor: dict[str, object] | None = None

    def bind_tools(self, tools):
        assert any(
            tool["function"]["name"] == "single_cell_analysis" for tool in tools
        )
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            context = next(
                message.content
                for message in messages
                if isinstance(message, SystemMessage)
                and "输入 artifact 权威描述" in str(message.content)
            )
            descriptor = json.loads(str(context).split("：\n", 1)[1])[0]
            self.seen_descriptor = descriptor
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "single_cell_analysis",
                        "args": {"dataset": descriptor, "goal": "生成 marker"},
                        "id": "graph-a-selected-dataset",
                        "type": "tool_call",
                    }
                ],
            )
        return _finish("Graph A 已完成")


class ControlledGraphA:
    def __init__(self) -> None:
        self.initial_state: dict[str, object] | None = None

    def invoke(self, state):
        self.initial_state = state
        workspace = Path(state["task_context"]["conversation_workspace"])
        marker_relative = state["marker_table_path"][len("/app/data/") :]
        marker = workspace / marker_relative
        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps(
                [
                    {
                        "gene": "IL7R",
                        "cluster": "0",
                        "pvals": 0.001,
                        "pvals_adj": 0.01,
                        "logfoldchanges": 2.5,
                        "pct.1": 0.8,
                        "pct.2": 0.1,
                    }
                ]
            ),
            encoding="utf-8",
        )
        return {
            **state,
            "plan_steps": [
                {
                    "step_type": "skill_call",
                    "skill_name": "marker_genes_extractor",
                    "instruction": "导出 marker",
                }
            ],
            "current_step_index": 1,
            "task_context": {
                **state["task_context"],
                "resolved_context": {
                    "species": "Human",
                    "tissue": "PBMC",
                    "disease_state": "Healthy",
                    "goal_type": "marker_discovery",
                },
                "eval_record": {"status": "success"},
                "retry_count": 0,
            },
            "sandbox_execution_result": {"status": "success", "stderr": ""},
        }


@pytest_asyncio.fixture
async def runtime():
    suffix = uuid.uuid4().hex[:10]
    settings = PostgresSettings(
        dsn=TEST_DSN,
        app_schema=f"omnicell_p7_app_{suffix}",
        checkpoint_schema=f"omnicell_p7_checkpoint_{suffix}",
        pool_min_size=1,
        pool_max_size=8,
    )
    persistence = PersistenceRuntime(settings)
    await persistence.initialize_schemas()
    await persistence.open()
    try:
        yield persistence
    finally:
        await persistence.close()
        async with await psycopg.AsyncConnection.connect(
            settings.psycopg_conninfo,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            for schema_name in (settings.checkpoint_schema, settings.app_schema):
                await connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )


def _coordinator(
    runtime,
    tmp_path,
    layer,
    model_factory,
    *,
    policy=None,
    config=None,
):
    return RunCoordinator(
        runtime.unit_of_work,
        checkpointer=runtime.checkpoints.get_saver(),
        agent_factory=AgentLoopFactory(
            layer,
            model_factory=model_factory,
            policy=policy,
            config=config,
            capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
        ),
        workspace_root=tmp_path / "workspaces",
    )


def _coordinator_with_factory(runtime, tmp_path, factory):
    return RunCoordinator(
        runtime.unit_of_work,
        checkpointer=runtime.checkpoints.get_saver(),
        agent_factory=factory,
        workspace_root=tmp_path / "workspaces",
    )


@pytest.mark.asyncio
async def test_coordinator_persists_ordered_terminal_lifecycle(runtime, tmp_path) -> None:
    responses = deque([_finish("complete")])
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(responses),
    )
    conversation = await coordinator.create_conversation(title="success")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="finish the controlled task",
        request_key="success-1",
    )
    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        assert stored is not None
        assert stored.status == RunStatus.COMPLETED.value
        assert stored.started_at is not None
        assert stored.finished_at is not None
        rows = await repositories.events.replay(run.id, limit=500)
    assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
    assert rows[-1].event_type == "run.completed"
    assert sum(row.event_type == "run.completed" for row in rows) == 1
    user_messages = [
        row for row in rows if row.event_type == "message.completed"
        and row.payload.get("role") == "user"
    ]
    assert [row.payload["content"] for row in user_messages] == [
        "finish the controlled task"
    ]

    page = await coordinator.event_log.replay(run.id, after_sequence=0)
    assert page.terminal is True
    assert page.events[-1].type.value == "run.completed"
    await coordinator.close()


@pytest.mark.asyncio
async def test_unclassified_failure_is_redacted_from_public_run_surfaces(
    runtime,
    tmp_path,
) -> None:
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        SecretFailingModel,
        config=AgentLoopConfig(max_model_retries=0),
    )
    app = create_app(ApiService(runtime.unit_of_work, coordinator))
    conversation = await coordinator.create_conversation(title="redacted failure")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="fail without exposing internal exception text",
    )
    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        tasks = await repositories.tasks.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
        rows = await repositories.events.replay(run.id, limit=500)

    assert stored is not None and stored.status == RunStatus.FAILED.value
    assert stored.error_summary == "运行执行失败；详细诊断仅保留在服务端日志。"
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.FAILED.value
    assert tasks[0].error_summary == stored.error_summary
    assert rows[-1].event_type == "run.failed"
    assert rows[-1].payload == {
        "status": "failed",
        "error_code": "run_execution_failed",
        "error_summary": stored.error_summary,
        "retryable": False,
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(f"/api/v1/runs/{run.id}")
        replay = await client.get(f"/api/v1/runs/{run.id}/events")
    assert response.status_code == 200
    assert replay.status_code == 200
    public_payload = json.dumps(
        {"run": response.json(), "events": replay.json()},
        ensure_ascii=False,
    )
    assert "checker-secret" not in public_payload
    assert "/Users/example/private" not in public_payload
    assert "RuntimeError" not in public_payload
    assert "run_execution_failed" in public_payload
    assert stored.error_summary in public_payload
    await coordinator.close()


@pytest.mark.asyncio
async def test_capability_failure_event_redacts_internal_exception_text(
    runtime,
    tmp_path,
) -> None:
    model = EchoingCapabilityFailureModel()
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(SecretFailingCapability()),
        lambda: model,
        config=AgentLoopConfig(max_tool_retries=0),
    )
    app = create_app(ApiService(runtime.unit_of_work, coordinator))
    conversation = await coordinator.create_conversation(title="capability redaction")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="recover after a redacted capability failure",
    )
    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        tasks = await repositories.tasks.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
    replay = await coordinator.event_log.replay(run.id, after_sequence=0)
    failed = [event for event in replay.events if event.type.value == "capability.failed"]
    assert len(failed) == 1
    assert failed[0].payload.error_code == "capability_execution_failed"
    assert failed[0].payload.error_summary == (
        "能力执行失败；详细诊断仅保留在服务端日志。"
    )
    capability_task = next(task for task in tasks if task.capability_name == "echo_tool")
    assert capability_task.status == TaskStatus.FAILED.value
    assert capability_task.error_summary == failed[0].payload.error_summary
    assert model.tool_feedback == (
        "Tool 执行失败：能力执行失败；详细诊断仅保留在服务端日志。"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(f"/api/v1/runs/{run.id}")
        replay_response = await client.get(f"/api/v1/runs/{run.id}/events")
        streamed = await client.get(f"/api/v1/runs/{run.id}/events/stream")
    assert response.status_code == 200
    assert replay_response.status_code == 200
    assert streamed.status_code == 200
    public_payload = json.dumps(
        {
            "run": response.json(),
            "events": replay_response.json(),
            "stream": streamed.text,
        },
        ensure_ascii=False,
    )
    assert "checker-secret" not in public_payload
    assert "/Users/example/private" not in public_payload
    assert "RuntimeError" not in public_payload
    await coordinator.close()


@pytest.mark.asyncio
async def test_claimed_run_reaps_durable_runtime_claims_before_agent_execution(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaped_workspaces: list[Path] = []

    async def record_reaper(workspace):
        reaped_workspaces.append(Path(workspace).resolve())
        return ()

    monkeypatch.setattr(
        "omnicell_agent.runs.coordinator.reap_workspace_runtime_claims",
        record_reaper,
    )
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("reaped before execution")])),
    )
    conversation = await coordinator.create_conversation(title="runtime reaper")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="verify durable runtime cleanup boundary",
    )

    await coordinator.wait(run.id)

    assert reaped_workspaces == [
        (tmp_path / "workspaces" / str(conversation.id)).resolve()
    ]
    await coordinator.close()


@pytest.mark.asyncio
async def test_unresolved_runtime_cleanup_blocks_agent_and_terminal_state(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = BlockingFinishModel()

    async def unresolved_reaper(_workspace):
        raise RuntimeCleanupError("controlled unresolved runtime")

    monkeypatch.setattr(
        "omnicell_agent.runs.coordinator.reap_workspace_runtime_claims",
        unresolved_reaper,
    )
    coordinator = _coordinator(runtime, tmp_path, _layer(), lambda: model)
    conversation = await coordinator.create_conversation(title="cleanup gate")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="do not start before runtime cleanup",
    )

    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.PENDING.value
    assert stored.worker_id == coordinator.worker_id
    assert stored.lease_expires_at is not None
    assert model.started.is_set() is False
    assert all(event.event_type != "run.started" for event in events)
    assert all(
        event.event_type not in {"run.completed", "run.failed", "run.cancelled"}
        for event in events
    )
    await coordinator.close()


@pytest.mark.asyncio
async def test_pre_agent_cleanup_gate_recovery_preserves_start_mode(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaper_calls = 0

    async def fail_once(_workspace):
        nonlocal reaper_calls
        reaper_calls += 1
        if reaper_calls == 1:
            raise RuntimeCleanupError("controlled first-attempt cleanup gate")
        return ()

    monkeypatch.setattr(
        "omnicell_agent.runs.coordinator.reap_workspace_runtime_claims",
        fail_once,
    )
    first_model = BlockingFinishModel()
    first = _coordinator(runtime, tmp_path, _layer(), lambda: first_model)
    conversation = await first.create_conversation(title="start mode cleanup gate")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="start only after cleanup recovery",
    )
    await first.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        staged = await repositories.runs.get_for_update(run.id)
        assert staged is not None and staged.status == RunStatus.PENDING.value
        staged.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("started after cleanup")])),
    )
    assert await recovery.recover(limit=1) == (run.id,)
    await asyncio.wait_for(recovery.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.COMPLETED.value
    assert stored.attempt == 2
    assert sum(event.event_type == "run.started" for event in events) == 1
    assert first_model.started.is_set() is False
    assert reaper_calls == 2
    await recovery.close()
    await first.close()


@pytest.mark.asyncio
async def test_marked_start_without_checkpoint_reconciles_to_start(
    runtime,
    tmp_path,
) -> None:
    first_responses = deque([_finish("prior run completed")])
    first = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(first_responses),
    )
    conversation = await first.create_conversation(title="marked start recovery")
    prior_run = await first.submit_run(
        conversation_id=conversation.id,
        goal="establish a prior checkpoint on the conversation thread",
    )
    await first.wait(prior_run.id)
    original_mark_started = first._mark_execution_started

    async def mark_then_abort(*args, **kwargs):
        await original_mark_started(*args, **kwargs)
        raise RuntimeCleanupError("controlled loss after run.started")

    first._mark_execution_started = mark_then_abort  # type: ignore[method-assign]
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="reconcile a missing initial checkpoint",
    )
    await first.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        staged = await repositories.runs.get_for_update(run.id)
        assert staged is not None and staged.status == RunStatus.RUNNING.value
        staged.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    recovery_responses = deque([_finish("reconciled initial start")])
    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(recovery_responses),
    )
    assert await recovery.recover(limit=1) == (run.id,)
    await asyncio.wait_for(recovery.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.COMPLETED.value
    assert stored.attempt == 2
    assert sum(event.event_type == "run.started" for event in events) == 1
    assistant_messages = [
        event.payload
        for event in events
        if event.event_type == "message.completed"
    ]
    assert any(
        payload.get("content") == "reconciled initial start"
        and payload.get("role") == "assistant"
        for payload in assistant_messages
    ), assistant_messages
    assert any(
        event.event_type == "task.updated"
        and event.payload.get("status") == TaskStatus.COMPLETED.value
        for event in events
    )
    assert all(
        event.payload.get("content") != "prior run completed"
        for event in events
        if event.event_type == "message.completed"
    )
    await recovery.close()
    await first.close()


@pytest.mark.asyncio
async def test_active_cancel_cleanup_gate_blocks_terminal_cancellation(
    runtime,
    tmp_path,
) -> None:
    factory = CleanupGateAgentFactory(
        lambda cancellation: ActiveCancelCleanupGateExecution(cancellation)
    )
    coordinator = _coordinator_with_factory(runtime, tmp_path, factory)

    async def cancellation_heartbeat(
        _run_id,
        *,
        attempt,
        token,
        observe_cancellation=True,
    ):
        del attempt, observe_cancellation
        await token.wait()

    coordinator._heartbeat = cancellation_heartbeat  # type: ignore[method-assign]
    conversation = await coordinator.create_conversation(title="active cancel gate")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="cancel only after exact cleanup",
    )
    while factory.execution is None:
        await asyncio.sleep(0)
    execution = factory.execution
    assert isinstance(execution, ActiveCancelCleanupGateExecution)
    await asyncio.wait_for(execution.started.wait(), timeout=5)

    assert await coordinator.request_cancel(run.id, reason="controlled cancel") is True
    await asyncio.wait_for(execution.cancellation_seen.wait(), timeout=5)
    await asyncio.sleep(0)
    execution.release_cleanup.set()
    await asyncio.wait_for(coordinator.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.CANCELLING.value
    assert stored.worker_id == coordinator.worker_id
    assert stored.lease_expires_at is not None
    assert all(
        event.event_type not in {"run.completed", "run.failed", "run.cancelled"}
        for event in events
    )
    await coordinator.close()


@pytest.mark.asyncio
async def test_graceful_shutdown_cleanup_gate_retains_lease(
    runtime,
    tmp_path,
) -> None:
    factory = CleanupGateAgentFactory(lambda _cancellation: ShutdownCleanupGateExecution())
    coordinator = _coordinator_with_factory(runtime, tmp_path, factory)
    conversation = await coordinator.create_conversation(title="shutdown cleanup gate")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="retain lease until exact cleanup",
    )
    while factory.execution is None:
        await asyncio.sleep(0)
    execution = factory.execution
    assert isinstance(execution, ShutdownCleanupGateExecution)
    await asyncio.wait_for(execution.started.wait(), timeout=5)

    await asyncio.wait_for(coordinator.close(), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.RUNNING.value
    assert stored.worker_id == coordinator.worker_id
    assert stored.lease_expires_at is not None
    assert all(
        event.event_type not in {"run.completed", "run.failed", "run.cancelled"}
        for event in events
    )


@pytest.mark.asyncio
async def test_shutdown_during_pre_agent_reaper_retains_lease(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaper_started = asyncio.Event()

    async def blocking_reaper(_workspace):
        reaper_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "omnicell_agent.runs.coordinator.reap_workspace_runtime_claims",
        blocking_reaper,
    )
    model = BlockingFinishModel()
    coordinator = _coordinator(runtime, tmp_path, _layer(), lambda: model)
    conversation = await coordinator.create_conversation(title="reaper shutdown gate")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="retain lease while pre-agent cleanup is unresolved",
    )
    await asyncio.wait_for(reaper_started.wait(), timeout=5)

    await asyncio.wait_for(coordinator.close(), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.PENDING.value
    assert stored.worker_id == coordinator.worker_id
    assert stored.lease_expires_at is not None
    assert model.started.is_set() is False
    assert all(event.event_type != "run.started" for event in events)
    assert all(
        event.event_type not in {"run.completed", "run.failed", "run.cancelled"}
        for event in events
    )


@pytest.mark.asyncio
async def test_recovered_cancellation_waits_for_runtime_cleanup_before_terminal(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = BlockingFinishModel()
    first = _coordinator(runtime, tmp_path, _layer(), lambda: blocked)
    conversation = await first.create_conversation(title="cancel cleanup gate")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="stage a recoverable cancellation",
    )
    await asyncio.wait_for(blocked.started.wait(), timeout=5)
    await first.close()
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        staged = await repositories.runs.get_for_update(run.id)
        assert staged is not None
        staged.status = RunStatus.CANCELLING.value
        staged.worker_id = None
        staged.lease_expires_at = None

    async def unresolved_reaper(_workspace):
        raise RuntimeCleanupError("controlled unresolved runtime")

    monkeypatch.setattr(
        "omnicell_agent.runs.coordinator.reap_workspace_runtime_claims",
        unresolved_reaper,
    )
    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("must not execute")])),
    )

    assert await recovery.recover(limit=1) == (run.id,)
    await recovery.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.CANCELLING.value
    assert stored.worker_id == recovery.worker_id
    assert stored.lease_expires_at is not None
    assert all(event.event_type != "run.cancelled" for event in events)
    await recovery.close()


@pytest.mark.docker
@pytest.mark.skipif(
    os.environ.get("OMNICELL_RUN_DOCKER_TESTS") != "1",
    reason="同时启用真实 PostgreSQL 与 Docker 后验证 lease takeover runtime reaper",
)
@pytest.mark.asyncio
async def test_lease_recovery_reaps_real_docker_claim_before_agent_execution(
    runtime,
    tmp_path,
) -> None:
    blocked = BlockingFinishModel()
    first = _coordinator(runtime, tmp_path, _layer(), lambda: blocked)
    conversation = await first.create_conversation(title="real runtime recovery")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="recover only after exact Docker cleanup",
    )
    await asyncio.wait_for(blocked.started.wait(), timeout=5)
    await first.close()

    invocation_id = "e" * 32
    workspace = tmp_path / "workspaces" / str(conversation.id)
    claim = _runtime_claim_path(workspace, invocation_id)
    scope = workspace / ".omnicell-invocations" / invocation_id
    scope.mkdir(parents=True)
    (scope / "partial.txt").write_text("partial", encoding="utf-8")
    docker = DockerCLI()
    created = await docker.run(
        (
            "run",
            "--detach",
            "--label",
            f"omnicell.runtime.invocation={invocation_id}",
            os.environ.get("OMNICELL_RUNTIME_IMAGE", "omnicell-worker:latest"),
            "python",
            "-c",
            "import time; time.sleep(300)",
        ),
        timeout=30,
        stdout_max_bytes=4096,
        stderr_max_bytes=4096,
    )
    container_id = created.stdout.decode().strip()
    claim.write_text(
        json.dumps(
            {
                "invocation_id": invocation_id,
                "container_id": container_id,
                "state": "confirmed",
            }
        ),
        encoding="utf-8",
    )
    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("recovered after cleanup")])),
    )
    try:
        assert await recovery.recover(limit=1) == (run.id,)
        await asyncio.wait_for(recovery.wait(run.id), timeout=10)

        inspected = await docker.run(
            ("container", "inspect", container_id),
            timeout=10,
            stdout_max_bytes=4096,
            stderr_max_bytes=4096,
            check=False,
        )
        assert inspected.returncode != 0
        assert not claim.exists()
        assert not scope.exists()
        async with runtime.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            stored = await repositories.runs.get(run.id)
        assert stored is not None and stored.status == RunStatus.COMPLETED.value
    finally:
        await recovery.close()
        await docker.run(
            ("rm", "--force", container_id),
            timeout=10,
            stdout_max_bytes=4096,
            stderr_max_bytes=4096,
            check=False,
        )


@pytest.mark.asyncio
async def test_each_run_routes_only_its_selected_dataset_through_graph_a(
    runtime,
    tmp_path,
) -> None:
    graph = ControlledGraphA()
    capability = SingleCellAnalysisCapability(
        graph_factory=lambda: graph,
        scope_factory=lambda _workspace: nullcontext(),
    )
    registry = CapabilityRegistry()
    registry.register(capability)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="single-cell-analysis",
            description="Controlled Graph A integration skill.",
            tools=(capability.spec.name,),
            content="使用 single_cell_analysis 处理已选择的数据集。",
        )
    )
    models: list[ArtifactRoutingModel] = []

    def model_factory():
        model = ArtifactRoutingModel()
        models.append(model)
        return model

    coordinator = _coordinator(
        runtime,
        tmp_path,
        DomainCapabilityLayer(registry=registry, skills=skills),
        model_factory,
    )
    conversation = await coordinator.create_conversation(title="graph-a routing")
    first_dataset = await coordinator.import_artifact(
        conversation.id,
        source=io.BytesIO(b"first-controlled-h5ad"),
        filename="first-pbmc.h5ad",
        kind="dataset",
        media_type="application/x-hdf5",
    )
    first_run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="使用第一份数据运行 Graph A",
        input_artifact_ids=[first_dataset.id],
    )
    await coordinator.wait(first_run.id)

    second_dataset = await coordinator.import_artifact(
        conversation.id,
        source=io.BytesIO(b"second-controlled-h5ad"),
        filename="second-pbmc.h5ad",
        kind="dataset",
        media_type="application/x-hdf5",
    )
    second_run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="使用第二份数据运行 Graph A",
        input_artifact_ids=[second_dataset.id],
    )
    await coordinator.wait(second_run.id)

    assert len(models) == 2
    assert models[0].seen_descriptor is not None
    assert models[0].seen_descriptor["artifact_id"] == str(first_dataset.id)
    assert models[1].seen_descriptor is not None
    assert models[1].seen_descriptor["artifact_id"] == str(second_dataset.id)
    assert graph.initial_state is not None
    assert graph.initial_state["raw_data_path"].startswith("/app/data/uploads/")
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(second_run.id)
        artifacts = await repositories.artifacts.list_for_run(
            second_run.id,
            conversation_id=conversation.id,
            limit=20,
        )
        events = await repositories.events.replay(second_run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.COMPLETED.value
    assert any(artifact.kind == "marker_table" for artifact in artifacts)
    created_artifact_ids = {
        uuid.UUID(str(event.payload["artifact_id"]))
        for event in events
        if event.event_type == "artifact.created"
    }
    assert second_dataset.id not in created_artifact_ids
    assert created_artifact_ids == {artifact.id for artifact in artifacts}
    await coordinator.close()


@pytest.mark.asyncio
async def test_selected_artifact_outside_latest_5000_executes_without_hydrating_noise(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("oldest selected artifact loaded")])),
    )
    conversation = await coordinator.create_conversation(
        title="exact selected artifact hydration"
    )
    selected = await coordinator.import_artifact(
        conversation.id,
        source=io.BytesIO(b"selected-oldest"),
        filename="oldest.h5ad",
        kind="dataset",
        media_type="application/x-hdf5",
    )

    noise_ids = [uuid.uuid4() for _ in range(5_000)]
    noise_created_at = datetime.now(UTC) + timedelta(minutes=1)
    insert_artifact = sql.SQL(
        """
        INSERT INTO {}.artifacts (
            id, conversation_id, run_id, source_event_id, kind, uri,
            media_type, size_bytes, sha256, metadata, created_at
        )
        VALUES (
            %s, %s, NULL, NULL, 'analysis', %s,
            NULL, 1, %s, '{{}}'::jsonb, %s
        )
        """
    ).format(sql.Identifier(runtime.settings.app_schema))
    async with await psycopg.AsyncConnection.connect(
        runtime.settings.psycopg_conninfo,
        autocommit=True,
    ) as connection:
        async with connection.cursor() as cursor:
            await cursor.executemany(
                insert_artifact,
                [
                    (
                        artifact_id,
                        conversation.id,
                        f"workspace://missing-noise/{artifact_id}.bin",
                        "0" * 64,
                        noise_created_at,
                    )
                    for artifact_id in noise_ids
                ],
            )

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        latest_page = await repositories.artifacts.list_for_conversation(
            conversation.id,
            limit=5_000,
        )
    assert len(latest_page) == 5_000
    assert selected.id not in {artifact.id for artifact in latest_page}

    def suppress_background_schedule(_run_id, coroutine) -> None:
        coroutine.close()

    coordinator._schedule = suppress_background_schedule  # type: ignore[method-assign]
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="use the oldest selected artifact",
        input_artifact_ids=[selected.id],
    )

    event_loop_thread = threading.get_ident()
    registered_ids: list[uuid.UUID] = []
    registration_threads: list[int] = []
    original_register = ConversationArtifactStore.register_trusted

    def record_registration(
        store: ConversationArtifactStore,
        reference,
    ):
        registered_ids.append(reference.artifact_id)
        registration_threads.append(threading.get_ident())
        return original_register(store, reference)

    monkeypatch.setattr(
        ConversationArtifactStore,
        "register_trusted",
        record_registration,
    )
    await coordinator._execute_start(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
    assert stored is not None and stored.status == RunStatus.COMPLETED.value
    assert registered_ids == [selected.id]
    assert registration_threads
    assert all(thread_id != event_loop_thread for thread_id in registration_threads)
    await coordinator.close()


@pytest.mark.asyncio
async def test_execution_context_hydrates_selected_and_current_run_artifacts_only(
    runtime,
    tmp_path,
) -> None:
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque()),
    )

    def suppress_background_schedule(_run_id, coroutine) -> None:
        coroutine.close()

    coordinator._schedule = suppress_background_schedule  # type: ignore[method-assign]
    cases = (
        ("start", RunStatus.PENDING),
        ("resume", RunStatus.REVIEW_REQUIRED),
        ("continue", RunStatus.RUNNING),
    )
    for mode, status in cases:
        conversation = await coordinator.create_conversation(
            title=f"{mode} artifact context"
        )
        selected = await coordinator.import_artifact(
            conversation.id,
            source=io.BytesIO(f"{mode}-selected".encode()),
            filename=f"{mode}-selected.h5ad",
            kind="dataset",
        )
        unrelated = await coordinator.import_artifact(
            conversation.id,
            source=io.BytesIO(f"{mode}-unrelated".encode()),
            filename=f"{mode}-unrelated.txt",
            kind="analysis",
        )
        run = await coordinator.submit_run(
            conversation_id=conversation.id,
            goal=f"{mode} with exact artifacts",
            input_artifact_ids=[selected.id],
        )
        store = ConversationArtifactStore(
            conversation.id,
            tmp_path / "workspaces" / str(conversation.id),
        )
        output_ref = await asyncio.to_thread(
            store.write_text,
            f"run-output/{run.id}.txt",
            f"{mode}-output",
            kind="analysis",
        )
        async with runtime.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            stored_run = await repositories.runs.get_for_update(run.id)
            assert stored_run is not None
            stored_run.status = status.value
            await repositories.artifacts.add(
                Artifact(
                    id=output_ref.artifact_id,
                    conversation_id=conversation.id,
                    run_id=run.id,
                    source_event_id=None,
                    kind=output_ref.kind,
                    uri=output_ref.uri,
                    media_type=output_ref.media_type,
                    size_bytes=output_ref.size_bytes,
                    sha256=output_ref.sha256,
                    artifact_metadata=output_ref.metadata,
                )
            )

        (
            claimed_run,
            _claimed_conversation,
            _goal,
            hydration_artifacts,
            input_artifacts,
            _attempt,
        ) = await coordinator._claim_and_load(
            run.id,
            mode=mode,
            review_id=uuid.uuid4() if mode == "resume" else None,
        )
        assert claimed_run.id == run.id
        assert [reference.artifact_id for reference in input_artifacts] == [
            selected.id
        ]
        assert [artifact.id for artifact in hydration_artifacts] == [
            selected.id,
            output_ref.artifact_id,
        ]
        assert unrelated.id not in {
            artifact.id for artifact in hydration_artifacts
        }

    await coordinator.close()


@pytest.mark.asyncio
async def test_artifact_upload_limits_cleanup_and_workspace_boundary(runtime, tmp_path) -> None:
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("unused")])),
    )
    conversation = await coordinator.create_conversation(title="artifact boundaries")
    with pytest.raises(ArtifactUploadTooLargeError):
        await coordinator.import_artifact(
            conversation.id,
            source=io.BytesIO(b"too-large"),
            filename="oversized.bin",
            kind="dataset",
            max_bytes=4,
        )
    upload_directory = tmp_path / "workspaces" / str(conversation.id) / "uploads"
    assert list(upload_directory.iterdir()) == []

    artifact = await coordinator.import_artifact(
        conversation.id,
        source=io.BytesIO(b"trusted"),
        filename="trusted.h5ad",
        kind="dataset",
    )
    _, content_stream = await coordinator.open_artifact(artifact.id)
    content_path = (
        tmp_path
        / "workspaces"
        / str(conversation.id)
        / artifact.uri.removeprefix("workspace://")
    )
    outside_content = tmp_path / "outside-content"
    outside_content.write_bytes(b"host-secret")
    content_path.unlink()
    content_path.symlink_to(outside_content)
    try:
        assert content_stream.read() == b"trusted"
    finally:
        content_stream.close()
    with pytest.raises(ArtifactBoundaryError, match="symlink|安全打开"):
        await coordinator.open_artifact(artifact.id)

    escaped = await coordinator.create_conversation(title="symlink escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped_uploads = tmp_path / "workspaces" / str(escaped.id) / "uploads"
    escaped_uploads.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="逃逸"):
        await coordinator.import_artifact(
            escaped.id,
            source=io.BytesIO(b"must-not-escape"),
            filename="escape.h5ad",
            kind="dataset",
        )
    assert list(outside.iterdir()) == []
    await coordinator.close()


@pytest.mark.asyncio
async def test_review_interrupt_is_persisted_and_resume_uses_checkpoint(runtime, tmp_path) -> None:
    responses = deque([_echo("reviewed"), _finish("approved")])
    handler = EchoCapability()
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(handler),
        lambda: SharedScriptModel(responses),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    conversation = await coordinator.create_conversation(title="review")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="use the reviewed echo",
    )
    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        waiting = await repositories.runs.get(run.id)
        assert waiting is not None
        assert waiting.status == RunStatus.REVIEW_REQUIRED.value
        reviews = await repositories.reviews.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
        assert len(reviews) == 1
        review = reviews[0]
        assert review.status == ReviewStatus.PENDING.value
        assert review.checkpoint_id

    await coordinator.resolve_review(
        review.id,
        decision=ReviewDecision.APPROVE,
        comment="approved in test",
    )
    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        completed = await repositories.runs.get(run.id)
        resolved = await repositories.reviews.get_by_id(review.id)
        assert completed is not None and completed.status == RunStatus.COMPLETED.value
        assert resolved is not None and resolved.status == ReviewStatus.APPROVED.value
    assert handler.calls == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_pre_agent_cleanup_gate_recovery_preserves_review_resume_mode(
    runtime,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_resume_reaper = False
    reaper_failures = 0

    async def controlled_reaper(_workspace):
        nonlocal fail_resume_reaper, reaper_failures
        if fail_resume_reaper:
            fail_resume_reaper = False
            reaper_failures += 1
            raise RuntimeCleanupError("controlled review resume cleanup gate")
        return ()

    monkeypatch.setattr(
        "omnicell_agent.runs.coordinator.reap_workspace_runtime_claims",
        controlled_reaper,
    )
    responses = deque([_echo("reviewed"), _finish("approved after cleanup")])
    handler = EchoCapability()
    first = _coordinator(
        runtime,
        tmp_path,
        _layer(handler),
        lambda: SharedScriptModel(responses),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    conversation = await first.create_conversation(title="review resume cleanup gate")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="resume review only after cleanup recovery",
    )
    await first.wait(run.id)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        reviews = await repositories.reviews.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
    assert len(reviews) == 1
    review = reviews[0]

    fail_resume_reaper = True
    await first.resolve_review(
        review.id,
        decision=ReviewDecision.APPROVE,
        comment="approved behind cleanup gate",
    )
    await first.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        staged = await repositories.runs.get_for_update(run.id)
        assert staged is not None
        assert staged.status == RunStatus.REVIEW_REQUIRED.value
        staged.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(handler),
        lambda: SharedScriptModel(responses),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    assert await recovery.recover(limit=1) == (run.id,)
    await asyncio.wait_for(recovery.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        resolved = await repositories.reviews.get_by_id(review.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.COMPLETED.value
    assert resolved is not None and resolved.status == ReviewStatus.APPROVED.value
    assert stored.attempt == 3
    assert sum(event.event_type == "run.started" for event in events) == 2
    assert reaper_failures == 1
    assert handler.calls == 1
    await recovery.close()
    await first.close()


@pytest.mark.asyncio
async def test_marked_review_resume_reconciles_unapplied_decision(
    runtime,
    tmp_path,
) -> None:
    responses = deque([_echo("reviewed"), _finish("reconciled review resume")])
    handler = EchoCapability()
    first = _coordinator(
        runtime,
        tmp_path,
        _layer(handler),
        lambda: SharedScriptModel(responses),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    conversation = await first.create_conversation(title="marked review recovery")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="reconcile an unapplied review decision",
    )
    await first.wait(run.id)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        reviews = await repositories.reviews.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
    assert len(reviews) == 1
    review = reviews[0]

    original_mark_started = first._mark_execution_started

    async def mark_then_abort(*args, **kwargs):
        await original_mark_started(*args, **kwargs)
        raise RuntimeCleanupError("controlled loss before review command")

    first._mark_execution_started = mark_then_abort  # type: ignore[method-assign]
    await first.resolve_review(
        review.id,
        decision=ReviewDecision.APPROVE,
        comment="apply after recovery reconciliation",
    )
    await first.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        staged = await repositories.runs.get_for_update(run.id)
        assert staged is not None and staged.status == RunStatus.RUNNING.value
        staged.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(handler),
        lambda: SharedScriptModel(responses),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    assert await recovery.recover(limit=1) == (run.id,)
    await asyncio.wait_for(recovery.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored = await repositories.runs.get(run.id)
        resolved = await repositories.reviews.get_by_id(review.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored is not None and stored.status == RunStatus.COMPLETED.value
    assert resolved is not None and resolved.status == ReviewStatus.APPROVED.value
    assert stored.attempt == 3
    assert sum(event.event_type == "run.started" for event in events) == 2
    assert handler.calls == 1
    await recovery.close()
    await first.close()


@pytest.mark.asyncio
async def test_concurrent_review_decisions_commit_one_authoritative_fact(runtime, tmp_path) -> None:
    responses = deque([_echo("reviewed"), _finish("resolved")])
    handler = EchoCapability()
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(handler),
        lambda: SharedScriptModel(responses),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    conversation = await coordinator.create_conversation(title="review race")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="race the review decision",
    )
    await coordinator.wait(run.id)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        reviews = await repositories.reviews.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
    assert len(reviews) == 1

    outcomes = await asyncio.gather(
        coordinator.resolve_review(reviews[0].id, decision=ReviewDecision.APPROVE),
        coordinator.resolve_review(reviews[0].id, decision=ReviewDecision.REJECT),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ReviewConflictError) for item in outcomes) == 1, outcomes
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    await coordinator.wait(run.id)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        stored_review = await repositories.reviews.get_by_id(reviews[0].id)
        events = await repositories.events.replay(run.id, limit=500)
    assert stored_review is not None
    resolved_events = [
        event for event in events if event.event_type == "review.resolved"
    ]
    assert len(resolved_events) == 1
    assert resolved_events[0].payload["decision"] == stored_review.decision_payload[
        "decision"
    ]
    await coordinator.close()


@pytest.mark.asyncio
async def test_stream_disconnect_does_not_cancel_but_explicit_cancel_does(runtime, tmp_path) -> None:
    model = BlockingFinishModel()
    coordinator = _coordinator(runtime, tmp_path, _layer(), lambda: model)
    conversation = await coordinator.create_conversation(title="disconnect")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="wait for release",
    )
    await asyncio.wait_for(model.started.wait(), timeout=5)

    app = create_app(ApiService(runtime.unit_of_work, coordinator))
    first_body = asyncio.Event()
    disconnect = asyncio.Event()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/api/v1/runs/{run.id}/events/stream",
        "raw_path": f"/api/v1/runs/{run.id}/events/stream".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    stream_task = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(first_body.wait(), timeout=5)
    disconnect.set()
    await asyncio.wait_for(stream_task, timeout=5)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        running = await repositories.runs.get(run.id)
        assert running is not None and running.status == RunStatus.RUNNING.value

    accepted = await coordinator.request_cancel(run.id, reason="explicit test cancel")
    assert accepted is True
    await asyncio.wait_for(coordinator.wait(run.id), timeout=5)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        cancelled = await repositories.runs.get(run.id)
        rows = await repositories.events.replay(run.id, limit=500)
        assert cancelled is not None and cancelled.status == RunStatus.CANCELLED.value
    assert rows[-1].event_type == "run.cancelled"
    assert model.release.is_set() is False
    await coordinator.close()


@pytest.mark.asyncio
async def test_cross_worker_cancel_is_intent_until_owner_cleans_up(runtime, tmp_path) -> None:
    model = BlockingFinishModel()
    owner = _coordinator(runtime, tmp_path, _layer(), lambda: model)
    requester = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("unused")])),
    )
    conversation = await owner.create_conversation(title="cross worker cancel")
    run = await owner.submit_run(
        conversation_id=conversation.id,
        goal="block until persisted cancellation",
    )
    await asyncio.wait_for(model.started.wait(), timeout=5)

    assert await requester.request_cancel(run.id, reason="remote cancel") is True
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        cancelling = await repositories.runs.get(run.id)
        before_cleanup = await repositories.events.replay(run.id, limit=500)
    assert cancelling is not None and cancelling.status == RunStatus.CANCELLING.value
    assert before_cleanup[-1].event_type == "run.cancel_requested"

    await asyncio.wait_for(owner.wait(run.id), timeout=6)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        cancelled = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert cancelled is not None and cancelled.status == RunStatus.CANCELLED.value
    assert events[-1].event_type == "run.cancelled"
    assert model.release.is_set() is False
    await requester.close()
    await owner.close()


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_execution_before_terminal_failure(runtime, tmp_path) -> None:
    model = BlockingFinishModel()
    coordinator = _coordinator(runtime, tmp_path, _layer(), lambda: model)

    async def failing_heartbeat(run_id, *, attempt, token):
        del run_id, attempt, token
        await model.started.wait()
        raise RunHeartbeatError(
            "token=checker-heartbeat-secret host=/Users/example/heartbeat-private"
        )

    coordinator._heartbeat = failing_heartbeat  # type: ignore[method-assign]
    conversation = await coordinator.create_conversation(title="heartbeat failure")
    run = await coordinator.submit_run(
        conversation_id=conversation.id,
        goal="fail closed when heartbeat fails",
    )
    await asyncio.wait_for(coordinator.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        failed = await repositories.runs.get(run.id)
        tasks = await repositories.tasks.list_for_run(
            run.id,
            conversation_id=conversation.id,
        )
        events = await repositories.events.replay(run.id, limit=500)
    assert failed is not None and failed.status == RunStatus.FAILED.value
    assert failed.worker_id is None and failed.lease_expires_at is None
    assert failed.error_summary == "运行心跳失败；详细诊断仅保留在服务端日志。"
    assert len(tasks) == 1
    assert tasks[0].error_summary == failed.error_summary
    assert events[-1].event_type == "run.failed"
    assert events[-1].payload["error_code"] == "run_heartbeat_failed"
    assert events[-1].payload["error_summary"] == failed.error_summary
    public_payload = json.dumps(
        {
            "run_error": failed.error_summary,
            "task_error": tasks[0].error_summary,
            "events": [event.payload for event in events],
        },
        ensure_ascii=False,
    )
    assert "checker-heartbeat-secret" not in public_payload
    assert "/Users/example/heartbeat-private" not in public_payload
    assert "RunHeartbeatError" not in public_payload
    assert model.release.is_set() is False
    await coordinator.close()


@pytest.mark.asyncio
async def test_expired_lease_recovery_fences_previous_owner(runtime, tmp_path) -> None:
    blocked = BlockingFinishModel()
    first = _coordinator(runtime, tmp_path, _layer(), lambda: blocked)
    conversation = await first.create_conversation(title="lease fencing")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="recover after the lease expires",
    )
    await asyncio.wait_for(blocked.started.wait(), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        leased = await repositories.runs.get_for_update(run.id)
        assert leased is not None
        leased.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("recovered by fenced owner")])),
    )
    assert await second.recover(limit=1) == (run.id,)
    await asyncio.wait_for(second.wait(run.id), timeout=5)
    await asyncio.wait_for(first.wait(run.id), timeout=5)

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        completed = await repositories.runs.get(run.id)
        events = await repositories.events.replay(run.id, limit=500)
    assert completed is not None and completed.status == RunStatus.COMPLETED.value
    assert completed.attempt == 2
    assert sum(event.event_type == "run.completed" for event in events) == 1
    assert blocked.release.is_set() is False
    await second.close()
    await first.close()


@pytest.mark.asyncio
async def test_graceful_shutdown_releases_lease_and_recovery_continues_checkpoint(runtime, tmp_path) -> None:
    blocked = BlockingFinishModel()
    first = _coordinator(runtime, tmp_path, _layer(), lambda: blocked)
    conversation = await first.create_conversation(title="recovery")
    run = await first.submit_run(
        conversation_id=conversation.id,
        goal="recover this run",
    )
    await asyncio.wait_for(blocked.started.wait(), timeout=5)
    await first.close()

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        interrupted = await repositories.runs.get(run.id)
        assert interrupted is not None
        assert interrupted.status == RunStatus.RUNNING.value
        assert interrupted.worker_id is None
        assert interrupted.lease_expires_at is None

    responses = deque([_finish("recovered")])
    second = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(responses),
    )
    assert await second.recover() == (run.id,)
    await second.wait(run.id)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        recovered = await repositories.runs.get(run.id)
        assert recovered is not None and recovered.status == RunStatus.COMPLETED.value
    await second.close()


@pytest.mark.asyncio
async def test_recovery_paginates_past_pending_review_candidates(runtime, tmp_path) -> None:
    review_owner = _coordinator(
        runtime,
        tmp_path,
        _layer(EchoCapability()),
        lambda: SharedScriptModel(deque([_echo("pending review")])),
        policy=DefaultToolPolicy(review_capabilities=frozenset({"echo_tool"})),
    )
    first_conversation = await review_owner.create_conversation(title="review blocker")
    waiting_run = await review_owner.submit_run(
        conversation_id=first_conversation.id,
        goal="remain pending for review",
    )
    await review_owner.wait(waiting_run.id)

    blocked = BlockingFinishModel()
    stager = _coordinator(runtime, tmp_path, _layer(), lambda: blocked)
    second_conversation = await stager.create_conversation(title="recover after page")
    recoverable_run = await stager.submit_run(
        conversation_id=second_conversation.id,
        goal="recover from the next page",
    )
    await asyncio.wait_for(blocked.started.wait(), timeout=5)
    await stager.close()

    recovery = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(deque([_finish("paged recovery completed")])),
    )
    assert await recovery.recover(limit=1) == (recoverable_run.id,)
    await asyncio.wait_for(recovery.wait(recoverable_run.id), timeout=5)
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        waiting = await repositories.runs.get(waiting_run.id)
        completed = await repositories.runs.get(recoverable_run.id)
    assert waiting is not None and waiting.status == RunStatus.REVIEW_REQUIRED.value
    assert completed is not None and completed.status == RunStatus.COMPLETED.value
    await recovery.close()
    await review_owner.close()


@pytest.mark.asyncio
async def test_http_replay_sse_idempotency_and_error_contract(runtime, tmp_path) -> None:
    responses = deque([_finish("api complete")])
    coordinator = _coordinator(
        runtime,
        tmp_path,
        _layer(),
        lambda: SharedScriptModel(responses),
    )
    app = create_app(ApiService(runtime.unit_of_work, coordinator))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"title": "api"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation_id"]

        uploaded = await client.post(
            f"/api/v1/conversations/{conversation_id}/artifacts",
            data={"kind": "dataset"},
            files={
                "file": (
                    "cells.h5ad",
                    b"controlled-dataset-bytes",
                    "application/x-hdf5",
                )
            },
        )
        assert uploaded.status_code == 201
        artifact_id = uploaded.json()["artifact_id"]
        assert uploaded.json()["metadata"] == {"filename": "cells.h5ad"}
        refreshed = await client.get(f"/api/v1/conversations/{conversation_id}")
        assert refreshed.status_code == 200
        assert refreshed.json()["dataset_artifact_id"] == artifact_id
        downloaded = await client.get(f"/api/v1/artifacts/{artifact_id}/content")
        assert downloaded.status_code == 200
        assert downloaded.content == b"controlled-dataset-bytes"
        assert "cells.h5ad" in downloaded.headers["content-disposition"]

        submitted = await client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            headers={"Idempotency-Key": "api-run-1"},
            json={
                "goal": "finish through api",
                "input_artifact_ids": [artifact_id],
            },
        )
        assert submitted.status_code == 202
        run_id = submitted.json()["run"]["run_id"]
        repeated = await client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            headers={"Idempotency-Key": "api-run-1"},
            json={
                "goal": "finish through api",
                "input_artifact_ids": [artifact_id],
            },
        )
        assert repeated.status_code == 202
        assert repeated.json()["run"]["run_id"] == run_id
        conflicting = await client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            headers={"Idempotency-Key": "api-run-1"},
            json={"goal": "a different request"},
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["error"]["code"] == "lifecycle_conflict"
        await coordinator.wait(uuid.UUID(run_id))

        replay = await client.get(f"/api/v1/runs/{run_id}/events")
        assert replay.status_code == 200
        events = replay.json()["events"]
        assert events[-1]["type"] == "run.completed"
        assert all(isinstance(event["sequence"], str) for event in events)

        streamed = await client.get(f"/api/v1/runs/{run_id}/events/stream")
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert "event: run.completed" in streamed.text

        mismatch = await client.get(
            f"/api/v1/runs/{run_id}/events/stream?after_sequence=1",
            headers={"Last-Event-ID": "2"},
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["code"] == "invalid_request"

        invalid = await client.post(
            f"/api/v1/conversations/{conversation_id}/runs",
            json={"goal": ""},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "request_validation_failed"

        missing = await client.get(f"/api/v1/runs/{uuid.uuid4()}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "resource_not_found"
    await coordinator.close()
