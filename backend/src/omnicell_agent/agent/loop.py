"""A small, domain-neutral LangGraph reasoning and Tool execution loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict, cast
from uuid import UUID, uuid5

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from omnicell_agent.persistence.checkpointer import checkpoint_thread_id
from omnicell_agent.runs.status import ReviewDecision, TaskStatus

from .cancellation import CancellationToken
from .hooks import (
    AgentHook,
    AgentTurnContext,
    DispatchAuthorizationInvalidatedError,
)
from .observer import AgentObserver
from .resource_boundary import contains_internal_resource_locator
from .scientific_evidence import scientific_evidence_from_state
from .tooling import (
    AgentToolFatalError,
    AgentToolInvocation,
    AgentToolRegistry,
    AgentToolRegistryError,
    render_tool_outcome,
)


logger = logging.getLogger(__name__)

_PUBLIC_TOOL_FAILURE = "内部执行失败，请检查输入或稍后重试。"
_MAX_PERSISTED_TOOL_CALLS_PER_TURN = 8
_AUTHORITATIVE_SCIENTIFIC_FINISH_PLACEHOLDER = (
    "由 backend 根据当前 Run 已验证科研证据生成终答。"
)
_MESSAGE_NAMESPACE = UUID("56b099a2-4bb5-4fe4-b6b5-acdb514a33e1")


class AgentOutcomeStatus(StrEnum):
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STALLED = "stalled"


class AgentLoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: int = Field(default=24, ge=1, le=200)
    max_model_calls: int = Field(default=30, ge=1, le=300)
    max_tool_calls: int = Field(default=20, ge=1, le=200)
    timeout_seconds: float = Field(default=30 * 60, gt=0, le=24 * 60 * 60)
    max_empty_reprompts: int = Field(default=2, ge=0, le=10)
    max_model_retries: int = Field(default=2, ge=0, le=5)
    max_tool_retries: int = Field(default=1, ge=0, le=5)


class ReviewInterrupt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: UUID
    tool_call_id: str = Field(min_length=1, max_length=255)
    capability: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    arguments: dict[str, Any]


class ReviewResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: UUID
    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=2_000)


class AgentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AgentOutcomeStatus
    final_response: str | None = Field(default=None, max_length=20_000)
    stop_reason: str | None = Field(default=None, max_length=500)
    turn_count: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    review: ReviewInterrupt | None = None


class AgentLoopState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    run_id: str
    task_status: str
    turn_count: int
    model_calls: int
    tool_calls: int
    consecutive_no_tool: int
    started_at_epoch: float
    outcome_status: str | None
    final_response: str | None
    stop_reason: str | None
    plan_revision: int
    plan_task_ids: list[str]
    plan_task_statuses: dict[str, str]
    plan_step_definitions: dict[str, dict[str, Any]]
    plan_step_evidence: dict[str, list[str]]
    active_plan_task_id: str | None
    loaded_skill_resources: list[dict[str, Any]]
    loaded_memory_resources: list[dict[str, Any]]
    tool_failure_counts: dict[str, int]
    tool_evidence: dict[str, dict[str, Any]]
    tool_call_batch_rejected: bool


def _json_size_guard(
    value: Mapping[str, Any],
    *,
    max_bytes: int = 64 * 1024,
) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"Tool arguments 超过 {max_bytes} bytes")


def _content_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return json.dumps(message.content, ensure_ascii=False, default=str)


def _final_response_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()[:20_000]
    text_blocks: list[str] = []
    for block in content:
        if isinstance(block, str):
            text_blocks.append(block)
            continue
        if isinstance(block, Mapping):
            text = block.get("text")
            if isinstance(text, str):
                text_blocks.append(text)
    return "\n".join(
        block.strip()
        for block in text_blocks
        if block.strip()
    )[:20_000]


def _has_unresolved_plan(state: AgentLoopState) -> bool:
    statuses = {
        str(task_id): str(status)
        for task_id, status in state.get("plan_task_statuses", {}).items()
    }
    task_ids = {
        str(task_id) for task_id in state.get("plan_task_ids", [])
    } | set(statuses)
    return any(
        statuses.get(task_id) != TaskStatus.COMPLETED.value
        for task_id in task_ids
    )


def _declared_tool_call_count(message: AIMessage) -> int:
    """Count declarations without double-counting parser source mirrors."""

    count = len(message.tool_calls)
    canonical_ids = {
        str(call.get("id"))
        for call in message.tool_calls
        if call.get("id")
    }
    raw_calls = message.additional_kwargs.get("tool_calls")
    raw_ids: set[str] = set()
    if isinstance(raw_calls, list):
        for call in raw_calls:
            call_id = call.get("id") if isinstance(call, Mapping) else None
            if call_id and str(call_id) in canonical_ids:
                continue
            count += 1
            if call_id:
                raw_ids.add(str(call_id))
    for call in message.invalid_tool_calls:
        call_id = call.get("id") if isinstance(call, Mapping) else None
        if call_id and str(call_id) in canonical_ids | raw_ids:
            continue
        count += 1
    return count


def _tool_outcome(
    messages: Any,
    *,
    tool_call_id: str,
) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if (
            not isinstance(message, ToolMessage)
            or str(message.tool_call_id) != tool_call_id
            or not isinstance(message.content, str)
        ):
            continue
        try:
            payload = json.loads(message.content)
        except (TypeError, ValueError):
            return None
        if (
            isinstance(payload, dict)
            and payload.get("status") in {"completed", "failed"}
        ):
            return payload
    return None


def _prior_tool_call(
    messages: list[AnyMessage],
    *,
    tool_call_id: str,
    run_id: UUID,
) -> dict[str, Any] | None:
    for message in reversed(messages[:-1]):
        if not isinstance(message, AIMessage):
            continue
        if str(
            message.response_metadata.get("omnicell_run_id") or ""
        ) != str(run_id):
            continue
        for call in message.tool_calls:
            if str(call.get("id") or "") == tool_call_id:
                return dict(call)
    return None


def _normalized_tool_calls(
    calls: list[dict[str, Any]],
    *,
    run_id: UUID,
    turn: int,
) -> list[dict[str, Any]]:
    """Return canonical calls with safe, bounded, batch-unique IDs."""

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, call in enumerate(calls):
        item = dict(call)
        original_id = str(item.get("id") or "").strip()
        call_id = original_id
        safe_public_id = bool(
            call_id
            and len(call_id) <= 255
            and call_id.isascii()
            and call_id[0].isalnum()
            and all(
                character.isalnum() or character in "_.:-"
                for character in call_id
            )
        )
        if not safe_public_id or call_id in seen_ids:
            salt = 0
            while True:
                call_id = "call_" + hashlib.sha256(
                    json.dumps(
                        {
                            "run_id": str(run_id),
                            "turn": turn,
                            "index": index,
                            "original_id": original_id,
                            "name": item.get("name"),
                            "args": item.get("args"),
                            "salt": salt,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()[:32]
                if call_id not in seen_ids:
                    break
                salt += 1
        item["id"] = call_id
        seen_ids.add(call_id)
        normalized.append(item)
    return normalized


class AgentExecution:
    """Coordinator-owned execution over an injected prompt and Tool registry."""

    def __init__(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        model: Any,
        tools: AgentToolRegistry,
        system_prompt: str,
        context_messages: tuple[SystemMessage, ...],
        checkpointer: Any,
        cancellation: CancellationToken,
        observer: AgentObserver,
        config: AgentLoopConfig,
        hooks: tuple[AgentHook, ...] = (),
        clock: Callable[[], float] = time.time,
        fatal_tool_errors: tuple[type[BaseException], ...] = (),
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("Agent system prompt 不能为空")
        if not tools.definitions:
            raise ValueError("Agent Loop 至少需要一个注册 Tool")
        self.run_id = run_id
        self.conversation_id = conversation_id
        self._tools = tools
        self._system_prompt = system_prompt
        self._context_messages = context_messages
        self._cancellation = cancellation
        self._observer = observer
        self._settings = config
        self._hooks = hooks
        self._clock = clock
        self._fatal_tool_errors = (
            AgentToolFatalError,
            *fatal_tool_errors,
        )
        self._model = model
        self._graph = self._build_graph(checkpointer)
        self._graph_config = {
            "configurable": {
                "thread_id": checkpoint_thread_id(str(conversation_id))
            },
            "recursion_limit": max(25, config.max_turns * 4 + 10),
        }

    async def start(self, instruction: str) -> AgentOutcome:
        normalized = instruction.strip()
        if not normalized:
            raise ValueError("Agent instruction 不能为空")
        if len(normalized) > 20_000:
            raise ValueError("Agent instruction 超过 20,000 字符")
        initial: AgentLoopState = {
            "messages": [
                HumanMessage(
                    content=normalized,
                    id=str(uuid5(_MESSAGE_NAMESPACE, f"{self.run_id}:user")),
                )
            ],
            "run_id": str(self.run_id),
            "task_status": TaskStatus.PENDING.value,
            "turn_count": 0,
            "model_calls": 0,
            "tool_calls": 0,
            "consecutive_no_tool": 0,
            "started_at_epoch": self._clock(),
            "outcome_status": "",
            "final_response": "",
            "stop_reason": "",
            "plan_revision": 0,
            "plan_task_ids": [],
            "plan_task_statuses": {},
            "plan_step_definitions": {},
            "plan_step_evidence": {},
            "active_plan_task_id": "",
            "loaded_skill_resources": [],
            "loaded_memory_resources": [],
            "tool_failure_counts": {},
            "tool_evidence": {},
            "tool_call_batch_rejected": False,
        }
        result = await self._invoke_with_timeout(
            initial,
            remaining_seconds=self._settings.timeout_seconds,
        )
        if isinstance(result, AgentOutcome):
            return result
        return self._outcome(result)

    async def resume_review(
        self,
        review_id: UUID,
        decision: ReviewDecision,
        *,
        comment: str | None = None,
    ) -> AgentOutcome:
        resolution = ReviewResolution(
            review_id=review_id,
            decision=decision,
            comment=comment,
        )
        result = await self._invoke_with_timeout(
            Command(resume=resolution.model_dump(mode="json")),
            remaining_seconds=await self._remaining_seconds(),
        )
        if isinstance(result, AgentOutcome):
            return result
        return self._outcome(result)

    async def continue_from_checkpoint(self) -> AgentOutcome:
        snapshot = await self._graph.aget_state(self._graph_config)
        if not snapshot.values:
            raise RuntimeError("Agent checkpoint 不存在，无法恢复 run")
        result = await self._invoke_with_timeout(
            None,
            remaining_seconds=await self._remaining_seconds(snapshot.values),
        )
        if isinstance(result, AgentOutcome):
            return result
        return self._outcome(result)

    async def checkpoint_identity(self) -> tuple[str, str, str]:
        identity = await self.current_checkpoint_identity()
        if identity is None:
            raise RuntimeError("Agent checkpoint identity 不完整")
        return identity

    async def current_checkpoint_identity(self) -> tuple[str, str, str] | None:
        identity = await self.recovery_checkpoint_identity()
        return identity[:3] if identity is not None else None

    async def recovery_checkpoint_identity(
        self,
    ) -> tuple[str, str, str, str] | None:
        snapshot = await self._graph.aget_state(self._graph_config)
        if not snapshot.values:
            return None
        configurable = dict(snapshot.config.get("configurable") or {})
        checkpoint_id = str(configurable.get("checkpoint_id") or "")
        if not checkpoint_id:
            return None
        return (
            str(
                configurable.get("thread_id")
                or checkpoint_thread_id(str(self.conversation_id))
            ),
            str(configurable.get("checkpoint_ns") or ""),
            checkpoint_id,
            str(snapshot.values.get("run_id") or ""),
        )

    async def _remaining_seconds(
        self,
        values: Mapping[str, Any] | None = None,
    ) -> float:
        if values is None:
            snapshot = await self._graph.aget_state(self._graph_config)
            values = snapshot.values
        started = float(values.get("started_at_epoch", self._clock()))
        return max(
            self._settings.timeout_seconds - (self._clock() - started),
            0,
        )

    async def _invoke_with_timeout(
        self,
        graph_input: Any,
        *,
        remaining_seconds: float,
    ) -> Mapping[str, Any] | AgentOutcome:
        try:
            if remaining_seconds <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining_seconds):
                return await self._graph.ainvoke(
                    graph_input,
                    self._graph_config,
                    durability="sync",
                )
        except TimeoutError:
            self._cancellation.cancel("Agent wall-clock budget exhausted")
            try:
                await self._cancellation.propagate()
            except Exception:
                pass
            await self._observer.emit(
                "budget.exhausted",
                {
                    "reason": "wall_clock",
                    "limit": self._settings.timeout_seconds,
                    "used": self._settings.timeout_seconds,
                    "unit": "seconds",
                },
                dedupe_key=f"budget:{self.run_id}:wall_clock",
            )
            snapshot = await self._graph.aget_state(self._graph_config)
            values = snapshot.values
            return AgentOutcome(
                status=AgentOutcomeStatus.BUDGET_EXHAUSTED,
                stop_reason="Agent budget exhausted: wall_clock",
                turn_count=int(values.get("turn_count", 0)),
                model_calls=int(values.get("model_calls", 0)),
                tool_calls=int(values.get("tool_calls", 0)),
            )

    def _build_graph(self, checkpointer: Any) -> Any:
        builder = StateGraph(AgentLoopState)
        builder.add_node("agent", self._agent_node)
        builder.add_node("tools", self._tool_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"agent": "agent", "tools": "tools", "end": END},
        )
        builder.add_conditional_edges(
            "tools",
            self._route_after_tool,
            {"agent": "agent", "end": END},
        )
        return builder.compile(
            checkpointer=checkpointer,
            name="generic-agent-loop",
        )

    async def _agent_node(
        self,
        state: AgentLoopState,
    ) -> dict[str, Any]:
        self._cancellation.raise_if_cancelled()
        valid_skill_resources, invalid_skill_resources = (
            self._tools.sanitize_skill_resources(
                list(state.get("loaded_skill_resources", []))
            )
        )
        if invalid_skill_resources:
            return {
                "loaded_skill_resources": valid_skill_resources,
                "messages": [
                    SystemMessage(
                        name="skill_context_recovery",
                        content=render_tool_outcome(
                            status="failed",
                            capability="agent_loop",
                            summary=(
                                "已清理无法按当前目录重建的 Skill 方法上下文。"
                            ),
                            error_code="skill_context_stale",
                            retryable=True,
                            recovery_hint=(
                                "根据当前目标重新调用 load_skill，"
                                "或在不需要该方法时继续选择其他 Tool。"
                            ),
                        ),
                    )
                ],
            }
        budget = self._budget_reason(state, before_model=True)
        if budget is not None:
            return await self._budget_exhausted(state, budget)

        next_turn = int(state.get("turn_count", 0)) + 1
        await self._observer.emit(
            "agent.turn_started",
            {"turn": next_turn},
            dedupe_key=f"turn:{next_turn}:started",
        )
        base_messages = [
            SystemMessage(content=self._system_prompt),
            *self._context_messages,
            *state.get("messages", []),
        ]
        turn_context = AgentTurnContext(
            state=dict(state),
            messages=base_messages,
            model=self._model,
        )
        for hook in self._hooks:
            await hook.pre_invoke(turn_context)
        turn_context.model = turn_context.model.bind_tools(
            self._tools.model_definitions(
                list(state.get("loaded_skill_resources", []))
            )
        )
        messages = turn_context.messages
        message: AIMessage | None = None
        model_calls = int(state.get("model_calls", 0))
        for attempt in range(self._settings.max_model_retries + 1):
            self._cancellation.raise_if_cancelled()
            try:
                for check in turn_context.pre_dispatch_checks:
                    await check()
                candidate = await self._invoke_model(
                    turn_context.model,
                    messages,
                )
                if not isinstance(candidate, AIMessage):
                    raise TypeError("Agent model 必须返回 AIMessage")
                turn_context.result = candidate
                for hook in self._hooks:
                    await hook.post_invoke(turn_context)
                message = turn_context.result
                if not isinstance(message, AIMessage):
                    raise TypeError("Agent hook 必须保留 AIMessage 结果")
                model_calls += 1
                break
            except DispatchAuthorizationInvalidatedError:
                # This exact body set is no longer authorized. In particular,
                # never route a revoke/purge/consent race into model retry.
                raise RuntimeError(
                    "memory disclosure authorization changed before dispatch"
                ) from None
            except Exception:
                model_calls += 1
                if attempt >= self._settings.max_model_retries:
                    if turn_context.transient_memory_bodies:
                        raise RuntimeError(
                            "model invocation failed with transient memory context"
                        ) from None
                    raise
                if model_calls >= self._settings.max_model_calls:
                    exhausted = await self._budget_exhausted(
                        state,
                        "model_calls",
                    )
                    exhausted["model_calls"] = model_calls
                    exhausted["turn_count"] = next_turn
                    return exhausted
        assert message is not None
        self._cancellation.raise_if_cancelled()

        declared_tool_call_count = _declared_tool_call_count(message)
        canonical_tool_calls = _normalized_tool_calls(
            list(message.tool_calls),
            run_id=self.run_id,
            turn=next_turn,
        )
        scientific_finish_redacted = False
        if scientific_evidence_from_state(state):
            sanitized_tool_calls: list[dict[str, Any]] = []
            for tool_call in canonical_tool_calls:
                if tool_call.get("name") != "finish_task":
                    sanitized_tool_calls.append(tool_call)
                    continue
                arguments = dict(tool_call.get("args") or {})
                if "final_response" in arguments:
                    arguments["final_response"] = (
                        _AUTHORITATIVE_SCIENTIFIC_FINISH_PLACEHOLDER
                    )
                    scientific_finish_redacted = True
                sanitized_tool_calls.append(
                    {
                        **tool_call,
                        "args": arguments,
                    }
                )
            canonical_tool_calls = sanitized_tool_calls
        if scientific_finish_redacted and _content_text(message):
            message = message.model_copy(update={"content": ""})
        message_key = hashlib.sha256(
            json.dumps(
                {
                    "run_id": str(self.run_id),
                    "turn": next_turn,
                    "provider_message_id": message.id,
                    "content": _content_text(message),
                    "tool_calls": canonical_tool_calls,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:32]
        message_update: dict[str, Any] = {
            "id": message_key,
            "tool_calls": canonical_tool_calls,
            "response_metadata": {
                **dict(message.response_metadata),
                "omnicell_run_id": str(self.run_id),
            },
        }
        canonical_tool_call_count = len(canonical_tool_calls)
        additional_kwargs = dict(message.additional_kwargs)
        had_raw_tool_calls = "tool_calls" in additional_kwargs
        additional_kwargs.pop("tool_calls", None)
        if had_raw_tool_calls or message.invalid_tool_calls:
            message_update.update(
                {
                    "invalid_tool_calls": [],
                    "additional_kwargs": additional_kwargs,
                }
            )
        remaining_tool_calls = max(
            self._settings.max_tool_calls
            - int(state.get("tool_calls", 0)),
            0,
        )
        persisted_tool_call_limit = min(
            _MAX_PERSISTED_TOOL_CALLS_PER_TURN,
            remaining_tool_calls,
        )
        if canonical_tool_call_count > persisted_tool_call_limit:
            message_update["tool_calls"] = list(
                canonical_tool_calls[:persisted_tool_call_limit]
            )
        if message_update:
            message = message.model_copy(update=message_update)
        update: dict[str, Any] = {
            "messages": [message],
            "turn_count": next_turn,
            "model_calls": model_calls,
            **turn_context.output_updates,
            "tool_call_batch_rejected": declared_tool_call_count > 1,
        }
        if message.tool_calls:
            update["consecutive_no_tool"] = 0
            return update

        final_response = _final_response_text(message)
        completion_replacement = turn_context.completion_replacement
        if completion_replacement:
            message_key = hashlib.sha256(
                json.dumps(
                    {
                        "run_id": str(self.run_id),
                        "turn": next_turn,
                        "authoritative_scientific_response": (
                            completion_replacement
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:32]
            message = AIMessage(
                id=message_key,
                content=completion_replacement,
                response_metadata={
                    "omnicell_run_id": str(self.run_id),
                    "omnicell_authoritative_scientific_response": True,
                },
            )
            update["messages"] = [message]
            final_response = completion_replacement
        invalid_resource_locator = bool(
            final_response
            and contains_internal_resource_locator(final_response)
        )
        if (
            final_response
            and not _has_unresolved_plan(state)
            and not invalid_resource_locator
        ):
            await self._observer.emit(
                "message.completed",
                {
                    "role": "assistant",
                    "content": final_response,
                    "has_tool_calls": False,
                    "turn": next_turn,
                },
                dedupe_key=f"message:{message_key}",
            )
            update.update(
                {
                    "consecutive_no_tool": 0,
                    "task_status": TaskStatus.COMPLETED.value,
                    "outcome_status": AgentOutcomeStatus.COMPLETED.value,
                    "final_response": final_response,
                }
            )
            return update

        empty_count = int(state.get("consecutive_no_tool", 0)) + 1
        update["consecutive_no_tool"] = empty_count
        if empty_count <= self._settings.max_empty_reprompts:
            reminder = (
                (
                    "最终回复包含内部资源定位符。请只引用 artifact_id 或"
                    "页面中的已登记产物，不要输出 workspace URI、宿主路径"
                    "或 backend 控制目录；请重新作答。"
                )
                if invalid_resource_locator
                else (
                    "当前显式计划仍有未完成步骤。请继续调用完成计划所需的 Tool，"
                    "或根据实际结果更新计划状态；不要只给出阶段性文字后提前结束。"
                    if _has_unresolved_plan(state)
                    else (
                        "当前回复为空，且没有 Tool 调用。"
                        "请直接给出面向用户的非空最终回复，"
                        "或调用完成目标所需的一个 Tool。"
                    )
                )
            )
            update["messages"] = [
                message,
                SystemMessage(content=reminder),
            ]
            return update
        update.update(
            {
                "outcome_status": AgentOutcomeStatus.STALLED.value,
                "stop_reason": (
                    "模型在有限提醒后仍输出内部资源定位符"
                    if invalid_resource_locator
                    else (
                        "显式计划仍未完成，且在有限提醒后仍未产生 Tool 调用"
                        if _has_unresolved_plan(state)
                        else "模型在有限提醒后仍未产生非空回复或 Tool 调用"
                    )
                ),
            }
        )
        return update

    async def _tool_node(
        self,
        state: AgentLoopState,
    ) -> dict[str, Any]:
        self._cancellation.raise_if_cancelled()
        budget = self._budget_reason(state, before_tool=True)
        if budget is not None:
            return await self._budget_exhausted(state, budget)

        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            raise RuntimeError("tools node 缺少 AIMessage")
        message = cast(AIMessage, messages[-1])
        valid_skill_resources, invalid_skill_resources = (
            self._tools.sanitize_skill_resources(
                list(state.get("loaded_skill_resources", []))
            )
        )
        if (
            invalid_skill_resources
            and len(message.tool_calls) == 1
            and not self._tools.handles_stale_skill_resources(
                str(message.tool_calls[0].get("name") or "")
            )
        ):
            call = message.tool_calls[0]
            name = str(call.get("name") or "agent_loop")
            definition = self._tools.definition(name)
            retryable = bool(
                definition is not None
                and not definition.required_skills
            )
            return {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability=name,
                            summary=(
                                "已清理无法按当前目录重建的 Skill 方法上下文；"
                                "本次 Tool 未执行。"
                            ),
                            error_code="skill_context_stale",
                            retryable=retryable,
                            recovery_hint=(
                                "重新调用当前 Tool。"
                                if retryable
                                else (
                                    "先重新加载当前 Skill，"
                                    "再从模型可见 Tool 中重新选择。"
                                )
                            ),
                        ),
                        tool_call_id=str(call.get("id") or "invalid")[:255],
                    )
                ],
                "loaded_skill_resources": valid_skill_resources,
                "tool_calls": int(state.get("tool_calls", 0)) + 1,
                "tool_call_batch_rejected": False,
            }
        if (
            bool(state.get("tool_call_batch_rejected"))
            or len(message.tool_calls) != 1
        ):
            errors = [
                ToolMessage(
                    content=render_tool_outcome(
                        status="failed",
                        capability=str(call.get("name") or "agent_loop"),
                        summary="每一轮只能调用一个 Tool。",
                        error_code="multiple_tool_calls",
                        retryable=False,
                        recovery_hint="下一轮只选择一个最必要的 Tool 调用。",
                    ),
                    tool_call_id=str(call.get("id") or "invalid")[:255],
                )
                for call in message.tool_calls
            ]
            update: dict[str, Any] = {
                "messages": errors,
                "tool_calls": int(state.get("tool_calls", 0))
                + len(errors),
                "tool_call_batch_rejected": False,
            }
            if invalid_skill_resources:
                update["loaded_skill_resources"] = valid_skill_resources
            return update

        call = message.tool_calls[0]
        name = str(call.get("name") or "")
        tool_call_id = self._tool_call_id(call, state)
        definition = self._tools.definition(name)
        next_tool_count = int(state.get("tool_calls", 0)) + 1
        try:
            arguments = dict(call.get("args") or {})
            _json_size_guard(arguments)
        except (TypeError, ValueError):
            if definition is not None:
                await self._observer.emit(
                    "agent.tool_started",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "category": definition.category,
                    },
                    dedupe_key=f"agent-tool:{tool_call_id}:started",
                )
            update = {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability=name or "agent_loop",
                            summary="Tool 参数不是有效的有界 JSON object。",
                            error_code="tool_arguments_invalid",
                            retryable=False,
                            recovery_hint="根据 Tool schema 重新构造参数。",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "tool_calls": next_tool_count,
                "task_status": TaskStatus.IN_PROGRESS.value,
            }
            if definition is not None:
                await self._observer.emit(
                    "agent.tool_failed",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "category": definition.category,
                        "error_code": "tool_arguments_invalid",
                        "error_summary": "Tool 参数不是有效的有界 JSON object。",
                        "retryable": False,
                        "recovery_hint": "根据 Tool schema 重新构造参数。",
                    },
                    dedupe_key=f"agent-tool:{tool_call_id}:failed",
                )
            return update
        prior_call = _prior_tool_call(
            messages,
            tool_call_id=tool_call_id,
            run_id=self.run_id,
        )
        if prior_call is not None:
            prior_name = str(prior_call.get("name") or "")
            try:
                prior_arguments = dict(prior_call.get("args") or {})
                _json_size_guard(prior_arguments)
            except (TypeError, ValueError):
                prior_arguments = {}
            prior_outcome = _tool_outcome(
                messages[:-1],
                tool_call_id=tool_call_id,
            )
            if (
                prior_name == name
                and prior_arguments == arguments
                and prior_outcome is not None
            ):
                return {
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                prior_outcome,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "tool_calls": next_tool_count,
                    "task_status": TaskStatus.IN_PROGRESS.value,
                    "tool_call_batch_rejected": False,
                }
            conflict_summary = (
                "该 Tool call ID 已在当前 run 中使用，不能绑定到另一项调用。"
                if prior_name != name or prior_arguments != arguments
                else "该 Tool call ID 的既有结果无法安全重放。"
            )
            conflict_code = (
                "tool_call_id_conflict"
                if prior_name != name or prior_arguments != arguments
                else "tool_call_replay_unavailable"
            )
            conflict_hint = (
                "为新的 Tool 调用生成新的 tool_call_id。"
                if conflict_code == "tool_call_id_conflict"
                else "检查 checkpoint 完整性后使用新的 tool_call_id 重新调用。"
            )
            if definition is not None:
                await self._observer.emit(
                    "agent.tool_failed",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "category": definition.category,
                        "error_code": conflict_code,
                        "error_summary": conflict_summary,
                        "retryable": False,
                        "recovery_hint": conflict_hint,
                    },
                    dedupe_key=f"agent-tool:{tool_call_id}:failed",
                )
            return {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability=name or "agent_loop",
                            summary=conflict_summary,
                            error_code=conflict_code,
                            retryable=False,
                            recovery_hint=conflict_hint,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "tool_calls": next_tool_count,
                "task_status": TaskStatus.IN_PROGRESS.value,
                "tool_call_batch_rejected": False,
            }
        if definition is not None:
            await self._observer.emit(
                "agent.tool_started",
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": name,
                    "category": definition.category,
                },
                dedupe_key=f"agent-tool:{tool_call_id}:started",
            )
        try:
            raw_update = await self._tools.invoke(
                AgentToolInvocation(
                    name=name,
                    arguments=arguments,
                    tool_call_id=tool_call_id,
                    state=state,
                )
            )
            update = dict(raw_update)
        except GraphInterrupt:
            raise
        except self._fatal_tool_errors:
            raise
        except AgentToolRegistryError:
            update = {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability=name or "agent_loop",
                            summary="Tool 不可用。",
                            error_code="tool_unavailable",
                            retryable=False,
                            recovery_hint="从当前模型可见的已注册 Tool 中重新选择。",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        except ValidationError:
            update = {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability=name or "agent_loop",
                            summary="Tool 参数不符合类型契约。",
                            error_code="tool_arguments_invalid",
                            retryable=False,
                            recovery_hint="根据 Tool schema 修正字段、类型与取值范围。",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        except Exception as exc:
            logger.error(
                "registered Tool invocation failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"run_id": str(self.run_id), "tool": name},
            )
            update = {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability=name or "agent_loop",
                            summary=_PUBLIC_TOOL_FAILURE,
                            error_code="tool_internal_error",
                            retryable=False,
                            recovery_hint=(
                                "不要重复相同调用；选择其他能力或向用户说明限制。"
                            ),
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        if not update.get("messages"):
            logger.error(
                "registered Tool returned no ToolMessage",
                extra={"run_id": str(self.run_id), "tool": name},
            )
            update["messages"] = [
                ToolMessage(
                    content=render_tool_outcome(
                        status="failed",
                        capability=name or "agent_loop",
                        summary=_PUBLIC_TOOL_FAILURE,
                        error_code="tool_contract_invalid",
                        retryable=False,
                        recovery_hint=(
                            "不要重复相同调用；选择其他能力或向用户说明限制。"
                        ),
                    ),
                    tool_call_id=tool_call_id,
                )
            ]
        outcome = _tool_outcome(
            update.get("messages"),
            tool_call_id=tool_call_id,
        )
        if definition is not None and outcome is not None:
            if outcome["status"] == "completed":
                await self._observer.emit(
                    "agent.tool_completed",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "category": definition.category,
                        "summary": str(
                            outcome.get("summary") or "Tool 调用已完成"
                        ),
                    },
                    dedupe_key=f"agent-tool:{tool_call_id}:completed",
                )
            else:
                await self._observer.emit(
                    "agent.tool_failed",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": name,
                        "category": definition.category,
                        "error_code": str(
                            outcome.get("error_code")
                            or "tool_internal_error"
                        ),
                        "error_summary": str(
                            outcome.get("summary")
                            or _PUBLIC_TOOL_FAILURE
                        ),
                        "retryable": bool(
                            outcome.get("retryable", False)
                        ),
                        "recovery_hint": str(
                            outcome.get("recovery_hint")
                            or "选择其他能力或向用户说明限制。"
                        ),
                    },
                    dedupe_key=f"agent-tool:{tool_call_id}:failed",
                )
        update["tool_calls"] = next_tool_count
        if not update.get("outcome_status"):
            update.setdefault(
                "task_status",
                TaskStatus.IN_PROGRESS.value,
            )
        return update

    def _route_after_agent(
        self,
        state: AgentLoopState,
    ) -> Literal["agent", "tools", "end"]:
        if state.get("outcome_status"):
            return "end"
        messages = state.get("messages", [])
        if (
            messages
            and isinstance(messages[-1], AIMessage)
            and messages[-1].tool_calls
        ):
            return "tools"
        return "agent"

    @staticmethod
    def _route_after_tool(
        state: AgentLoopState,
    ) -> Literal["agent", "end"]:
        return "end" if state.get("outcome_status") else "agent"

    def _budget_reason(
        self,
        state: AgentLoopState,
        *,
        before_model: bool = False,
        before_tool: bool = False,
    ) -> str | None:
        elapsed = self._clock() - float(
            state.get("started_at_epoch", self._clock())
        )
        if elapsed >= self._settings.timeout_seconds:
            return "wall_clock"
        if before_model:
            if int(state.get("turn_count", 0)) >= self._settings.max_turns:
                return "turns"
            if (
                int(state.get("model_calls", 0))
                >= self._settings.max_model_calls
            ):
                return "model_calls"
            if (
                int(state.get("tool_calls", 0))
                >= self._settings.max_tool_calls
            ):
                return "tool_calls"
        if (
            before_tool
            and int(state.get("tool_calls", 0))
            >= self._settings.max_tool_calls
        ):
            return "tool_calls"
        return None

    async def _budget_exhausted(
        self,
        state: AgentLoopState,
        reason: str,
    ) -> dict[str, Any]:
        await self._observer.emit(
            "budget.exhausted",
            {
                "reason": reason,
                "limit": {
                    "turns": self._settings.max_turns,
                    "model_calls": self._settings.max_model_calls,
                    "tool_calls": self._settings.max_tool_calls,
                    "wall_clock": self._settings.timeout_seconds,
                }.get(reason, 0),
                "used": {
                    "turns": int(state.get("turn_count", 0)),
                    "model_calls": int(state.get("model_calls", 0)),
                    "tool_calls": int(state.get("tool_calls", 0)),
                    "wall_clock": max(
                        self._clock()
                        - float(
                            state.get(
                                "started_at_epoch",
                                self._clock(),
                            )
                        ),
                        0,
                    ),
                }.get(reason, 0),
                "unit": "seconds" if reason == "wall_clock" else "count",
            },
            dedupe_key=f"budget:{self.run_id}:{reason}",
        )
        return {
            "outcome_status": AgentOutcomeStatus.BUDGET_EXHAUSTED.value,
            "stop_reason": f"Agent budget exhausted: {reason}",
        }

    async def _invoke_model(
        self,
        model: Any,
        messages: list[AnyMessage],
    ) -> Any:
        work = asyncio.create_task(model.ainvoke(messages))
        cancelled = asyncio.create_task(self._cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {work, cancelled},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                self._cancellation.raise_if_cancelled()
            return await work
        except asyncio.CancelledError:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            raise
        finally:
            if not cancelled.done():
                cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    @staticmethod
    def _tool_call_id(
        call: Mapping[str, Any],
        state: AgentLoopState,
    ) -> str:
        raw = str(call.get("id") or "").strip()
        if raw:
            return raw[:255]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "turn": state.get("turn_count", 0),
                    "name": call.get("name"),
                    "args": call.get("args"),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return f"generated-{digest[:32]}"

    @staticmethod
    def _outcome(result: Mapping[str, Any]) -> AgentOutcome:
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            review = ReviewInterrupt.model_validate(interrupts[0].value)
            status = AgentOutcomeStatus.REVIEW_REQUIRED
        else:
            review = None
            status = AgentOutcomeStatus(
                result.get("outcome_status")
                or AgentOutcomeStatus.STALLED.value
            )
        return AgentOutcome(
            status=status,
            final_response=result.get("final_response") or None,
            stop_reason=result.get("stop_reason") or None,
            turn_count=int(result.get("turn_count", 0)),
            model_calls=int(result.get("model_calls", 0)),
            tool_calls=int(result.get("tool_calls", 0)),
            review=review,
        )


__all__ = [
    "AgentExecution",
    "AgentLoopConfig",
    "AgentLoopState",
    "AgentOutcome",
    "AgentOutcomeStatus",
    "ReviewInterrupt",
    "ReviewResolution",
]
