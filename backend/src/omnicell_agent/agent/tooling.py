"""Domain-neutral Tool registration surface consumed by the Agent Loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


class AgentToolRegistryError(RuntimeError):
    pass


class AgentToolFatalError(RuntimeError):
    """A Tool failure whose cleanup or control-plane state must abort the run."""


def render_tool_outcome(
    *,
    status: Literal["completed", "failed"],
    capability: str,
    summary: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
    recovery_hint: str | None = None,
    evidence_artifact_ids: list[str] | None = None,
    evidence_handle: str | None = None,
    plan_task_id: str | None = None,
) -> str:
    """Render the single model-facing outcome envelope for every Tool."""

    if status == "failed" and (
        not error_code
        or retryable is None
        or not recovery_hint
    ):
        raise ValueError(
            "failed Tool outcome 必须包含 error_code、retryable 与 recovery_hint"
        )
    payload: dict[str, Any] = {
        "status": status,
        "capability": capability,
        "summary": summary,
    }
    if result is not None:
        payload["result"] = result
    if error_code is not None:
        payload["error_code"] = error_code
    if retryable is not None:
        payload["retryable"] = retryable
    if recovery_hint is not None:
        payload["recovery_hint"] = recovery_hint
    if evidence_artifact_ids:
        payload["evidence_artifact_ids"] = evidence_artifact_ids
    if evidence_handle:
        payload["evidence_handle"] = evidence_handle
    if plan_task_id:
        payload["plan_task_id"] = plan_task_id
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    name: str
    description: str
    prompt_hint: str
    input_model: type[BaseModel]
    category: Literal["control", "skill", "domain"] = "domain"
    required_skills: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 128:
            raise ValueError("Tool name 必须为 1-128 个字符")
        if not self.description.strip():
            raise ValueError(f"Tool {self.name} 缺少 description")
        if not self.prompt_hint.strip():
            raise ValueError(f"Tool {self.name} 缺少 prompt_hint")
        if any(not name.strip() for name in self.required_skills):
            raise ValueError(f"Tool {self.name} required_skills 非法")

    def model_definition(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        artifact_ref = schema.get("$defs", {}).get("ArtifactRef")
        if isinstance(artifact_ref, dict):
            artifact_ref.clear()
            artifact_ref.update(
                {
                    "additionalProperties": False,
                    "description": (
                        "当前 conversation 中已登记 artifact 的稳定句柄；"
                        "backend 会在执行前还原并验证完整权威引用。"
                    ),
                    "properties": {
                        "artifact_id": {
                            "format": "uuid",
                            "type": "string",
                        }
                    },
                    "required": ["artifact_id"],
                    "title": "ArtifactHandle",
                    "type": "object",
                }
            )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
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
SkillBodyValidator = Callable[[str, Mapping[str, Any]], bool]
SkillResourceSanitizer = Callable[
    [list[Any]],
    tuple[list[dict[str, Any]], list[Any]],
]


class AgentToolRegistry:
    """Instance-owned registry for control, Skill, and domain Tool handlers."""

    def __init__(
        self,
        *,
        skill_body_validator: SkillBodyValidator | None = None,
        skill_resource_sanitizer: SkillResourceSanitizer | None = None,
    ) -> None:
        self._definitions: dict[str, AgentToolDefinition] = {}
        self._handlers: dict[str, AgentToolHandler] = {}
        self._stale_skill_handlers: set[str] = set()
        self._skill_body_validator = (
            skill_body_validator or _default_skill_body_validator
        )
        self._skill_resource_sanitizer = (
            skill_resource_sanitizer
            or _default_skill_resource_sanitizer
        )

    def register(
        self,
        definition: AgentToolDefinition,
        handler: AgentToolHandler,
        *,
        handles_stale_skill_resources: bool = False,
    ) -> None:
        if definition.name in self._definitions:
            raise AgentToolRegistryError(f"Tool 已注册：{definition.name}")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler
        if handles_stale_skill_resources:
            self._stale_skill_handlers.add(definition.name)

    @property
    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return tuple(self._definitions.values())

    def visible_definitions(
        self,
        loaded_skill_resources: (
            list[dict[str, Any]] | tuple[dict[str, Any], ...]
        ) = (),
    ) -> tuple[AgentToolDefinition, ...]:
        loaded_skills = {
            str(resource.get("skill_name"))
            for resource in loaded_skill_resources
            if isinstance(resource, dict)
            and resource.get("skill_name")
            and resource.get("resource_kind") == "body"
            and self._skill_body_validator(
                str(resource["skill_name"]),
                resource,
            )
        }
        return tuple(
            definition
            for definition in self.definitions
            if not definition.required_skills
            or set(definition.required_skills).issubset(loaded_skills)
        )

    def model_definitions(
        self,
        loaded_skill_resources: (
            list[dict[str, Any]] | tuple[dict[str, Any], ...]
        ) = (),
    ) -> list[dict[str, Any]]:
        return [
            definition.model_definition()
            for definition in self.visible_definitions(loaded_skill_resources)
        ]

    def prompt_inventory(self) -> str:
        return "\n".join(
            (
                f"- {definition.name}: {definition.prompt_hint}"
                + (
                    " [加载 "
                    + ", ".join(definition.required_skills)
                    + " 后可用]"
                    if definition.required_skills
                    else ""
                )
            )
            for definition in self.definitions
        )

    def sanitize_skill_resources(
        self,
        resources: list[Any],
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        return self._skill_resource_sanitizer(resources)

    def handles_stale_skill_resources(self, name: str) -> bool:
        return name in self._stale_skill_handlers

    def definition(self, name: str) -> AgentToolDefinition | None:
        return self._definitions.get(name)

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
    "render_tool_outcome",
]


def _default_skill_body_validator(
    skill_name: str,
    resource: Mapping[str, Any],
) -> bool:
    """Fallback for domain-neutral registries without a Skill Catalog."""

    return (
        resource.get("skill_name") == skill_name
        and resource.get("resource_kind") == "body"
    )


def _default_skill_resource_sanitizer(
    resources: list[Any],
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Fallback for domain-neutral registries without versioned Skills."""

    return (
        [dict(resource) for resource in resources if isinstance(resource, dict)],
        [resource for resource in resources if not isinstance(resource, dict)],
    )
