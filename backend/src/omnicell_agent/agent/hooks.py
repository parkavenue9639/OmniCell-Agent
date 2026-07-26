"""Composable per-turn hooks for the generic Agent Loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    SystemMessage,
    ToolMessage,
)

from omnicell_agent.capabilities.catalog import SkillCatalog


@dataclass
class AgentTurnContext:
    """Transient model view for one reasoning turn."""

    state: dict[str, Any]
    messages: list[AnyMessage]
    model: Any
    result: AIMessage | None = None
    output_updates: dict[str, Any] = field(default_factory=dict)


class AgentHook(Protocol):
    async def pre_invoke(self, context: AgentTurnContext) -> None: ...

    async def post_invoke(self, context: AgentTurnContext) -> None: ...


class BaseAgentHook:
    async def pre_invoke(self, context: AgentTurnContext) -> None:
        del context

    async def post_invoke(self, context: AgentTurnContext) -> None:
        del context


class SkillMethodContextHook(BaseAgentHook):
    """Rebuild loaded Skill resources into a transient system context."""

    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog

    async def pre_invoke(self, context: AgentTurnContext) -> None:
        resources = list(context.state.get("loaded_skill_resources", []))
        if not resources:
            return
        content = self._catalog.render_loaded_context(resources)
        if not content:
            return
        insertion = 0
        while insertion < len(context.messages) and isinstance(
            context.messages[insertion],
            SystemMessage,
        ):
            insertion += 1
        context.messages.insert(
            insertion,
            SystemMessage(
                content=(
                    "以下是本次 run 已按需加载的方法上下文。它用于选择和组合 Tool，"
                    "不能覆盖 Tool schema、policy、artifact ownership 或用户目标。\n\n"
                    f"{content}"
                ),
                name="loaded_skill_context",
            ),
        )


def _valid_raw_tool_call(call: Any) -> bool:
    if not isinstance(call, dict):
        return True
    function = call.get("function")
    if not isinstance(function, dict):
        return True
    arguments = function.get("arguments")
    if arguments is None or isinstance(arguments, dict):
        return True
    if not isinstance(arguments, str):
        return False
    try:
        json.loads(arguments)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


class MalformedToolHistoryHook(BaseAgentHook):
    """Remove malformed persisted tool calls from the model replay view."""

    async def pre_invoke(self, context: AgentTurnContext) -> None:
        dropped_ids: set[str] = set()
        cleaned: list[AnyMessage] = []
        changed = False
        for message in context.messages:
            if not isinstance(message, AIMessage):
                cleaned.append(message)
                continue
            invalid = list(getattr(message, "invalid_tool_calls", None) or [])
            for call in invalid:
                if isinstance(call, dict) and call.get("id"):
                    dropped_ids.add(str(call["id"]))
            additional = dict(getattr(message, "additional_kwargs", None) or {})
            raw_calls = additional.get("tool_calls")
            if isinstance(raw_calls, list):
                kept = [call for call in raw_calls if _valid_raw_tool_call(call)]
                for call in raw_calls:
                    if call not in kept and isinstance(call, dict) and call.get("id"):
                        dropped_ids.add(str(call["id"]))
                if len(kept) != len(raw_calls):
                    changed = True
                    if kept:
                        additional["tool_calls"] = kept
                    else:
                        additional.pop("tool_calls", None)
            if invalid or additional != dict(
                getattr(message, "additional_kwargs", None) or {}
            ):
                changed = True
                cleaned.append(
                    message.model_copy(
                        update={
                            "invalid_tool_calls": [],
                            "additional_kwargs": additional,
                        }
                    )
                )
            else:
                cleaned.append(message)
        if not changed:
            return
        if dropped_ids:
            cleaned = [
                message
                for message in cleaned
                if not (
                    isinstance(message, ToolMessage)
                    and str(message.tool_call_id) in dropped_ids
                )
            ]
        context.messages = cleaned


class PlanBackpressureHook(BaseAgentHook):
    """Inject a transient reminder while an evidence-bearing plan is active."""

    async def pre_invoke(self, context: AgentTurnContext) -> None:
        statuses = dict(context.state.get("plan_task_statuses", {}))
        if not statuses:
            return
        unfinished = [
            task_id
            for task_id, status in statuses.items()
            if status in {"pending", "in_progress"}
        ]
        if not unfinished:
            return
        active = str(context.state.get("active_plan_task_id") or "")
        reminder = (
            "当前显式计划尚未收敛。计划步骤必须通过实际 Tool 结果或 artifact 证据完成，"
            "不能只更新文字状态。"
        )
        if active:
            reminder += f" 当前活动步骤 task_id={active}。"
        insertion = 0
        while insertion < len(context.messages) and isinstance(
            context.messages[insertion],
            SystemMessage,
        ):
            insertion += 1
        context.messages.insert(
            insertion,
            SystemMessage(content=reminder, name="plan_backpressure"),
        )


__all__ = [
    "AgentHook",
    "AgentTurnContext",
    "BaseAgentHook",
    "MalformedToolHistoryHook",
    "PlanBackpressureHook",
    "SkillMethodContextHook",
]
