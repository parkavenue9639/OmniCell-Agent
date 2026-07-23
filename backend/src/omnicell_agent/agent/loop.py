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
from uuid import UUID

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
from pydantic import BaseModel, ConfigDict, Field

from omnicell_agent.persistence.checkpointer import checkpoint_thread_id
from omnicell_agent.runs.status import ReviewDecision, TaskStatus

from .cancellation import CancellationToken
from .observer import AgentObserver
from .tooling import (
    AgentToolFatalError,
    AgentToolInvocation,
    AgentToolRegistry,
    AgentToolRegistryError,
)


logger = logging.getLogger(__name__)

_PUBLIC_TOOL_FAILURE = "内部执行失败，请检查输入或稍后重试。"


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
    loaded_skill_resources: list[str]


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
        self._clock = clock
        self._fatal_tool_errors = (
            AgentToolFatalError,
            *fatal_tool_errors,
        )
        self._bound_model = model.bind_tools(tools.model_definitions())
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
            "messages": [HumanMessage(content=normalized)],
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
            "loaded_skill_resources": [],
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
        budget = self._budget_reason(state, before_model=True)
        if budget is not None:
            return await self._budget_exhausted(state, budget)

        next_turn = int(state.get("turn_count", 0)) + 1
        await self._observer.emit(
            "agent.turn_started",
            {"turn": next_turn},
            dedupe_key=f"turn:{next_turn}:started",
        )
        messages = [
            SystemMessage(content=self._system_prompt),
            *self._context_messages,
            *state.get("messages", []),
        ]
        message: AIMessage | None = None
        model_calls = int(state.get("model_calls", 0))
        for attempt in range(self._settings.max_model_retries + 1):
            self._cancellation.raise_if_cancelled()
            try:
                candidate = await self._invoke_model(messages)
                if not isinstance(candidate, AIMessage):
                    raise TypeError("Agent model 必须返回 AIMessage")
                message = candidate
                model_calls += 1
                break
            except Exception:
                model_calls += 1
                if attempt >= self._settings.max_model_retries:
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

        message_key = message.id or hashlib.sha256(
            json.dumps(
                {
                    "turn": next_turn,
                    "content": _content_text(message),
                    "tool_calls": message.tool_calls,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:32]
        await self._observer.emit(
            "message.completed",
            {
                "role": "assistant",
                "content": _content_text(message)[:20_000],
                "has_tool_calls": bool(message.tool_calls),
                "turn": next_turn,
            },
            dedupe_key=f"message:{message_key}",
        )

        update: dict[str, Any] = {
            "messages": [message],
            "turn_count": next_turn,
            "model_calls": model_calls,
        }
        if message.tool_calls:
            update["consecutive_no_tool"] = 0
            return update

        empty_count = int(state.get("consecutive_no_tool", 0)) + 1
        update["consecutive_no_tool"] = empty_count
        if state.get("task_status") == TaskStatus.COMPLETED.value:
            update["outcome_status"] = AgentOutcomeStatus.COMPLETED.value
            return update
        if empty_count <= self._settings.max_empty_reprompts:
            update["messages"] = [
                message,
                SystemMessage(
                    content=(
                        "当前任务仍未完成。请继续调用完成目标所需的 Tool；"
                        "若目标已经完成，请调用注册的完成 Tool 给出最终答复。"
                    )
                ),
            ]
            return update
        update.update(
            {
                "outcome_status": AgentOutcomeStatus.STALLED.value,
                "stop_reason": "pending task 在有限提醒后仍未产生 Tool 调用",
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
        if len(message.tool_calls) != 1:
            errors = [
                ToolMessage(
                    content="每一轮只能调用一个 Tool，请重新选择。",
                    tool_call_id=str(call.get("id") or "invalid")[:255],
                )
                for call in message.tool_calls[:8]
            ]
            return {
                "messages": errors,
                "tool_calls": int(state.get("tool_calls", 0))
                + len(errors),
            }

        call = message.tool_calls[0]
        name = str(call.get("name") or "")
        arguments = dict(call.get("args") or {})
        _json_size_guard(arguments)
        tool_call_id = self._tool_call_id(call, state)
        next_tool_count = int(state.get("tool_calls", 0)) + 1
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
                        content="Tool 不可用，请从已注册 Tool 中重新选择。",
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
                        content=f"Tool 执行失败：{_PUBLIC_TOOL_FAILURE}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        if not update.get("messages"):
            raise RuntimeError("注册 Tool 必须返回至少一个 ToolMessage")
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

    async def _invoke_model(self, messages: list[AnyMessage]) -> Any:
        work = asyncio.create_task(self._bound_model.ainvoke(messages))
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
