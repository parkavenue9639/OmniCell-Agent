"""Authoritative run coordination around the Agent Loop."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import UUID, uuid4, uuid5

from sqlalchemy.exc import IntegrityError

from omnicell_agent.agent import (
    AgentLoopFactory,
    AgentOutcome,
    AgentOutcomeStatus,
    CancellationToken,
    RunCancelledError,
)
from omnicell_agent.agent.capability_process import (
    RuntimeCleanupError,
    reap_workspace_runtime_claims,
)
from omnicell_agent.agent.observer import AgentObserver
from omnicell_agent.capabilities.artifacts import (
    ArtifactSizeLimitError,
    ConversationArtifactStore,
)
from omnicell_agent.capabilities.contracts import ArtifactRef
from omnicell_agent.capabilities.errors import PUBLIC_CAPABILITY_FAILURE_SUMMARY
from omnicell_agent.capabilities.registry import CapabilityContext
from omnicell_agent.persistence.database import await_cancellation_safe
from omnicell_agent.persistence.models import (
    Artifact,
    CheckpointAnchor,
    Conversation,
    Review,
    Run,
    RunTask,
)
from omnicell_agent.persistence.unit_of_work import UnitOfWork

from .event_log import RunEventLog, UnitOfWorkFactory
from .events import (
    AgentTurnStartedPayload,
    ArtifactCreatedPayload,
    BudgetExhaustedPayload,
    BudgetKind,
    CapabilityCompletedPayload,
    CapabilityFailedPayload,
    CapabilityProgressPayload,
    CapabilityRetryingPayload,
    CapabilityStartedPayload,
    EventPayload,
    EventType,
    MessageCompletedPayload,
    MessageRole,
    ReviewRequestedPayload,
    ReviewResolvedPayload,
    RuntimeCommandCompletedPayload,
    RuntimeCommandStartedPayload,
    RuntimeOutputPayload,
    RunCancelRequestedPayload,
    RunCancelledPayload,
    RunCompletedPayload,
    RunCreatedPayload,
    RunFailedPayload,
    RunInterruptedPayload,
    RunStartedPayload,
    SkillLoadCompletedPayload,
    SkillLoadFailedPayload,
    SkillLoadStartedPayload,
    TaskCreatedPayload,
    TaskUpdatedPayload,
)
from .status import ReviewDecision, ReviewStatus, RunStatus, TaskStatus, is_terminal_run_status


logger = logging.getLogger(__name__)


_EVENT_NAMESPACE = UUID("d13cd87e-837d-4531-8da4-60597461527a")
_TASK_NAMESPACE = UUID("060a1126-1b14-4df8-b732-22f40848bcb9")
_MESSAGE_NAMESPACE = UUID("56b099a2-4bb5-4fe4-b6b5-acdb514a33e1")
_PUBLIC_RUN_EXECUTION_FAILURE = "运行执行失败；详细诊断仅保留在服务端日志。"
_PUBLIC_RUN_HEARTBEAT_FAILURE = "运行心跳失败；详细诊断仅保留在服务端日志。"


class RunCoordinatorError(RuntimeError):
    pass


class ConversationNotFoundError(RunCoordinatorError):
    pass


class RunNotFoundError(RunCoordinatorError):
    pass


class RunConflictError(RunCoordinatorError):
    pass


class ReviewNotFoundError(RunCoordinatorError):
    pass


class ReviewConflictError(RunCoordinatorError):
    pass


class ArtifactNotFoundError(RunCoordinatorError):
    pass


class ArtifactUploadTooLargeError(RunCoordinatorError):
    pass


class RunLeaseLostError(RunCoordinatorError):
    pass


class RunHeartbeatError(RunCoordinatorError):
    pass


def _safe_upload_filename(value: str | None) -> str:
    leaf = (value or "upload.bin").replace("\\", "/").rsplit("/", 1)[-1].strip()
    leaf = "".join(character for character in leaf if ord(character) >= 32 and character != "\x7f")
    return (leaf or "upload.bin")[:255]


def root_task_id(run_id: UUID) -> UUID:
    return uuid5(_TASK_NAMESPACE, f"{run_id}:root")


def capability_call_id(run_id: UUID, tool_call_id: str) -> UUID:
    return uuid5(_TASK_NAMESPACE, f"{run_id}:call:{tool_call_id}")


def capability_task_id(run_id: UUID, tool_call_id: str) -> UUID:
    return uuid5(_TASK_NAMESPACE, f"{run_id}:task:{tool_call_id}")


def runtime_command_id(
    run_id: UUID,
    tool_call_id: str,
    local_command_id: str,
) -> UUID:
    parsed = UUID(local_command_id)
    return uuid5(
        _TASK_NAMESPACE,
        f"{run_id}:runtime:{tool_call_id}:{parsed.hex}",
    )


def skill_load_id(run_id: UUID, tool_call_id: str) -> UUID:
    return uuid5(_TASK_NAMESPACE, f"{run_id}:skill:{tool_call_id}")


def lifecycle_event_id(run_id: UUID, dedupe_key: str) -> UUID:
    return uuid5(_EVENT_NAMESPACE, f"{run_id}:{dedupe_key}")


def _extract_artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: dict[UUID, ArtifactRef] = {}

    def visit(candidate: Any) -> None:
        if isinstance(candidate, Mapping):
            if {
                "artifact_id",
                "conversation_id",
                "kind",
                "uri",
                "size_bytes",
                "sha256",
            }.issubset(candidate):
                try:
                    reference = ArtifactRef.model_validate(candidate)
                except Exception:
                    pass
                else:
                    found[reference.artifact_id] = reference
                    return
            for item in candidate.values():
                visit(item)
        elif isinstance(candidate, (list, tuple)):
            for item in candidate:
                visit(item)

    visit(value)
    return tuple(found.values())


def _artifact_ref(artifact: Artifact) -> ArtifactRef:
    if artifact.size_bytes is None or artifact.sha256 is None:
        raise ValueError(f"artifact {artifact.id} 缺少 identity 元数据")
    return ArtifactRef(
        artifact_id=artifact.id,
        conversation_id=artifact.conversation_id,
        kind=artifact.kind,
        uri=artifact.uri,
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata=artifact.artifact_metadata,
    )


class RunLifecycleObserver(AgentObserver):
    """Translate internal Agent callbacks into typed, persisted product facts."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        event_log: RunEventLog,
        *,
        run_id: UUID,
        conversation_id: UUID,
        max_turns: int,
        clock: Callable[[], datetime],
        worker_id: str,
        attempt: int,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._event_log = event_log
        self.run_id = run_id
        self.conversation_id = conversation_id
        self._max_turns = max_turns
        self._clock = clock
        self._worker_id = worker_id
        self._attempt = attempt

    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> None:
        public_type = EventType(event_type)
        event_payload, refs = self._project(public_type, payload, dedupe_key)
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(self.run_id)
            if (
                run is None
                or run.worker_id != self._worker_id
                or run.attempt != self._attempt
            ):
                raise RunLeaseLostError(
                    f"run {self.run_id} execution fence 已失效"
                )
            if run.status == RunStatus.CANCELLING.value:
                raise RunCancelledError("run cancellation command 已持久化")
            if run.status != RunStatus.RUNNING.value:
                raise RunLeaseLostError(
                    f"run {self.run_id} 不再处于 running 状态"
                )
            event = await repositories.events.append(
                event_id=lifecycle_event_id(self.run_id, dedupe_key),
                run_id=self.run_id,
                event_type=public_type.value,
                payload=event_payload.model_dump(mode="json"),
            )
            await self._update_task_projection(
                repositories,
                public_type,
                payload,
            )
            for reference in refs:
                if reference.conversation_id != self.conversation_id:
                    raise ValueError("capability 返回了其他 conversation 的 artifact")
                existing = await repositories.artifacts.get(reference.artifact_id)
                if existing is None:
                    await repositories.artifacts.add(
                        Artifact(
                            id=reference.artifact_id,
                            conversation_id=self.conversation_id,
                            run_id=self.run_id,
                            source_event_id=event.id,
                            kind=reference.kind,
                            uri=reference.uri,
                            media_type=reference.media_type,
                            size_bytes=reference.size_bytes,
                            sha256=reference.sha256,
                            artifact_metadata=reference.metadata,
                        )
                    )
                    artifact_payload = ArtifactCreatedPayload(
                        artifact_id=reference.artifact_id,
                        kind=reference.kind,
                        media_type=reference.media_type,
                        size_bytes=reference.size_bytes,
                        sha256=reference.sha256,
                    )
                    await repositories.events.append(
                        event_id=lifecycle_event_id(
                            self.run_id,
                            f"artifact:{reference.artifact_id}:created",
                        ),
                        run_id=self.run_id,
                        event_type=EventType.ARTIFACT_CREATED.value,
                        payload=artifact_payload.model_dump(mode="json"),
                    )
                elif (
                    existing.conversation_id != self.conversation_id
                    or existing.kind != reference.kind
                    or existing.uri != reference.uri
                    or existing.size_bytes != reference.size_bytes
                    or existing.sha256 != reference.sha256
                ):
                    raise ValueError("artifact identity 与数据库登记冲突")
        await self._event_log.notifier.notify(self.run_id)

    def _project(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        dedupe_key: str,
    ) -> tuple[EventPayload, tuple[ArtifactRef, ...]]:
        refs: tuple[ArtifactRef, ...] = ()
        if event_type is EventType.AGENT_TURN_STARTED:
            turn = int(payload["turn"])
            result: EventPayload = AgentTurnStartedPayload(
                turn_index=turn,
                remaining_turns=max(self._max_turns - turn, 0),
            )
        elif event_type is EventType.MESSAGE_COMPLETED:
            result = MessageCompletedPayload(
                message_id=uuid5(_MESSAGE_NAMESPACE, f"{self.run_id}:{dedupe_key}"),
                role=MessageRole(str(payload["role"])),
                content=str(payload.get("content") or "")[:20_000],
                turn_index=(
                    int(payload["turn"])
                    if payload.get("turn") is not None
                    else None
                ),
                has_tool_calls=bool(payload.get("has_tool_calls", False)),
            )
        elif event_type is EventType.TASK_CREATED:
            result = TaskCreatedPayload(
                task_id=UUID(str(payload["task_id"])),
                title=str(payload["title"])[:300],
                description=(
                    str(payload["description"])[:2_000]
                    if payload.get("description") is not None
                    else None
                ),
                capability_name=(
                    str(payload["capability_name"])
                    if payload.get("capability_name") is not None
                    else None
                ),
            )
        elif event_type in {
            EventType.SKILL_LOAD_STARTED,
            EventType.SKILL_LOAD_COMPLETED,
            EventType.SKILL_LOAD_FAILED,
        }:
            load_id = skill_load_id(
                self.run_id,
                str(payload["tool_call_id"]),
            )
            common = {
                "skill_load_id": load_id,
                "skill_name": str(payload["skill_name"]),
                "resource_kind": str(payload["resource_kind"]),
                "resource_name": (
                    str(payload["resource_name"])
                    if payload.get("resource_name") is not None
                    else None
                ),
                "purpose": str(payload["purpose"]),
            }
            if event_type is EventType.SKILL_LOAD_STARTED:
                result = SkillLoadStartedPayload(**common)
            elif event_type is EventType.SKILL_LOAD_COMPLETED:
                result = SkillLoadCompletedPayload(
                    **common,
                    outcome=str(payload["outcome"]),
                    content_bytes=int(payload.get("content_bytes", 0)),
                )
            else:
                result = SkillLoadFailedPayload(
                    **common,
                    error_code="skill_resource_unavailable",
                    error_summary="Skill 资源未能加载；请检查名称或改用其他能力。",
                )
        elif event_type in {
            EventType.CAPABILITY_STARTED,
            EventType.CAPABILITY_COMPLETED,
            EventType.CAPABILITY_FAILED,
            EventType.CAPABILITY_PROGRESS,
            EventType.CAPABILITY_RETRYING,
        }:
            tool_call_id = str(payload["tool_call_id"])
            name = str(payload["capability"])
            call_id = capability_call_id(self.run_id, tool_call_id)
            task_id = capability_task_id(self.run_id, tool_call_id)
            if event_type is EventType.CAPABILITY_STARTED:
                result = CapabilityStartedPayload(
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=1,
                )
            elif event_type is EventType.CAPABILITY_COMPLETED:
                refs = _extract_artifact_refs(payload.get("result"))
                raw_result = payload.get("result")
                raw_status = (
                    str(raw_result.get("status"))
                    if isinstance(raw_result, Mapping)
                    and raw_result.get("status") is not None
                    else None
                )
                result_status = (
                    raw_status if raw_status in {"completed", "aborted"} else None
                )
                result = CapabilityCompletedPayload(
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=int(payload.get("attempt", 1)),
                    result_status=result_status,
                    artifact_ids=[ref.artifact_id for ref in refs],
                    summary=(
                        "工作流已完成"
                        if result_status == "completed"
                        else "能力调用已返回"
                    ),
                )
            elif event_type is EventType.CAPABILITY_FAILED:
                # Observer payloads are internal diagnostics. Never project a raw
                # exception into the replayable API/SSE contract: it may contain
                # provider secrets, host paths, subprocess output or user data.
                result = CapabilityFailedPayload(
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=int(payload.get("attempt", 1)),
                    error_code="capability_execution_failed",
                    error_summary=PUBLIC_CAPABILITY_FAILURE_SUMMARY,
                    retryable=bool(payload.get("retryable", False)),
                )
            elif event_type is EventType.CAPABILITY_RETRYING:
                result = CapabilityRetryingPayload(
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    next_attempt=int(payload.get("attempt", 2)),
                    delay_seconds=0,
                    reason="capability execution requested a bounded retry",
                )
            else:
                result = CapabilityProgressPayload(
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=int(payload.get("attempt", 1)),
                    stage="isolated_execution",
                    current=int(payload["current"]),
                    total=(
                        int(payload["total"])
                        if payload.get("total") is not None
                        else None
                    ),
                    message="能力仍在隔离执行环境中运行",
                )
        elif event_type in {
            EventType.RUNTIME_COMMAND_STARTED,
            EventType.RUNTIME_OUTPUT,
            EventType.RUNTIME_COMMAND_COMPLETED,
        }:
            tool_call_id = str(payload["tool_call_id"])
            name = str(payload["capability"])
            call_id = capability_call_id(self.run_id, tool_call_id)
            task_id = capability_task_id(self.run_id, tool_call_id)
            command_id = runtime_command_id(
                self.run_id,
                tool_call_id,
                str(payload["command_id"]),
            )
            if event_type is EventType.RUNTIME_COMMAND_STARTED:
                result = RuntimeCommandStartedPayload(
                    runtime_command_id=command_id,
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=int(payload.get("attempt", 1)),
                    backend=str(payload["backend"]),
                    command=[str(token) for token in payload["command"]],
                    code=(
                        str(payload["script"])
                        if payload.get("script") is not None
                        else None
                    ),
                    workdir=str(payload["workdir"]),
                    command_truncated=bool(
                        payload.get("command_truncated", False)
                    ),
                    redacted=bool(payload.get("redacted", False)),
                )
            elif event_type is EventType.RUNTIME_OUTPUT:
                result = RuntimeOutputPayload(
                    runtime_command_id=command_id,
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=int(payload.get("attempt", 1)),
                    stream=str(payload["stream"]),
                    index=int(payload["index"]),
                    chunk=str(payload["chunk"]),
                    encoding=str(payload.get("encoding") or "utf8"),
                    truncated=bool(payload.get("truncated", False)),
                    redacted=bool(payload.get("redacted", False)),
                )
            else:
                result = RuntimeCommandCompletedPayload(
                    runtime_command_id=command_id,
                    capability_call_id=call_id,
                    capability_name=name,
                    task_id=task_id,
                    attempt=int(payload.get("attempt", 1)),
                    outcome=str(payload["outcome"]),
                    exit_code=(
                        int(payload["exit_code"])
                        if payload.get("exit_code") is not None
                        else None
                    ),
                    duration_ms=int(payload["duration_ms"]),
                    stdout_observed_bytes=int(
                        payload.get("stdout_observed_bytes", 0)
                    ),
                    stdout_published_bytes=int(
                        payload.get("stdout_published_bytes", 0)
                    ),
                    stderr_observed_bytes=int(
                        payload.get("stderr_observed_bytes", 0)
                    ),
                    stderr_published_bytes=int(
                        payload.get("stderr_published_bytes", 0)
                    ),
                    stdout_truncated=bool(
                        payload.get("stdout_truncated", False)
                    ),
                    stderr_truncated=bool(
                        payload.get("stderr_truncated", False)
                    ),
                    redacted=bool(payload.get("redacted", False)),
                )
        elif event_type is EventType.TASK_UPDATED:
            status = TaskStatus(str(payload["status"]))
            result = TaskUpdatedPayload(
                task_id=(
                    UUID(str(payload["task_id"]))
                    if payload.get("task_id") is not None
                    else root_task_id(self.run_id)
                ),
                status=status,
                summary=(
                    str(payload["summary"])[:2_000]
                    if payload.get("summary") is not None
                    else None
                ),
            )
        elif event_type is EventType.BUDGET_EXHAUSTED:
            raw_reason = str(payload.get("reason") or "turns")
            kind = {
                "turns": BudgetKind.TURN,
                "wall_clock": BudgetKind.WALL_TIME,
                "model_calls": BudgetKind.MODEL_CALL,
                "tool_calls": BudgetKind.CAPABILITY_CALL,
            }.get(raw_reason, BudgetKind.RETRY)
            result = BudgetExhaustedPayload(
                budget=kind,
                limit=float(payload.get("limit", 0)),
                used=float(payload.get("used", 0)),
                unit=str(payload.get("unit") or "count"),
            )
        else:
            raise ValueError(f"Agent observer 不接受 lifecycle event：{event_type.value}")
        return result, refs

    async def _update_task_projection(
        self,
        repositories: Any,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        now = self._clock()
        if event_type is EventType.TASK_CREATED:
            task_id = UUID(str(payload["task_id"]))
            task = await repositories.tasks.get(
                task_id,
                conversation_id=self.conversation_id,
                run_id=self.run_id,
            )
            if task is None:
                await repositories.tasks.add(
                    RunTask(
                        id=task_id,
                        conversation_id=self.conversation_id,
                        run_id=self.run_id,
                        tool_call_id=str(payload["tool_call_id"])[:255],
                        capability_name=(
                            str(payload["capability_name"])
                            if payload.get("capability_name") is not None
                            else "agent_plan"
                        ),
                        status=TaskStatus.PENDING.value,
                        request_payload={
                            "title": str(payload["title"])[:300],
                            "description": (
                                str(payload["description"])[:2_000]
                                if payload.get("description") is not None
                                else None
                            ),
                        },
                    )
                )
        elif event_type in {
            EventType.CAPABILITY_STARTED,
            EventType.CAPABILITY_COMPLETED,
            EventType.CAPABILITY_FAILED,
        }:
            tool_call_id = str(payload["tool_call_id"])
            task = await repositories.tasks.get_by_tool_call(
                conversation_id=self.conversation_id,
                run_id=self.run_id,
                tool_call_id=tool_call_id,
            )
            if task is None:
                task = await repositories.tasks.add(
                    RunTask(
                        id=capability_task_id(self.run_id, tool_call_id),
                        conversation_id=self.conversation_id,
                        run_id=self.run_id,
                        tool_call_id=tool_call_id,
                        capability_name=str(payload["capability"]),
                        status=TaskStatus.IN_PROGRESS.value,
                        request_payload={},
                        started_at=now,
                    )
                )
            elif event_type is EventType.CAPABILITY_STARTED:
                task.status = TaskStatus.IN_PROGRESS.value
                task.started_at = task.started_at or now
            if event_type is EventType.CAPABILITY_COMPLETED:
                task.status = TaskStatus.COMPLETED.value
                task.finished_at = now
            elif event_type is EventType.CAPABILITY_FAILED:
                task.status = TaskStatus.FAILED.value
                task.error_summary = PUBLIC_CAPABILITY_FAILURE_SUMMARY
                task.finished_at = now
        elif event_type is EventType.TASK_UPDATED:
            task_id = (
                UUID(str(payload["task_id"]))
                if payload.get("task_id") is not None
                else root_task_id(self.run_id)
            )
            task = await repositories.tasks.get(
                task_id,
                conversation_id=self.conversation_id,
                run_id=self.run_id,
            )
            if task is not None:
                task.status = TaskStatus(str(payload["status"])).value
                if task.status == TaskStatus.IN_PROGRESS.value:
                    task.started_at = task.started_at or now
                if task.status in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    task.finished_at = now
                if task.status == TaskStatus.FAILED.value:
                    task.error_summary = (
                        str(payload["summary"])[:2_000]
                        if payload.get("summary") is not None
                        else None
                    )


class RunCoordinator:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        checkpointer: Any,
        agent_factory: AgentLoopFactory,
        workspace_root: str | Path,
        event_log: RunEventLog | None = None,
        worker_id: str | None = None,
        lease_duration: timedelta = timedelta(minutes=2),
        clock: Any | None = None,
    ) -> None:
        if lease_duration <= timedelta(seconds=20):
            raise ValueError("lease_duration 必须大于 20 秒，以预留隔离进程回收窗口")
        self._unit_of_work = unit_of_work
        self._checkpointer = checkpointer
        self._agent_factory = agent_factory
        self._workspace_root = Path(workspace_root).expanduser().resolve(strict=False)
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self.event_log = event_log or RunEventLog(unit_of_work)
        self.worker_id = (worker_id or f"local-{os.getpid()}-{uuid4().hex[:12]}")[:255]
        self._lease_duration = lease_duration
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._tokens: dict[UUID, CancellationToken] = {}

    async def create_conversation(
        self,
        *,
        title: str | None = None,
    ) -> Conversation:
        if title is not None and (not title.strip() or len(title) > 300):
            raise ValueError("conversation title 长度必须在 1..300 之间")
        conversation_id = uuid4()
        workspace = self._workspace_root / str(conversation_id)
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=False)
        conversation = Conversation(
            id=conversation_id,
            title=title.strip() if title else None,
            workspace_uri=f"workspace://conversations/{conversation_id}",
        )
        try:
            async with self._unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                await repositories.conversations.add(conversation)
        except BaseException:
            # The directory is new and still empty because artifact upload is a
            # separate command; remove only this exact leaf on failed creation.
            try:
                await asyncio.to_thread(workspace.rmdir)
            except OSError:
                pass
            raise
        return conversation

    async def import_artifact(
        self,
        conversation_id: UUID,
        *,
        source: BinaryIO,
        filename: str | None,
        kind: str,
        media_type: str | None = None,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> Artifact:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", kind):
            raise ValueError("artifact kind 格式非法")
        if max_bytes <= 0:
            raise ValueError("artifact 上传上限必须为正数")
        normalized_media_type = media_type.strip() if media_type else None
        if normalized_media_type and (
            len(normalized_media_type) > 255
            or any(ord(character) < 32 for character in normalized_media_type)
        ):
            raise ValueError("artifact media type 格式非法")

        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            if await repositories.conversations.get(conversation_id) is None:
                raise ConversationNotFoundError(str(conversation_id))

        public_filename = _safe_upload_filename(filename)
        suffix = Path(public_filename).suffix[:32]
        workspace = self._workspace_root / str(conversation_id)
        store = ConversationArtifactStore(conversation_id, workspace)
        relative_target = f"uploads/{uuid4().hex}{suffix}"
        reference: ArtifactRef | None = None
        try:
            reference = await asyncio.to_thread(
                store.import_stream,
                relative_target,
                source,
                max_bytes=max_bytes,
                kind=kind,
                media_type=normalized_media_type,
                metadata={"filename": public_filename},
            )
            artifact = Artifact(
                id=reference.artifact_id,
                conversation_id=conversation_id,
                run_id=None,
                source_event_id=None,
                kind=reference.kind,
                uri=reference.uri,
                media_type=reference.media_type,
                size_bytes=reference.size_bytes,
                sha256=reference.sha256,
                artifact_metadata=reference.metadata,
            )
            async with self._unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                conversation = await repositories.conversations.get(conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError(str(conversation_id))
                await repositories.artifacts.add(artifact)
                if kind == "dataset":
                    conversation.dataset_uri = reference.uri
            return artifact
        except ArtifactSizeLimitError as exc:
            raise ArtifactUploadTooLargeError(str(exc)) from exc
        except BaseException:
            if reference is not None:
                try:
                    await asyncio.to_thread(store.remove, reference)
                except OSError:
                    logger.warning(
                        "failed to remove incomplete artifact upload",
                        exc_info=True,
                    )
            raise

    async def open_artifact(self, artifact_id: UUID) -> tuple[Artifact, BinaryIO]:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            artifact = await repositories.artifacts.get(artifact_id)
        if artifact is None or artifact.size_bytes is None or artifact.sha256 is None:
            raise ArtifactNotFoundError(str(artifact_id))
        store = ConversationArtifactStore(
            artifact.conversation_id,
            self._workspace_root / str(artifact.conversation_id),
        )
        reference = ArtifactRef(
            artifact_id=artifact.id,
            conversation_id=artifact.conversation_id,
            kind=artifact.kind,
            uri=artifact.uri,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            metadata=artifact.artifact_metadata,
        )
        await asyncio.to_thread(store.register_trusted, reference)
        return artifact, await asyncio.to_thread(store.open_verified, reference)

    async def submit_run(
        self,
        *,
        conversation_id: UUID,
        goal: str,
        input_artifact_ids: list[UUID] | tuple[UUID, ...] = (),
        request_key: str | None = None,
    ) -> Run:
        normalized_goal = goal.strip()
        if not normalized_goal or len(normalized_goal) > 20_000:
            raise ValueError("run goal 长度必须在 1..20,000 之间")
        if len(input_artifact_ids) > 100:
            raise ValueError("input artifacts 不能超过 100 个")
        normalized_artifact_ids = tuple(input_artifact_ids)
        if len(set(normalized_artifact_ids)) != len(normalized_artifact_ids):
            raise ValueError("input artifacts 不允许重复")
        normalized_key = request_key.strip() if request_key else None
        run_id = uuid4()
        task_id = root_task_id(run_id)
        created = False
        try:
            async with self._unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                conversation = await repositories.conversations.get(conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError(str(conversation_id))
                if normalized_key:
                    existing = await repositories.runs.get_by_request_key(
                        conversation_id=conversation_id,
                        request_key=normalized_key,
                    )
                    if existing is not None:
                        expected_payload = {
                            "goal": normalized_goal,
                            "input_artifact_ids": [
                                str(item) for item in normalized_artifact_ids
                            ],
                        }
                        if existing.request_payload != expected_payload:
                            raise RunConflictError(
                                "request_key 已用于不同的 run request"
                            )
                        return existing
                active = await repositories.runs.get_active_for_conversation(conversation_id)
                if active is not None:
                    raise RunConflictError(
                        f"conversation 已有活跃 run：{active.id}"
                    )
                for artifact_id in normalized_artifact_ids:
                    artifact = await repositories.artifacts.get_for_conversation(
                        artifact_id,
                        conversation_id=conversation_id,
                    )
                    if artifact is None:
                        raise ValueError(f"input artifact 不属于 conversation：{artifact_id}")
                run = await repositories.runs.add(
                    Run(
                        id=run_id,
                        conversation_id=conversation_id,
                        request_key=normalized_key,
                        status=RunStatus.PENDING.value,
                        request_payload={
                            "goal": normalized_goal,
                            "input_artifact_ids": [
                                str(item) for item in normalized_artifact_ids
                            ],
                        },
                        checkpoint_thread_id=f"conversation:{conversation_id}",
                    )
                )
                await repositories.tasks.add(
                    RunTask(
                        id=task_id,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_call_id=f"agent-goal:{run_id}",
                        capability_name="agent_goal",
                        status=TaskStatus.PENDING.value,
                        request_payload={"goal": normalized_goal},
                    )
                )
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "run:created"),
                    run_id=run_id,
                    event_type=EventType.RUN_CREATED.value,
                    payload=RunCreatedPayload().model_dump(mode="json"),
                )
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "message:user"),
                    run_id=run_id,
                    event_type=EventType.MESSAGE_COMPLETED.value,
                    payload=MessageCompletedPayload(
                        message_id=uuid5(_MESSAGE_NAMESPACE, f"{run_id}:user"),
                        role=MessageRole.USER,
                        content=normalized_goal,
                    ).model_dump(mode="json"),
                )
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "task:root:created"),
                    run_id=run_id,
                    event_type=EventType.TASK_CREATED.value,
                    payload=TaskCreatedPayload(
                        task_id=task_id,
                        title=normalized_goal[:300],
                        description=normalized_goal[:2_000],
                        capability_name="agent_goal",
                    ).model_dump(mode="json"),
                )
                created = True
        except IntegrityError as exc:
            if normalized_key:
                async with self._unit_of_work() as unit_of_work:
                    repositories = unit_of_work.repositories
                    assert repositories is not None
                    existing = await repositories.runs.get_by_request_key(
                        conversation_id=conversation_id,
                        request_key=normalized_key,
                    )
                    if existing is not None:
                        expected_payload = {
                            "goal": normalized_goal,
                            "input_artifact_ids": [
                                str(item) for item in normalized_artifact_ids
                            ],
                        }
                        if existing.request_payload != expected_payload:
                            raise RunConflictError(
                                "request_key 已用于不同的 run request"
                            )
                        return existing
            raise RunConflictError("conversation run 并发冲突") from exc
        if created:
            await self.event_log.notifier.notify(run_id)
            self._schedule(run_id, self._execute_start(run_id))
        return run

    async def request_cancel(self, run_id: UUID, *, reason: str | None = None) -> bool:
        normalized_reason = reason.strip()[:2_000] if reason and reason.strip() else None
        accepted = False
        owner_has_valid_lease = False
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            if run is None:
                raise RunNotFoundError(str(run_id))
            if is_terminal_run_status(run.status):
                return False
            now = self._clock()
            owner_has_valid_lease = bool(
                run.worker_id
                and run.lease_expires_at is not None
                and run.lease_expires_at > now
            )
            if run.status != RunStatus.CANCELLING.value:
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "run:cancel-requested"),
                    run_id=run_id,
                    event_type=EventType.RUN_CANCEL_REQUESTED.value,
                    payload=RunCancelRequestedPayload(
                        reason=normalized_reason
                    ).model_dump(mode="json"),
                    run_status=RunStatus.CANCELLING,
                )
                accepted = True
            reviews = await repositories.reviews.list_for_run(
                run_id,
                conversation_id=run.conversation_id,
                limit=500,
            )
            for review in reviews:
                if review.status != ReviewStatus.PENDING.value:
                    continue
                review.status = ReviewStatus.CANCELLED.value
                review.decided_at = self._clock()
                task = await repositories.tasks.get_by_tool_call(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    tool_call_id=review.tool_call_id,
                )
                if task is not None:
                    task.status = TaskStatus.CANCELLED.value
                    task.finished_at = self._clock()
                    await repositories.events.append(
                        event_id=lifecycle_event_id(
                            run_id, f"task:{task.id}:cancelled"
                        ),
                        run_id=run_id,
                        event_type=EventType.TASK_UPDATED.value,
                        payload=TaskUpdatedPayload(
                            task_id=task.id,
                            status=TaskStatus.CANCELLED,
                            summary="review cancelled with run",
                        ).model_dump(mode="json"),
                    )
                await repositories.events.append(
                    event_id=lifecycle_event_id(
                        run_id, f"review:{review.id}:cancelled"
                    ),
                    run_id=run_id,
                    event_type=EventType.REVIEW_RESOLVED.value,
                    payload=ReviewResolvedPayload(
                        review_id=review.id,
                        status=ReviewStatus.CANCELLED,
                    ).model_dump(mode="json"),
                )
        await self.event_log.notifier.notify(run_id)
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel(normalized_reason or "run cancellation requested")
            try:
                await token.propagate()
            except Exception:
                logger.exception("run runtime cancellation propagation failed")
        if run_id not in self._tasks and not owner_has_valid_lease:
            self._schedule(
                run_id,
                self._execute_cancel_cleanup(
                    run_id,
                    reason=normalized_reason,
                ),
            )
        return accepted

    async def resolve_review(
        self,
        review_id: UUID,
        *,
        decision: ReviewDecision,
        comment: str | None = None,
    ) -> Review:
        normalized_comment = comment.strip()[:5_000] if comment and comment.strip() else None
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            review_hint = await repositories.reviews.get_by_id(review_id)
            if review_hint is None:
                raise ReviewNotFoundError(str(review_id))
            run = await repositories.runs.get_for_update(review_hint.run_id)
            if run is None:
                raise ReviewConflictError("review 对应的 run 不存在")
            review = await repositories.reviews.get_by_id_for_update(review_id)
            if review is None or review.run_id != run.id:
                raise ReviewConflictError("review identity 在决策期间发生变化")
            if review.status != ReviewStatus.PENDING.value:
                expected = (
                    ReviewStatus.APPROVED
                    if decision is ReviewDecision.APPROVE
                    else ReviewStatus.REJECTED
                )
                if review.status == expected.value and review.decision_payload == {
                    "decision": decision.value,
                    "comment": normalized_comment,
                }:
                    return review
                raise ReviewConflictError("review 已经被其他决定解决")
            if run.status != RunStatus.REVIEW_REQUIRED.value:
                raise ReviewConflictError("run 不处于可审核状态")
            status = (
                ReviewStatus.APPROVED
                if decision is ReviewDecision.APPROVE
                else ReviewStatus.REJECTED
            )
            review.status = status.value
            review.decision_payload = {
                "decision": decision.value,
                "comment": normalized_comment,
            }
            review.decided_at = self._clock()
            if decision is ReviewDecision.REJECT:
                task = await repositories.tasks.get_by_tool_call(
                    conversation_id=review.conversation_id,
                    run_id=review.run_id,
                    tool_call_id=review.tool_call_id,
                )
                if task is not None:
                    task.status = TaskStatus.CANCELLED.value
                    task.finished_at = self._clock()
                    await repositories.events.append(
                        event_id=lifecycle_event_id(
                            review.run_id, f"task:{task.id}:review-rejected"
                        ),
                        run_id=review.run_id,
                        event_type=EventType.TASK_UPDATED.value,
                        payload=TaskUpdatedPayload(
                            task_id=task.id,
                            status=TaskStatus.CANCELLED,
                            summary="human review rejected capability",
                        ).model_dump(mode="json"),
                    )
            await repositories.events.append(
                event_id=lifecycle_event_id(
                    run.id,
                    f"review:{review.id}:resolved",
                ),
                run_id=run.id,
                event_type=EventType.REVIEW_RESOLVED.value,
                payload=ReviewResolvedPayload(
                    review_id=review.id,
                    status=status,
                    decision=decision,
                    comment=normalized_comment,
                ).model_dump(mode="json"),
            )
            run_id = run.id
        await self.event_log.notifier.notify(run_id)
        self._schedule(
            run_id,
            self._execute_resume(
                run_id,
                review_id=review_id,
                decision=decision,
                comment=normalized_comment,
            ),
        )
        return review

    async def recover(self, *, limit: int = 100) -> tuple[UUID, ...]:
        if not 1 <= limit <= 5_000:
            raise ValueError("recovery page limit 必须在 1..5000 之间")
        scheduled: list[UUID] = []
        after_created_at: datetime | None = None
        after_id: UUID | None = None
        while True:
            async with self._unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                candidates = tuple(
                    await repositories.runs.list_recoverable(
                        at=self._clock(),
                        limit=limit,
                        after_created_at=after_created_at,
                        after_id=after_id,
                    )
                )
                review_map: dict[UUID, tuple[Review, ...]] = {}
                for run in candidates:
                    if run.status == RunStatus.REVIEW_REQUIRED.value:
                        review_map[run.id] = tuple(
                            await repositories.reviews.list_for_run(
                                run.id,
                                conversation_id=run.conversation_id,
                                limit=500,
                            )
                        )
            if not candidates:
                break
            after_created_at = candidates[-1].created_at
            after_id = candidates[-1].id
            for run in candidates:
                if run.id in self._tasks:
                    continue
                if run.status == RunStatus.PENDING.value:
                    coroutine = self._execute_start(run.id)
                elif run.status == RunStatus.CANCELLING.value:
                    coroutine = self._execute_cancel_cleanup(run.id)
                elif run.status == RunStatus.REVIEW_REQUIRED.value:
                    resolved = next(
                        (
                            review
                            for review in reversed(review_map.get(run.id, ()))
                            if review.status
                            in {
                                ReviewStatus.APPROVED.value,
                                ReviewStatus.REJECTED.value,
                            }
                        ),
                        None,
                    )
                    if resolved is None:
                        continue
                    decision = ReviewDecision(resolved.decision_payload["decision"])
                    coroutine = self._execute_resume(
                        run.id,
                        review_id=resolved.id,
                        decision=decision,
                        comment=resolved.decision_payload.get("comment"),
                    )
                else:
                    coroutine = self._execute_continue(run.id)
                self._schedule(run.id, coroutine)
                scheduled.append(run.id)
            if len(candidates) < limit:
                break
        return tuple(scheduled)

    async def resume_run(
        self,
        run_id: UUID,
        *,
        review_id: UUID | None = None,
    ) -> bool:
        if run_id in self._tasks:
            return False
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get(run_id)
            if run is None:
                raise RunNotFoundError(str(run_id))
            status = RunStatus(run.status)
            if is_terminal_run_status(status) or status is RunStatus.CANCELLING:
                return False
            review: Review | None = None
            if status is RunStatus.REVIEW_REQUIRED:
                reviews = await repositories.reviews.list_for_run(
                    run_id,
                    conversation_id=run.conversation_id,
                    limit=500,
                )
                if review_id is not None:
                    review = next((item for item in reviews if item.id == review_id), None)
                    if review is None:
                        raise ReviewNotFoundError(str(review_id))
                else:
                    review = reviews[-1] if reviews else None
                if review is None or review.status == ReviewStatus.PENDING.value:
                    raise ReviewConflictError("pending review 必须先提交 decision")
                if review.status == ReviewStatus.CANCELLED.value:
                    return False
        if status is RunStatus.PENDING:
            coroutine = self._execute_start(run_id)
        elif status is RunStatus.REVIEW_REQUIRED:
            assert review is not None
            decision = ReviewDecision(review.decision_payload["decision"])
            coroutine = self._execute_resume(
                run_id,
                review_id=review.id,
                decision=decision,
                comment=review.decision_payload.get("comment"),
            )
        else:
            coroutine = self._execute_continue(run_id)
        self._schedule(run_id, coroutine)
        return True

    async def wait(self, run_id: UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)

    async def close(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _require_execution_owner(
        self,
        run: Run | None,
        *,
        run_id: UUID,
        expected_attempt: int,
    ) -> Run:
        if (
            run is None
            or run.worker_id != self.worker_id
            or run.attempt != expected_attempt
        ):
            raise RunLeaseLostError(f"run {run_id} execution fence 已失效")
        return run

    def _schedule(self, run_id: UUID, coroutine: Any) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            coroutine.close()
            raise RunConflictError(f"run {run_id} 已在当前 worker 执行")
        task = asyncio.create_task(coroutine, name=f"omnicell-run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda completed: self._task_done(run_id, completed))

    def _task_done(self, run_id: UUID, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
        try:
            failure = task.exception()
        except asyncio.CancelledError:
            return
        if failure is not None:
            logger.error(
                "run background task ended with an unhandled error",
                exc_info=(type(failure), failure, failure.__traceback__),
            )

    async def _execute_start(self, run_id: UUID) -> None:
        await self._execute(run_id, mode="start")

    async def _execute_continue(self, run_id: UUID) -> None:
        await self._execute(run_id, mode="continue")

    async def _execute_cancel_cleanup(
        self,
        run_id: UUID,
        *,
        reason: str | None = None,
    ) -> None:
        await self._execute(run_id, mode="cancel_cleanup", comment=reason)

    async def _execute_resume(
        self,
        run_id: UUID,
        *,
        review_id: UUID,
        decision: ReviewDecision,
        comment: str | None,
    ) -> None:
        await self._execute(
            run_id,
            mode="resume",
            review_id=review_id,
            decision=decision,
            comment=comment,
        )

    async def _execute(
        self,
        run_id: UUID,
        *,
        mode: str,
        review_id: UUID | None = None,
        decision: ReviewDecision | None = None,
        comment: str | None = None,
    ) -> None:
        token = CancellationToken()
        token.enable_lease_watchdog(
            timeout_seconds=max(self._lease_duration.total_seconds() - 15, 5)
        )
        self._tokens[run_id] = token
        heartbeat: asyncio.Task[None] | None = None
        attempt: int | None = None
        runtime_cleanup_gate_passed = False
        try:
            (
                run,
                conversation,
                goal,
                artifacts,
                input_artifacts,
                attempt,
            ) = await self._claim_and_load(
                run_id,
                mode=mode,
                review_id=review_id,
            )
            token.renew_lease()
            heartbeat_coro = (
                self._heartbeat(
                    run_id,
                    attempt=attempt,
                    token=token,
                    observe_cancellation=False,
                )
                if mode == "cancel_cleanup"
                else self._heartbeat(run_id, attempt=attempt, token=token)
            )
            heartbeat = asyncio.create_task(heartbeat_coro)
            workspace = self._workspace_root / str(conversation.id)
            store = ConversationArtifactStore(
                conversation.id,
                workspace,
            )
            # A process owner may have died after Docker accepted a container
            # but before normal cleanup. Only the new DB lease holder may reap
            # exact, label-verified claims for this conversation workspace.
            await reap_workspace_runtime_claims(workspace)
            runtime_cleanup_gate_passed = True
            if mode == "cancel_cleanup":
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                heartbeat = None
                await await_cancellation_safe(
                    self._finalize_cancelled(
                        run.id,
                        comment or "recovered cancellation",
                        expected_attempt=attempt,
                    )
                )
                return
            await self._mark_execution_started(
                run.id,
                mode=mode,
                review_id=review_id,
                expected_attempt=attempt,
            )
            for artifact in artifacts:
                await asyncio.to_thread(
                    store.register_trusted,
                    _artifact_ref(artifact),
                )
            observer = RunLifecycleObserver(
                self._unit_of_work,
                self.event_log,
                run_id=run.id,
                conversation_id=conversation.id,
                max_turns=self._agent_factory.config.max_turns,
                clock=self._clock,
                worker_id=self.worker_id,
                attempt=attempt,
            )
            execution = self._agent_factory.create(
                run_id=run.id,
                conversation_id=conversation.id,
                capability_context=CapabilityContext(
                    conversation_id=conversation.id,
                    artifacts=store,
                ),
                checkpointer=self._checkpointer,
                input_artifacts=input_artifacts,
                cancellation=token,
                observer=observer,
            )
            if mode == "start":
                execution_coro = execution.start(goal)
            elif mode == "resume":
                if review_id is None or decision is None:
                    raise RuntimeError("resume 缺少 review decision")
                execution_coro = execution.resume_review(
                    review_id,
                    decision,
                    comment=comment,
                )
            else:
                execution_coro = await self._reconcile_recovery_execution(
                    execution,
                    run_id=run.id,
                    conversation_id=conversation.id,
                    goal=goal,
                )
            outcome = await self._await_execution(
                execution_coro,
                heartbeat=heartbeat,
                token=token,
            )
            heartbeat = None
            await await_cancellation_safe(
                self._handle_outcome(
                    run.id,
                    conversation.id,
                    execution,
                    outcome,
                    expected_attempt=attempt,
                )
            )
        except RuntimeCleanupError:
            # The exact runtime is not yet quiescent. Keep this run non-terminal
            # and retain the current lease until expiry; recovery must retry the
            # durable claim before executing or finalizing another attempt.
            return
        except RunCancelledError as exc:
            if attempt is not None:
                await await_cancellation_safe(
                    self._finalize_cancelled(
                        run_id,
                        str(exc),
                        expected_attempt=attempt,
                    )
                )
        except RunLeaseLostError:
            return
        except RunHeartbeatError as exc:
            if attempt is not None:
                logger.error(
                    "run heartbeat failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={"run_id": str(run_id)},
                )
                try:
                    await await_cancellation_safe(
                        self._finalize_failed(
                            run_id,
                            error_code="run_heartbeat_failed",
                            summary=_PUBLIC_RUN_HEARTBEAT_FAILURE,
                            expected_attempt=attempt,
                        )
                    )
                except RunLeaseLostError:
                    return
        except asyncio.CancelledError:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                heartbeat = None
            if attempt is not None and runtime_cleanup_gate_passed:
                await await_cancellation_safe(
                    self._release_lease(run_id, expected_attempt=attempt)
                )
            raise
        except RunConflictError:
            return
        except Exception as exc:
            if attempt is not None:
                logger.error(
                    "run execution failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={"run_id": str(run_id)},
                )
                await await_cancellation_safe(
                    self._finalize_failed(
                        run_id,
                        error_code="run_execution_failed",
                        summary=_PUBLIC_RUN_EXECUTION_FAILURE,
                        expected_attempt=attempt,
                    )
                )
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self._tokens.pop(run_id, None)

    async def _claim_and_load(
        self,
        run_id: UUID,
        *,
        mode: str,
        review_id: UUID | None,
    ) -> tuple[
        Run,
        Conversation,
        str,
        tuple[Artifact, ...],
        tuple[ArtifactRef, ...],
        int,
    ]:
        now = self._clock()
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            if run is None:
                raise RunNotFoundError(str(run_id))
            if is_terminal_run_status(run.status):
                raise RunConflictError("不能执行终态 run")
            if (
                mode == "cancel_cleanup"
                and run.status != RunStatus.CANCELLING.value
            ):
                raise RunConflictError("cleanup claim 仅适用于 cancelling run")
            if (
                run.worker_id
                and run.worker_id != self.worker_id
                and run.lease_expires_at is not None
                and run.lease_expires_at > now
            ):
                raise RunConflictError("run 仍由其他 worker 持有有效 lease")
            run.worker_id = self.worker_id
            run.attempt += 1
            run.last_heartbeat_at = now
            run.lease_expires_at = now + self._lease_duration
            conversation = await repositories.conversations.get(run.conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(run.conversation_id))
            goal = str(run.request_payload.get("goal") or "").strip()
            if not goal:
                raise RuntimeError("run request 缺少 goal")
            raw_artifact_ids = run.request_payload.get("input_artifact_ids", [])
            if not isinstance(raw_artifact_ids, list) or len(raw_artifact_ids) > 100:
                raise RuntimeError("run request 的 input artifacts 非法")
            try:
                selected_ids = tuple(
                    UUID(str(raw_id)) for raw_id in raw_artifact_ids
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError("run request 的 input artifact identity 非法") from exc
            if len(set(selected_ids)) != len(selected_ids):
                raise RuntimeError("run request 的 input artifacts 不允许重复")
            selected_artifacts = tuple(
                await repositories.artifacts.get_many_for_conversation(
                    selected_ids,
                    conversation_id=conversation.id,
                )
            )
            artifact_by_id = {
                artifact.id: artifact for artifact in selected_artifacts
            }
            input_artifacts: list[ArtifactRef] = []
            hydration_artifacts: list[Artifact] = []
            hydrated_ids: set[UUID] = set()
            for artifact_id in selected_ids:
                artifact = artifact_by_id.get(artifact_id)
                if artifact is None:
                    raise RuntimeError(
                        f"run input artifact 不再属于 conversation：{artifact_id}"
                    )
                input_artifacts.append(_artifact_ref(artifact))
                hydration_artifacts.append(artifact)
                hydrated_ids.add(artifact.id)
            current_run_artifacts = await repositories.artifacts.list_for_run_context(
                run.id,
                conversation_id=conversation.id,
            )
            for artifact in current_run_artifacts:
                if artifact.id not in hydrated_ids:
                    hydration_artifacts.append(artifact)
                    hydrated_ids.add(artifact.id)
            attempt = run.attempt
        return (
            run,
            conversation,
            goal,
            tuple(hydration_artifacts),
            tuple(input_artifacts),
            attempt,
        )

    async def _mark_execution_started(
        self,
        run_id: UUID,
        *,
        mode: str,
        review_id: UUID | None,
        expected_attempt: int,
    ) -> None:
        """Publish ``run.started`` only after the runtime cleanup gate passes."""

        notify = False
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            if run is None:
                raise RunNotFoundError(str(run_id))
            if run.worker_id != self.worker_id or run.attempt != expected_attempt:
                raise RunLeaseLostError(f"run {run_id} attempt fence 已失效")
            if run.status == RunStatus.CANCELLING.value:
                raise RunCancelledError("run cancellation requested")
            if is_terminal_run_status(run.status):
                raise RunConflictError("不能启动终态 run")
            if mode == "start":
                if run.status != RunStatus.PENDING.value:
                    raise RunConflictError("start claim 必须保持 pending 至清理门禁通过")
            elif mode == "resume":
                if review_id is None:
                    raise RuntimeError("resume 缺少 review identity")
                if run.status != RunStatus.REVIEW_REQUIRED.value:
                    raise RunConflictError(
                        "resume claim 必须保持 review_required 至清理门禁通过"
                    )
            elif mode == "continue":
                if run.status != RunStatus.RUNNING.value:
                    raise RunConflictError("continue claim 仅适用于 running run")
                return
            else:
                raise RunConflictError(f"不支持的 execution mode：{mode}")
            start_key = (
                f"run:resumed:{review_id}"
                if mode == "resume"
                else f"run:started:attempt:{expected_attempt}"
            )
            await repositories.events.append(
                event_id=lifecycle_event_id(run_id, start_key),
                run_id=run_id,
                event_type=EventType.RUN_STARTED.value,
                payload=RunStartedPayload().model_dump(mode="json"),
                run_status=RunStatus.RUNNING,
            )
            notify = True
        if notify:
            await self.event_log.notifier.notify(run_id)

    async def _reconcile_recovery_execution(
        self,
        execution: Any,
        *,
        run_id: UUID,
        conversation_id: UUID,
        goal: str,
    ) -> Any:
        """Reconcile RUNNING with durable checkpoints before choosing recovery mode."""

        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            reviews = tuple(
                await repositories.reviews.list_for_run(
                    run_id,
                    conversation_id=conversation_id,
                    limit=500,
                )
            )
        resolved_review = next(
            (
                review
                for review in reversed(reviews)
                if review.status
                in {
                    ReviewStatus.APPROVED.value,
                    ReviewStatus.REJECTED.value,
                }
            ),
            None,
        )
        recovery_identity = await execution.recovery_checkpoint_identity()
        if recovery_identity is None or recovery_identity[3] != str(run_id):
            if resolved_review is not None:
                raise RuntimeError("resolved review 缺少可恢复 checkpoint")
            return execution.start(goal)
        checkpoint_identity = recovery_identity[:3]
        if resolved_review is not None and checkpoint_identity == (
            resolved_review.checkpoint_thread_id,
            resolved_review.checkpoint_ns,
            resolved_review.checkpoint_id,
        ):
            decision = ReviewDecision(
                resolved_review.decision_payload["decision"]
            )
            return execution.resume_review(
                resolved_review.id,
                decision,
                comment=resolved_review.decision_payload.get("comment"),
            )
        return execution.continue_from_checkpoint()

    async def _await_execution(
        self,
        execution_coro: Any,
        *,
        heartbeat: asyncio.Task[None],
        token: CancellationToken,
    ) -> AgentOutcome:
        execution = asyncio.create_task(execution_coro)
        try:
            done, _ = await asyncio.wait(
                {execution, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                heartbeat_error = heartbeat.exception()
                if heartbeat_error is None:
                    heartbeat_error = RunCancelledError(token.reason)
                token.cancel(f"run heartbeat stopped: {heartbeat_error}")
                try:
                    await token.propagate()
                except Exception:
                    logger.exception("heartbeat failure cancellation propagation failed")
                try:
                    await execution
                except RuntimeCleanupError:
                    raise
                except BaseException:
                    pass
                if isinstance(heartbeat_error, RunLeaseLostError):
                    raise heartbeat_error
                if isinstance(heartbeat_error, RunCancelledError):
                    raise heartbeat_error
                if isinstance(heartbeat_error, RunHeartbeatError):
                    raise heartbeat_error
                raise RunHeartbeatError(
                    f"run heartbeat failed: {type(heartbeat_error).__name__}: "
                    f"{heartbeat_error}"
                ) from heartbeat_error
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            return await execution
        except asyncio.CancelledError:
            token.cancel("run execution task cancelled")
            try:
                await token.propagate()
            except Exception:
                logger.exception("execution cancellation propagation failed")
            execution.cancel()
            heartbeat.cancel()
            results = await asyncio.gather(
                execution,
                heartbeat,
                return_exceptions=True,
            )
            cleanup_error = next(
                (
                    result
                    for result in results
                    if isinstance(result, RuntimeCleanupError)
                ),
                None,
            )
            if cleanup_error is not None:
                raise cleanup_error
            raise

    async def _heartbeat(
        self,
        run_id: UUID,
        *,
        attempt: int,
        token: CancellationToken,
        observe_cancellation: bool = True,
    ) -> None:
        interval = max(min(self._lease_duration.total_seconds() / 3, 2), 0.5)
        while True:
            await asyncio.sleep(interval)
            cancellation_requested = False
            try:
                async with self._unit_of_work() as unit_of_work:
                    repositories = unit_of_work.repositories
                    assert repositories is not None
                    run = await repositories.runs.get_for_update(run_id)
                    if (
                        run is None
                        or is_terminal_run_status(run.status)
                        or run.worker_id != self.worker_id
                        or run.attempt != attempt
                    ):
                        raise RunLeaseLostError(
                            f"run {run_id} lease fence 已失效"
                        )
                    if (
                        observe_cancellation
                        and run.status == RunStatus.CANCELLING.value
                    ):
                        cancellation_requested = True
                    else:
                        now = self._clock()
                        run.last_heartbeat_at = now
                        run.lease_expires_at = now + self._lease_duration
            except (asyncio.CancelledError, RunLeaseLostError):
                raise
            except Exception as exc:
                raise RunHeartbeatError(
                    f"run {run_id} heartbeat database update failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if cancellation_requested:
                token.cancel("run cancellation requested by persisted command")
                try:
                    await token.propagate()
                except Exception:
                    logger.exception("persisted cancellation propagation failed")
                return
            token.renew_lease()

    async def _handle_outcome(
        self,
        run_id: UUID,
        conversation_id: UUID,
        execution: Any,
        outcome: AgentOutcome,
        *,
        expected_attempt: int,
    ) -> None:
        if outcome.status is AgentOutcomeStatus.COMPLETED:
            await self._finalize_completed(
                run_id,
                expected_attempt=expected_attempt,
            )
            return
        if outcome.status is AgentOutcomeStatus.REVIEW_REQUIRED:
            assert outcome.review is not None
            thread_id, namespace, checkpoint_id = await execution.checkpoint_identity()
            async with self._unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                owned_run = self._require_execution_owner(
                    await repositories.runs.get_for_update(run_id),
                    run_id=run_id,
                    expected_attempt=expected_attempt,
                )
                if owned_run.status != RunStatus.RUNNING.value:
                    raise RunLeaseLostError(
                        f"run {run_id} 不再处于可中断的 running 状态"
                    )
                existing = await repositories.reviews.get_by_tool_call(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    tool_call_id=outcome.review.tool_call_id,
                )
                if existing is None:
                    capability_task = await repositories.tasks.get_by_tool_call(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_call_id=outcome.review.tool_call_id,
                    )
                    if capability_task is None:
                        capability_task = await repositories.tasks.add(
                            RunTask(
                                id=capability_task_id(
                                    run_id, outcome.review.tool_call_id
                                ),
                                conversation_id=conversation_id,
                                run_id=run_id,
                                tool_call_id=outcome.review.tool_call_id,
                                capability_name=outcome.review.capability,
                                status=TaskStatus.PENDING.value,
                                request_payload=outcome.review.arguments,
                            )
                        )
                        await repositories.events.append(
                            event_id=lifecycle_event_id(
                                run_id,
                                f"task:{capability_task.id}:created",
                            ),
                            run_id=run_id,
                            event_type=EventType.TASK_CREATED.value,
                            payload=TaskCreatedPayload(
                                task_id=capability_task.id,
                                title=f"执行 {outcome.review.capability}",
                                description=outcome.review.reason,
                                capability_name=outcome.review.capability,
                            ).model_dump(mode="json"),
                        )
                    await repositories.reviews.add(
                        Review(
                            id=outcome.review.review_id,
                            conversation_id=conversation_id,
                            run_id=run_id,
                            capability_name=outcome.review.capability,
                            tool_call_id=outcome.review.tool_call_id,
                            checkpoint_thread_id=thread_id,
                            checkpoint_ns=namespace,
                            checkpoint_id=checkpoint_id,
                            request_payload=outcome.review.arguments,
                        )
                    )
                    await repositories.checkpoint_anchors.add(
                        CheckpointAnchor(
                            conversation_id=conversation_id,
                            run_id=run_id,
                            thread_id=thread_id,
                            checkpoint_ns=namespace,
                            checkpoint_id=checkpoint_id,
                            anchor_kind="review",
                        )
                    )
                await repositories.events.append(
                    event_id=lifecycle_event_id(
                        run_id, f"review:{outcome.review.review_id}:requested"
                    ),
                    run_id=run_id,
                    event_type=EventType.REVIEW_REQUESTED.value,
                    payload=ReviewRequestedPayload(
                        review_id=outcome.review.review_id,
                        task_id=capability_task_id(
                            run_id, outcome.review.tool_call_id
                        ),
                        prompt=(
                            f"{outcome.review.capability}: {outcome.review.reason}"
                        ),
                    ).model_dump(mode="json"),
                    run_status=RunStatus.REVIEW_REQUIRED,
                )
                await repositories.events.append(
                    event_id=lifecycle_event_id(
                        run_id, f"run:interrupted:{outcome.review.review_id}"
                    ),
                    run_id=run_id,
                    event_type=EventType.RUN_INTERRUPTED.value,
                    payload=RunInterruptedPayload(
                        review_id=outcome.review.review_id,
                        reason="waiting for human review",
                    ).model_dump(mode="json"),
                )
                run = await repositories.runs.get(run_id)
                assert run is not None
                run.worker_id = None
                run.lease_expires_at = None
            await self.event_log.notifier.notify(run_id)
            return
        await self._finalize_failed(
            run_id,
            error_code=(
                "agent_budget_exhausted"
                if outcome.status is AgentOutcomeStatus.BUDGET_EXHAUSTED
                else "agent_stalled"
            ),
            summary=outcome.stop_reason or outcome.status.value,
            expected_attempt=expected_attempt,
        )

    async def _finalize_completed(
        self,
        run_id: UUID,
        *,
        expected_attempt: int,
    ) -> None:
        completed = False
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            run = self._require_execution_owner(
                run,
                run_id=run_id,
                expected_attempt=expected_attempt,
            )
            if run.status == RunStatus.CANCELLING.value:
                # A committed cancel command wins over a later model response.
                pass
            elif run.status == RunStatus.COMPLETED.value:
                return
            elif is_terminal_run_status(run.status):
                return
            else:
                artifacts = await repositories.artifacts.list_for_run(
                    run_id,
                    conversation_id=run.conversation_id,
                    limit=500,
                )
                await self._set_root_task(repositories, run, TaskStatus.COMPLETED)
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "run:completed"),
                    run_id=run_id,
                    event_type=EventType.RUN_COMPLETED.value,
                    payload=RunCompletedPayload(
                        artifact_ids=[artifact.id for artifact in artifacts[:100]]
                    ).model_dump(mode="json"),
                    run_status=RunStatus.COMPLETED,
                )
                run.worker_id = None
                run.lease_expires_at = None
                completed = True
        if run.status == RunStatus.CANCELLING.value:
            await self._finalize_cancelled(
                run_id,
                "cancel command won completion race",
                expected_attempt=expected_attempt,
            )
            return
        if completed:
            await self.event_log.notifier.notify(run_id)

    async def _finalize_failed(
        self,
        run_id: UUID,
        *,
        error_code: str,
        summary: str,
        expected_attempt: int,
    ) -> None:
        cancelled_won = False
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            if run is None or is_terminal_run_status(run.status):
                return
            run = self._require_execution_owner(
                run,
                run_id=run_id,
                expected_attempt=expected_attempt,
            )
            if run.status == RunStatus.CANCELLING.value:
                cancelled_won = True
            else:
                await self._set_root_task(repositories, run, TaskStatus.FAILED, summary)
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "run:failed"),
                    run_id=run_id,
                    event_type=EventType.RUN_FAILED.value,
                    payload=RunFailedPayload(
                        error_code=error_code,
                        error_summary=summary[:2_000],
                    ).model_dump(mode="json"),
                    run_status=RunStatus.FAILED,
                    error_summary=summary[:2_000],
                )
                run.worker_id = None
                run.lease_expires_at = None
        if cancelled_won:
            await self._finalize_cancelled(
                run_id,
                summary,
                expected_attempt=expected_attempt,
            )
            return
        await self.event_log.notifier.notify(run_id)

    async def _release_lease(
        self,
        run_id: UUID,
        *,
        expected_attempt: int,
    ) -> None:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            if (
                run is not None
                and run.worker_id == self.worker_id
                and run.attempt == expected_attempt
            ):
                run.worker_id = None
                run.lease_expires_at = None

    async def _finalize_cancelled(
        self,
        run_id: UUID,
        reason: str | None,
        *,
        expected_attempt: int | None,
    ) -> None:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            run = await repositories.runs.get_for_update(run_id)
            if run is None or is_terminal_run_status(run.status):
                return
            if expected_attempt is not None:
                run = self._require_execution_owner(
                    run,
                    run_id=run_id,
                    expected_attempt=expected_attempt,
                )
            elif (
                run.worker_id is not None
                and run.lease_expires_at is not None
                and run.lease_expires_at > self._clock()
            ):
                return
            if run.status != RunStatus.CANCELLING.value:
                await repositories.events.append(
                    event_id=lifecycle_event_id(run_id, "run:cancel-requested"),
                    run_id=run_id,
                    event_type=EventType.RUN_CANCEL_REQUESTED.value,
                    payload=RunCancelRequestedPayload(reason=reason).model_dump(mode="json"),
                    run_status=RunStatus.CANCELLING,
                )
            await self._set_root_task(repositories, run, TaskStatus.CANCELLED, reason)
            await repositories.events.append(
                event_id=lifecycle_event_id(run_id, "run:cancelled"),
                run_id=run_id,
                event_type=EventType.RUN_CANCELLED.value,
                payload=RunCancelledPayload(reason=reason).model_dump(mode="json"),
                run_status=RunStatus.CANCELLED,
            )
            run.worker_id = None
            run.lease_expires_at = None
        await self.event_log.notifier.notify(run_id)

    async def _set_root_task(
        self,
        repositories: Any,
        run: Run,
        status: TaskStatus,
        summary: str | None = None,
    ) -> None:
        task = await repositories.tasks.get(
            root_task_id(run.id),
            conversation_id=run.conversation_id,
            run_id=run.id,
        )
        already_applied = task is not None and task.status == status.value
        if task is not None:
            task.status = status.value
            task.finished_at = self._clock()
            if status is TaskStatus.FAILED:
                task.error_summary = summary[:2_000] if summary else None
        if already_applied:
            return
        await repositories.events.append(
            event_id=lifecycle_event_id(run.id, f"task:root:{status.value}"),
            run_id=run.id,
            event_type=EventType.TASK_UPDATED.value,
            payload=TaskUpdatedPayload(
                task_id=root_task_id(run.id),
                status=status,
                summary=summary[:2_000] if summary else None,
            ).model_dump(mode="json"),
        )


__all__ = [
    "ConversationNotFoundError",
    "ReviewConflictError",
    "ReviewNotFoundError",
    "RunConflictError",
    "RunCoordinator",
    "RunCoordinatorError",
    "RunLifecycleObserver",
    "RunNotFoundError",
    "capability_call_id",
    "capability_task_id",
    "lifecycle_event_id",
    "root_task_id",
]
