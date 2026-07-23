"""Domain-neutral Tool registration surface consumed by the Agent Loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class AgentToolRegistryError(RuntimeError):
    pass


class AgentToolFatalError(RuntimeError):
    """A Tool failure whose cleanup or control-plane state must abort the run."""


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    name: str
    description: str
    prompt_hint: str
    input_model: type[BaseModel]

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 128:
            raise ValueError("Tool name 必须为 1-128 个字符")
        if not self.description.strip():
            raise ValueError(f"Tool {self.name} 缺少 description")
        if not self.prompt_hint.strip():
            raise ValueError(f"Tool {self.name} 缺少 prompt_hint")

    def model_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


@dataclass(frozen=True, slots=True)
class AgentToolInvocation:
    name: str
    arguments: dict[str, Any]
    tool_call_id: str
    state: Mapping[str, Any]


AgentToolHandler = Callable[
    [AgentToolInvocation],
    Awaitable[Mapping[str, Any]],
]


class AgentToolRegistry:
    """Instance-owned registry for control, Skill, and domain Tool handlers."""

    def __init__(self) -> None:
        self._definitions: dict[str, AgentToolDefinition] = {}
        self._handlers: dict[str, AgentToolHandler] = {}

    def register(
        self,
        definition: AgentToolDefinition,
        handler: AgentToolHandler,
    ) -> None:
        if definition.name in self._definitions:
            raise AgentToolRegistryError(f"Tool 已注册：{definition.name}")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    @property
    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return tuple(self._definitions.values())

    def model_definitions(self) -> list[dict[str, Any]]:
        return [definition.model_definition() for definition in self.definitions]

    def prompt_inventory(self) -> str:
        return "\n".join(
            f"- {definition.name}: {definition.prompt_hint}"
            for definition in self.definitions
        )

    async def invoke(
        self,
        invocation: AgentToolInvocation,
    ) -> Mapping[str, Any]:
        try:
            handler = self._handlers[invocation.name]
        except KeyError as exc:
            raise AgentToolRegistryError(
                f"未知 Tool：{invocation.name}"
            ) from exc
        return await handler(invocation)


__all__ = [
    "AgentToolDefinition",
    "AgentToolFatalError",
    "AgentToolHandler",
    "AgentToolInvocation",
    "AgentToolRegistry",
    "AgentToolRegistryError",
]
