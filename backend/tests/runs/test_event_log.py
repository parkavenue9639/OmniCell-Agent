from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from omnicell_agent.runs.event_log import RunEventNotifier
from omnicell_agent.runs.coordinator import (
    RunLifecycleObserver,
    capability_call_id,
    capability_task_id,
    runtime_command_id,
    skill_load_id,
)
from omnicell_agent.runs.events import (
    EventType,
    RuntimeCommandCompletedPayload,
    RuntimeCommandStartedPayload,
    RuntimeOutputPayload,
    SkillLoadCompletedPayload,
    SkillLoadStartedPayload,
    TaskCreatedPayload,
    TaskUpdatedPayload,
)


@pytest.mark.asyncio
async def test_active_follower_keeps_notifier_state_alive() -> None:
    notifier = RunEventNotifier()
    run_id = uuid4()

    async with notifier.follow_run(run_id):
        assert notifier.tracked_run_count == 1
        await notifier.notify(run_id)
        assert notifier.revision(run_id) == 1
        assert notifier.tracked_run_count == 1

    assert notifier.tracked_run_count == 0


@pytest.mark.asyncio
async def test_notifications_without_followers_do_not_accumulate_runs() -> None:
    notifier = RunEventNotifier()

    for _ in range(20):
        await notifier.notify(uuid4())

    assert notifier.tracked_run_count == 0


@pytest.mark.asyncio
async def test_terminal_run_is_reclaimed_after_last_follower_closes() -> None:
    notifier = RunEventNotifier()
    run_id = uuid4()

    async with notifier.follow_run(run_id):
        observed = notifier.revision(run_id)
        waiting = asyncio.create_task(
            notifier.wait_for_change(run_id, observed, timeout_seconds=1)
        )
        await asyncio.sleep(0)

        await notifier.mark_terminal(run_id)

        assert await waiting > observed
        assert notifier.tracked_run_count == 1

    assert notifier.tracked_run_count == 0


@pytest.mark.asyncio
async def test_reclaimed_run_can_create_a_fresh_notifier_generation() -> None:
    notifier = RunEventNotifier()
    run_id = uuid4()

    async with notifier.follow_run(run_id):
        await notifier.mark_terminal(run_id)
        assert notifier.tracked_run_count == 1

    assert notifier.tracked_run_count == 0
    assert notifier.revision(run_id) == 0

    async with notifier.follow_run(run_id):
        assert notifier.tracked_run_count == 1
        assert notifier.revision(run_id) == 0
        observed = notifier.revision(run_id)
        waiting = asyncio.create_task(
            notifier.wait_for_change(run_id, observed, timeout_seconds=1)
        )
        await asyncio.sleep(0)
        await notifier.notify(run_id)
        assert await waiting == 1

    assert notifier.tracked_run_count == 0


def test_lifecycle_observer_projects_explicit_plan_tasks() -> None:
    observer = object.__new__(RunLifecycleObserver)
    observer.run_id = uuid4()
    observer._max_turns = 24
    task_id = uuid4()

    created, refs = observer._project(
        EventType.TASK_CREATED,
        {
            "task_id": str(task_id),
            "tool_call_id": "agent-plan:1:1",
            "title": "检查输入",
            "description": "只读检查当前数据",
            "capability_name": "inspect_single_cell_context",
        },
        f"task:{task_id}:created",
    )
    updated, _ = observer._project(
        EventType.TASK_UPDATED,
        {
            "task_id": str(task_id),
            "status": "completed",
            "summary": "输入已验证",
        },
        f"task:{task_id}:completed",
    )

    assert created == TaskCreatedPayload(
        task_id=task_id,
        title="检查输入",
        description="只读检查当前数据",
        capability_name="inspect_single_cell_context",
    )
    assert updated == TaskUpdatedPayload(
        task_id=task_id,
        status="completed",
        summary="输入已验证",
    )
    assert refs == ()


def test_lifecycle_observer_projects_skill_loading_without_skill_content() -> None:
    observer = object.__new__(RunLifecycleObserver)
    observer.run_id = uuid4()
    observer._max_turns = 24
    tool_call_id = "load-skill-1"
    common = {
        "tool_call_id": tool_call_id,
        "skill_name": "pca-clustering",
        "resource_kind": "reference",
        "resource_name": "quality-control.md",
        "purpose": "validation_rules",
    }
    load_id = skill_load_id(observer.run_id, tool_call_id)

    started, _ = observer._project(
        EventType.SKILL_LOAD_STARTED,
        common,
        "skill:started",
    )
    completed, _ = observer._project(
        EventType.SKILL_LOAD_COMPLETED,
        {
            **common,
            "outcome": "loaded",
            "content_bytes": 2048,
        },
        "skill:completed",
    )

    assert started == SkillLoadStartedPayload(
        skill_load_id=load_id,
        skill_name="pca-clustering",
        resource_kind="reference",
        resource_name="quality-control.md",
        purpose="validation_rules",
    )
    assert completed == SkillLoadCompletedPayload(
        skill_load_id=load_id,
        skill_name="pca-clustering",
        resource_kind="reference",
        resource_name="quality-control.md",
        purpose="validation_rules",
        outcome="loaded",
        content_bytes=2048,
    )
    assert "content" not in completed.model_dump()


def test_lifecycle_observer_rebinds_runtime_transcript_identity() -> None:
    observer = object.__new__(RunLifecycleObserver)
    observer.run_id = uuid4()
    observer._max_turns = 24
    tool_call_id = "tool-call-1"
    local_command_id = uuid4().hex
    common = {
        "capability": "run_pca_clustering",
        "tool_call_id": tool_call_id,
        "attempt": 2,
        "command_id": local_command_id,
    }
    public_command_id = runtime_command_id(
        observer.run_id,
        tool_call_id,
        local_command_id,
    )
    public_call_id = capability_call_id(observer.run_id, tool_call_id)
    public_task_id = capability_task_id(observer.run_id, tool_call_id)

    started, _ = observer._project(
        EventType.RUNTIME_COMMAND_STARTED,
        {
            **common,
            "backend": "local-docker-cli",
            "command": ["python", "/app/data/request.py"],
            "script": "print('hello')",
            "workdir": "/app/data",
            "command_truncated": False,
            "redacted": False,
        },
        "runtime:start",
    )
    output, _ = observer._project(
        EventType.RUNTIME_OUTPUT,
        {
            **common,
            "stream": "stdout",
            "index": 0,
            "chunk": "hello\n",
            "encoding": "utf8",
            "truncated": False,
            "redacted": False,
        },
        "runtime:stdout:0",
    )
    completed, _ = observer._project(
        EventType.RUNTIME_COMMAND_COMPLETED,
        {
            **common,
            "outcome": "completed",
            "exit_code": 0,
            "duration_ms": 25,
            "stdout_observed_bytes": 6,
            "stdout_published_bytes": 6,
            "stderr_observed_bytes": 0,
            "stderr_published_bytes": 0,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "redacted": False,
        },
        "runtime:completed",
    )

    assert started == RuntimeCommandStartedPayload(
        runtime_command_id=public_command_id,
        capability_call_id=public_call_id,
        capability_name="run_pca_clustering",
        task_id=public_task_id,
        attempt=2,
        backend="local-docker-cli",
        command=["python", "/app/data/request.py"],
        code="print('hello')",
        workdir="/app/data",
    )
    assert output == RuntimeOutputPayload(
        runtime_command_id=public_command_id,
        capability_call_id=public_call_id,
        capability_name="run_pca_clustering",
        task_id=public_task_id,
        attempt=2,
        stream="stdout",
        index=0,
        chunk="hello\n",
    )
    assert completed == RuntimeCommandCompletedPayload(
        runtime_command_id=public_command_id,
        capability_call_id=public_call_id,
        capability_name="run_pca_clustering",
        task_id=public_task_id,
        attempt=2,
        outcome="completed",
        exit_code=0,
        duration_ms=25,
        stdout_observed_bytes=6,
        stdout_published_bytes=6,
        stderr_observed_bytes=0,
        stderr_published_bytes=0,
    )
