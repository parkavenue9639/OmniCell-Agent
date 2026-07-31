"""OmniCell composition root for the domain-neutral Agent Loop."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid5

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import interrupt
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer
from omnicell_agent.capabilities.artifacts import ArtifactBoundaryError
from omnicell_agent.capabilities.catalog import (
    SkillCatalogError,
    SkillContextLimitError,
)
from omnicell_agent.capabilities.contracts import ArtifactRef, CapabilityStatus
from omnicell_agent.capabilities.errors import (
    CapabilityExecutionError,
    CapabilityInputError,
    PUBLIC_CAPABILITY_FAILURE_SUMMARY,
    PUBLIC_CAPABILITY_NOT_COMPLETED_SUMMARY,
)
from omnicell_agent.capabilities.registry import CapabilityContext
from omnicell_agent.llm.factory import LLMFactory
from omnicell_agent.llm.types import LLMRole
from omnicell_agent.runs.status import ReviewDecision, TaskStatus

from .cancellation import CancellationToken
from .capability_process import (
    CapabilityInvokerFactory,
    RuntimeCleanupError,
    SubprocessCapabilityInvoker,
)
from .executor import AsyncCapabilityExecutor
from .hooks import (
    AgentHook,
    MalformedToolHistoryHook,
    MemoryContextHook,
    MemoryContextResolver,
    PlanBackpressureHook,
    ScientificEvidenceCompletionHook,
    SkillMethodContextHook,
)
from .loop import (
    AgentExecution,
    AgentLoopConfig,
    ReviewInterrupt,
    ReviewResolution,
)
from .observer import AgentObserver, NullAgentObserver
from .memory import AgentMemoryControlError, AgentMemoryControlPort
from .memory_policy import (
    FORGET_MEMORY_DESCRIPTION,
    FORGET_MEMORY_PROMPT_HINT,
    PROPOSE_MEMORY_DESCRIPTION,
    PROPOSE_MEMORY_PROMPT_HINT,
    SEARCH_MEMORY_DESCRIPTION,
    SEARCH_MEMORY_PROMPT_HINT,
)
from .policy import DefaultToolPolicy, ToolPolicy, ToolPolicyOutcome
from .response_contract import render_response_contract
from .resource_boundary import contains_internal_resource_locator
from .scientific_evidence import (
    deterministic_scientific_fallback,
    project_scientific_evidence,
    scientific_evidence_from_state,
    validate_scientific_final_response,
)
from .tooling import (
    AgentToolDefinition,
    AgentToolInvocation,
    AgentToolRegistry,
    render_tool_outcome,
)


logger = logging.getLogger(__name__)

_REVIEW_NAMESPACE = UUID("510b1e62-91cb-49ab-887e-b06bdc7f148e")
_PLAN_NAMESPACE = UUID("f5f693f6-f36b-4b75-9864-eb5ba865c10e")


def _tool_call_fingerprint(name: str, arguments: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"name": name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _collect_artifact_ids(value: Any) -> list[str]:
    found: list[str] = []

    def visit(current: Any) -> None:
        if isinstance(current, dict):
            artifact_id = current.get("artifact_id")
            conversation_id = current.get("conversation_id")
            if artifact_id and conversation_id:
                text = str(artifact_id)
                if text not in found:
                    found.append(text)
            for nested in current.values():
                visit(nested)
        elif isinstance(current, list):
            for nested in current:
                visit(nested)

    visit(value)
    return found[:128]


def _model_visible_result(value: Any) -> Any:
    """Remove internal resource locators while preserving bounded typed facts."""

    if isinstance(value, dict):
        return {
            key: _model_visible_result(nested)
            for key, nested in value.items()
            if key not in {"uri", "host_path", "workspace_path"}
        }
    if isinstance(value, list):
        return [_model_visible_result(nested) for nested in value]
    return value


def _failed_tool_update(
    invocation: AgentToolInvocation,
    *,
    error_code: str,
    summary: str,
    retryable: bool,
    recovery_hint: str,
    failure_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    update: dict[str, Any] = {
        "messages": [
            ToolMessage(
                content=render_tool_outcome(
                    status="failed",
                    capability=invocation.name,
                    summary=summary,
                    error_code=error_code,
                    retryable=retryable,
                    recovery_hint=recovery_hint,
                ),
                tool_call_id=invocation.tool_call_id,
            )
        ],
        "task_status": TaskStatus.IN_PROGRESS.value,
    }
    if failure_counts is not None:
        update["tool_failure_counts"] = failure_counts
    return update


def _plan_transition_rejected(
    invocation: AgentToolInvocation,
    summary: str,
) -> dict[str, Any]:
    return {
        "messages": [
            ToolMessage(
                content=render_tool_outcome(
                    status="failed",
                    capability="update_task_plan",
                    summary=summary,
                    error_code="plan_transition_invalid",
                    retryable=False,
                    recovery_hint=(
                        "根据当前计划状态、步骤依赖和已验证 Tool 证据重新选择更新。"
                    ),
                ),
                tool_call_id=invocation.tool_call_id,
            )
        ]
    }


class _LoadSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    reference: str | None = Field(default=None, max_length=256)
    example: str | None = Field(default=None, max_length=256)
    purpose: Literal[
        "domain_method",
        "validation_rules",
        "workflow_guidance",
        "reference_lookup",
        "example_lookup",
    ] = "domain_method"


class _FinishTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_response: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=20_000,
        ),
    ]
    evidence_artifact_ids: list[UUID] = Field(default_factory=list, max_length=128)
    limitations: list[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=2_000,
            ),
        ]
    ] = Field(default_factory=list, max_length=20)

    @field_validator("final_response")
    @classmethod
    def _no_internal_resource_locator(cls, value: str) -> str:
        if contains_internal_resource_locator(value):
            raise ValueError("最终回复不得包含内部资源定位符")
        return value


class _PlanStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=2_000)
    success_criteria: str = Field(min_length=1, max_length=2_000)
    depends_on: list[int] = Field(default_factory=list, max_length=11)
    capability_hint: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class _CreateTaskPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=2_000)
    steps: list[_PlanStepInput] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def _validate_dependencies(self) -> "_CreateTaskPlanInput":
        for index, step in enumerate(self.steps, start=1):
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("计划步骤依赖不能重复")
            if any(dep < 1 or dep >= index for dep in step.depends_on):
                raise ValueError("计划步骤只能依赖此前的步骤编号")
        return self


class _UpdateTaskPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: Literal["in_progress", "completed", "failed", "cancelled"]
    summary: str | None = Field(default=None, max_length=2_000)
    evidence_tool_call_ids: list[str] = Field(default_factory=list, max_length=20)


MemoryKindLiteral = Literal[
    "response_preference",
    "profile_fact",
    "project_context",
]
ProposableMemoryKindLiteral = Literal[
    "response_preference",
    "profile_fact",
    "project_context",
]


class _SearchMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[MemoryKindLiteral] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=5, ge=1, le=10)


class _MemoryResourceProjection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: UUID
    version_id: UUID
    version_number: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: MemoryKindLiteral
    source_kind: Literal["explicit", "proposed", "corrected"]
    selection_reason: Literal["default", "selected", "tool_search"]


class _ProposeMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProposableMemoryKindLiteral
    source_message_id: UUID


class _ForgetMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    version_id: UUID


class _OmniCellToolComposition:
    def __init__(
        self,
        *,
        run_id: UUID,
        capabilities: DomainCapabilityLayer,
        capability_context: CapabilityContext,
        executor: AsyncCapabilityExecutor,
        observer: AgentObserver,
        policy: ToolPolicy,
        memory_tools: AgentMemoryControlPort | None = None,
    ) -> None:
        self._run_id = run_id
        self._capabilities = capabilities
        self._capability_context = capability_context
        self._executor = executor
        self._observer = observer
        self._policy = policy
        self._memory_tools = memory_tools

    def _hydrate_artifact_handles(self, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) == {"artifact_id"}:
                try:
                    artifact_id = UUID(str(value["artifact_id"]))
                except (TypeError, ValueError) as exc:
                    raise ArtifactBoundaryError(
                        "artifact_id 不是有效 UUID"
                    ) from exc
                return self._capability_context.artifacts.reference_by_id(
                    artifact_id
                ).model_dump(mode="json")
            return {
                key: self._hydrate_artifact_handles(nested)
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [
                self._hydrate_artifact_handles(nested)
                for nested in value
            ]
        return value

    def build(self) -> AgentToolRegistry:
        registry = AgentToolRegistry(
            skill_body_validator=self._skill_body_is_current,
            skill_resource_sanitizer=self._sanitize_skill_resources,
        )
        registry.register(
            AgentToolDefinition(
                name="load_skill",
                description=(
                    "按需加载一个已注册 Skill 的详细正文，或其 reference/example 子文档。"
                ),
                prompt_hint=(
                    "根据 Skill 摘要判断任务确实需要领域方法、组合规则或验证标准时再调用；"
                    "简单问答和契约充分的单一原子 Tool 不必加载，且不要重复加载同一资源。"
                ),
                input_model=_LoadSkillInput,
                category="skill",
            ),
            self._load_skill,
            handles_stale_skill_resources=True,
        )
        registry.register(
            AgentToolDefinition(
                name="create_task_plan",
                description="为复合目标创建或替换一个有界的显式任务计划。",
                prompt_hint=(
                    "仅当目标包含至少两个相互依赖、可分别验证的步骤时调用；"
                    "简单问答和单能力任务不要建计划。"
                ),
                input_model=_CreateTaskPlanInput,
                category="control",
            ),
            self._create_task_plan,
        )
        registry.register(
            AgentToolDefinition(
                name="update_task_plan",
                description="更新当前显式计划中一个步骤的权威状态。",
                prompt_hint=(
                    "领域 Tool 成功后会自动完成当前活动步骤并激活下一步；"
                    "仅在需要手工标记 failed/cancelled 或补充特殊证据时调用。"
                ),
                input_model=_UpdateTaskPlanInput,
                category="control",
            ),
            self._update_task_plan,
        )
        registry.register(
            AgentToolDefinition(
                name="finish_task",
                description="可选地以结构化证据结束已经收敛的目标。",
                prompt_hint=(
                    "仅在需要显式声明 evidence artifact 或 limitations 时调用；"
                    "普通问答和单 Tool 总结直接返回非空最终文本，不要调用本 Tool。"
                ),
                input_model=_FinishTaskInput,
                category="control",
            ),
            self._finish_task,
        )
        if self._memory_tools is not None:
            registry.register(
                AgentToolDefinition(
                    name="search_memory",
                    description=SEARCH_MEMORY_DESCRIPTION,
                    prompt_hint=SEARCH_MEMORY_PROMPT_HINT,
                    input_model=_SearchMemoryInput,
                    category="control",
                ),
                self._search_memory,
            )
            registry.register(
                AgentToolDefinition(
                    name="propose_memory",
                    description=PROPOSE_MEMORY_DESCRIPTION,
                    prompt_hint=PROPOSE_MEMORY_PROMPT_HINT,
                    input_model=_ProposeMemoryInput,
                    category="control",
                ),
                self._propose_memory,
            )
            registry.register(
                AgentToolDefinition(
                    name="forget_memory",
                    description=FORGET_MEMORY_DESCRIPTION,
                    prompt_hint=FORGET_MEMORY_PROMPT_HINT,
                    input_model=_ForgetMemoryInput,
                    category="control",
                ),
                self._request_forget_memory,
            )
        for spec in self._capabilities.registry.specs:
            handler = self._capabilities.registry.get(spec.name)
            registry.register(
                AgentToolDefinition(
                    name=spec.name,
                    description=spec.model_description(),
                    prompt_hint=spec.prompt_hint,
                    input_model=handler.request_model,
                    category="domain",
                    required_skills=spec.required_skills,
                ),
                self._invoke_domain_tool,
                handles_stale_skill_resources=True,
            )
        return registry

    @staticmethod
    def _memory_failure(
        invocation: AgentToolInvocation,
        error: AgentMemoryControlError,
    ) -> dict[str, Any]:
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="failed",
                        capability=invocation.name,
                        summary=error.summary,
                        error_code=error.error_code,
                        retryable=error.retryable,
                        recovery_hint=error.recovery_hint,
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ]
        }

    async def _search_memory(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        assert self._memory_tools is not None
        request = _SearchMemoryInput.model_validate(invocation.arguments)
        try:
            raw_identities = await self._memory_tools.search(
                kinds=tuple(request.kinds),
                limit=request.limit,
                tool_call_id=invocation.tool_call_id,
            )
        except AgentMemoryControlError as exc:
            return self._memory_failure(invocation, exc)
        try:
            identities = tuple(
                _MemoryResourceProjection.model_validate(item).model_dump(
                    mode="json"
                )
                for item in raw_identities
            )
        except ValueError:
            return self._memory_failure(
                invocation,
                AgentMemoryControlError(
                    error_code="memory_control_contract_invalid",
                    summary="Memory control adapter 返回了无效 identity。",
                    retryable=False,
                    recovery_hint=(
                        "停止使用本次搜索结果并检查 backend Memory adapter。"
                    ),
                ),
            )
        existing = [
            dict(item)
            for item in invocation.state.get("loaded_memory_resources", [])
            if isinstance(item, dict)
        ]
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in [*existing, *(dict(item) for item in identities)]:
            identity = (
                str(item.get("item_id") or ""),
                str(item.get("version_id") or ""),
            )
            if not all(identity) or identity in seen:
                continue
            seen.add(identity)
            merged.append(item)
            if len(merged) >= 32:
                break
        public_identities = list(identities)
        await self._observer.emit(
            "memory.search_completed",
            {
                "tool_call_id": invocation.tool_call_id,
                "outcome": "loaded" if public_identities else "empty",
                "inputs": [
                    {
                        key: item[key]
                        for key in (
                            "item_id",
                            "version_id",
                            "version_number",
                            "kind",
                            "source_kind",
                            "selection_reason",
                        )
                    }
                    for item in public_identities
                ],
            },
            dedupe_key=(
                f"memory:search-completed:{invocation.tool_call_id}"
            ),
        )
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="completed",
                        capability="search_memory",
                        summary=(
                            f"找到 {len(public_identities)} 条可用记忆 identity；"
                            "正文将在下一轮由 backend 瞬时解析。"
                        ),
                        result={"memories": public_identities},
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "loaded_memory_resources": merged,
        }

    async def _propose_memory(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        assert self._memory_tools is not None
        request = _ProposeMemoryInput.model_validate(invocation.arguments)
        try:
            identity = await self._memory_tools.propose(
                kind=request.kind,
                source_message_id=request.source_message_id,
                tool_call_id=invocation.tool_call_id,
            )
        except AgentMemoryControlError as exc:
            return self._memory_failure(invocation, exc)
        await self._observer.emit(
            "memory.proposal_created",
            {
                "tool_call_id": invocation.tool_call_id,
                "memory": {
                    key: identity[key]
                    for key in (
                        "item_id",
                        "version_id",
                        "version_number",
                        "kind",
                        "source_kind",
                        "selection_reason",
                    )
                    if key in identity
                },
                "status": "proposed",
            },
            dedupe_key=f"memory:proposal-created:{invocation.tool_call_id}",
        )
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="completed",
                        capability="propose_memory",
                        summary="已创建待用户确认的记忆提议。",
                        result={
                            key: identity[key]
                            for key in (
                                "item_id",
                                "version_id",
                                "version_number",
                                "content_sha256",
                                "kind",
                                "status",
                            )
                            if key in identity
                        },
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ]
        }

    async def _request_forget_memory(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        assert self._memory_tools is not None
        request = _ForgetMemoryInput.model_validate(invocation.arguments)
        try:
            intent = await self._memory_tools.request_forget(
                item_id=request.item_id,
                version_id=request.version_id,
                tool_call_id=invocation.tool_call_id,
            )
        except AgentMemoryControlError as exc:
            return self._memory_failure(invocation, exc)
        await self._observer.emit(
            "memory.forget_requested",
            {
                "tool_call_id": invocation.tool_call_id,
                "memory": {
                    key: intent[key]
                    for key in (
                        "item_id",
                        "version_id",
                        "version_number",
                        "kind",
                        "source_kind",
                        "selection_reason",
                    )
                    if key in intent
                },
                "status": "confirmation_required",
            },
            dedupe_key=f"memory:forget-requested:{invocation.tool_call_id}",
        )
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="completed",
                        capability="forget_memory",
                        summary=(
                            "已验证目标记忆；必须由用户在记忆管理界面确认 "
                            "revoke 或 purge，当前 Tool 不执行删除。"
                        ),
                        result={
                            key: intent[key]
                            for key in (
                                "item_id",
                                "version_id",
                                "status",
                            )
                            if key in intent
                        },
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ]
        }

    def _skill_body_is_current(
        self,
        skill_name: str,
        resource: Mapping[str, Any],
    ) -> bool:
        if (
            not isinstance(resource, dict)
            or resource.get("skill_name") != skill_name
            or resource.get("resource_kind") != "body"
        ):
            return False
        try:
            self._capabilities.skills.load_resource(resource)
        except SkillCatalogError:
            return False
        return True

    def _validated_skill_resources(
        self,
        resources: list[Any],
    ) -> tuple[list[dict[str, Any]], set[str], list[Any]]:
        identity_valid: list[dict[str, Any]] = []
        invalid_resources: list[Any] = []
        for resource in resources:
            if not isinstance(resource, dict):
                invalid_resources.append(resource)
                continue
            try:
                self._capabilities.skills.load_resource(resource)
            except SkillCatalogError:
                invalid_resources.append(resource)
                continue
            identity_valid.append(dict(resource))
        valid_bodies = [
            resource
            for resource in identity_valid
            if resource.get("resource_kind") == "body"
        ]
        valid_body_names = {
            str(resource.get("skill_name"))
            for resource in valid_bodies
        }
        valid_children: list[dict[str, Any]] = []
        for resource in identity_valid:
            if resource.get("resource_kind") == "body":
                continue
            if str(resource.get("skill_name")) not in valid_body_names:
                invalid_resources.append(resource)
                continue
            valid_children.append(resource)
        valid_resources = [*valid_bodies, *valid_children]
        return valid_resources, valid_body_names, invalid_resources

    def _sanitize_skill_resources(
        self,
        resources: list[Any],
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        valid, _, invalid = self._validated_skill_resources(resources)
        return valid, invalid

    async def _load_skill(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        request = _LoadSkillInput.model_validate(invocation.arguments)
        resource_kind = (
            "reference"
            if request.reference
            else "example" if request.example else "body"
        )
        resource_name = request.reference or request.example
        base_activity = {
            "tool_call_id": invocation.tool_call_id,
            "skill_name": request.skill_name,
            "resource_kind": resource_kind,
            "resource_name": resource_name,
            "purpose": request.purpose,
        }
        raw_loaded = list(
            invocation.state.get("loaded_skill_resources", [])
        )
        loaded, _, invalid_resources = self._validated_skill_resources(
            raw_loaded
        )
        if invalid_resources:
            invalid_names = sorted(
                {
                    str(resource.get("skill_name"))
                    for resource in invalid_resources
                    if isinstance(resource, dict)
                    and resource.get("skill_name")
                }
            )
            await self._observer.emit(
                "skill.load_started",
                base_activity,
                dedupe_key=f"skill:{invocation.tool_call_id}:started",
            )
            content = render_tool_outcome(
                status="failed",
                capability="load_skill",
                summary="已加载的 Skill 资源与当前目录版本或内容不一致。",
                error_code="skill_context_stale",
                retryable=True,
                recovery_hint=(
                    "已清理失效方法上下文；请重新调用 load_skill"
                    + (
                        " 加载：" + ", ".join(invalid_names)
                        if invalid_names
                        else "。"
                    )
                ),
            )
            await self._observer.emit(
                "skill.load_failed",
                {
                    **base_activity,
                    "error_code": "skill_context_stale",
                },
                dedupe_key=f"skill:{invocation.tool_call_id}:failed",
            )
            return {
                "messages": [
                    ToolMessage(
                        content=content,
                        tool_call_id=invocation.tool_call_id,
                    )
                ],
                "loaded_skill_resources": loaded,
            }
        try:
            identity, method_content = (
                self._capabilities.skills.resolve_resource(
                    request.skill_name,
                    reference=request.reference,
                    example=request.example,
                )
            )
        except SkillCatalogError as exc:
            await self._observer.emit(
                "skill.load_started",
                base_activity,
                dedupe_key=f"skill:{invocation.tool_call_id}:started",
            )
            logger.info(
                "skill resource load failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": str(self._run_id),
                    "skill": request.skill_name,
                    "resource_kind": resource_kind,
                },
            )
            content = render_tool_outcome(
                status="failed",
                capability="load_skill",
                summary="Skill 资源不存在或不符合加载契约。",
                error_code="skill_resource_unavailable",
                retryable=False,
                recovery_hint="从系统提示列出的 Skill 名称中重新选择。",
            )
            await self._observer.emit(
                "skill.load_failed",
                base_activity,
                dedupe_key=f"skill:{invocation.tool_call_id}:failed",
            )
        else:
            resource = identity.model_dump(mode="json")
            activity = {
                **base_activity,
                "skill_version": identity.skill_version,
                "resource_sha256": identity.resource_sha256,
            }
            await self._observer.emit(
                "skill.load_started",
                activity,
                dedupe_key=f"skill:{invocation.tool_call_id}:started",
            )
            body_loaded = any(
                self._skill_body_is_current(identity.skill_name, item)
                for item in loaded
            )
            if identity.resource_kind != "body" and not body_loaded:
                content = render_tool_outcome(
                    status="failed",
                    capability="load_skill",
                    summary="加载 Skill 子资源前必须先加载同版本正文。",
                    error_code="skill_body_required",
                    retryable=False,
                    recovery_hint=(
                        f"先加载 {identity.skill_name} 的 body，"
                        "再按需加载 reference/example。"
                    ),
                )
                await self._observer.emit(
                    "skill.load_failed",
                    {
                        **activity,
                        "error_code": "skill_body_required",
                    },
                    dedupe_key=f"skill:{invocation.tool_call_id}:failed",
                )
            elif resource in loaded:
                content = render_tool_outcome(
                    status="completed",
                    capability="load_skill",
                    summary="该 Skill 资源已经加载，继续使用当前方法上下文。",
                    result={
                        "skill_name": request.skill_name,
                        "resource": resource,
                        "outcome": "already_loaded",
                    },
                )
                await self._observer.emit(
                    "skill.load_completed",
                    {
                        **activity,
                        "outcome": "already_loaded",
                        "content_bytes": 0,
                    },
                    dedupe_key=f"skill:{invocation.tool_call_id}:completed",
                )
            else:
                candidate_resources = [*loaded, resource]
                try:
                    self._capabilities.skills.render_loaded_context(
                        candidate_resources
                    )
                except SkillContextLimitError:
                    content = render_tool_outcome(
                        status="failed",
                        capability="load_skill",
                        summary="加载该资源会超过本次 run 的 Skill 方法上下文上限。",
                        error_code="skill_context_limit_exceeded",
                        retryable=False,
                        recovery_hint=(
                            "继续使用已加载方法，避免加载不必要的 reference/example；"
                            "如仍不足，请缩小任务范围。"
                        ),
                    )
                    await self._observer.emit(
                        "skill.load_failed",
                        {
                            **activity,
                            "error_code": "skill_context_limit_exceeded",
                        },
                        dedupe_key=f"skill:{invocation.tool_call_id}:failed",
                    )
                except SkillCatalogError:
                    loaded, _, _ = self._validated_skill_resources(
                        loaded
                    )
                    content = render_tool_outcome(
                        status="failed",
                        capability="load_skill",
                        summary="Skill 方法上下文在加载期间发生版本或内容变化。",
                        error_code="skill_context_stale",
                        retryable=True,
                        recovery_hint=(
                            "已清理失效方法上下文；请重新调用 load_skill。"
                        ),
                    )
                    await self._observer.emit(
                        "skill.load_failed",
                        {
                            **activity,
                            "error_code": "skill_context_stale",
                        },
                        dedupe_key=f"skill:{invocation.tool_call_id}:failed",
                    )
                else:
                    loaded = candidate_resources
                    content = render_tool_outcome(
                        status="completed",
                        capability="load_skill",
                        summary="Skill 资源已加入本次 run 的方法上下文。",
                        result={
                            "skill_name": request.skill_name,
                            "resource": resource,
                            "outcome": "loaded",
                        },
                    )
                    await self._observer.emit(
                        "skill.load_completed",
                        {
                            **activity,
                            "outcome": "loaded",
                            "content_bytes": len(method_content.encode("utf-8")),
                        },
                        dedupe_key=f"skill:{invocation.tool_call_id}:completed",
                    )
        return {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "loaded_skill_resources": loaded,
        }

    async def _create_task_plan(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        plan = _CreateTaskPlanInput.model_validate(invocation.arguments)
        previous_statuses = dict(
            invocation.state.get("plan_task_statuses", {})
        )
        revision = int(invocation.state.get("plan_revision", 0)) + 1
        task_ids = [
            str(
                uuid5(
                    _PLAN_NAMESPACE,
                    f"{self._run_id}:plan:{revision}:step:{index}",
                )
            )
            for index in range(1, len(plan.steps) + 1)
        ]
        capability_names = {
            spec.name for spec in self._capabilities.registry.specs
        }
        unknown_hints = sorted(
            {
                step.capability_hint
                for step in plan.steps
                if step.capability_hint
                and step.capability_hint not in capability_names
            }
        )
        if unknown_hints:
            return {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability="create_task_plan",
                            summary="计划包含未注册的 capability hint。",
                            error_code="plan_capability_unknown",
                            retryable=False,
                            recovery_hint=(
                                "移除或改为已注册能力："
                                + ", ".join(unknown_hints)
                            ),
                        ),
                        tool_call_id=invocation.tool_call_id,
                    )
                ]
            }
        task_statuses = {
            task_id: TaskStatus.PENDING.value for task_id in task_ids
        }
        step_definitions: dict[str, dict[str, Any]] = {}
        rendered_steps: list[dict[str, Any]] = []
        for index, (task_id_text, step) in enumerate(
            zip(task_ids, plan.steps, strict=True),
            start=1,
        ):
            dependency_ids = [
                task_ids[dependency_index - 1]
                for dependency_index in step.depends_on
            ]
            step_definitions[task_id_text] = {
                "index": index,
                "title": step.title,
                "objective": step.objective,
                "success_criteria": step.success_criteria,
                "depends_on": dependency_ids,
                "capability_hint": step.capability_hint,
            }
            rendered_steps.append(
                {
                    "task_id": task_id_text,
                    "title": step.title,
                    "objective": step.objective,
                    "success_criteria": step.success_criteria,
                    "depends_on": dependency_ids,
                    "capability_hint": step.capability_hint,
                    "status": TaskStatus.PENDING.value,
                }
            )
        # 新计划已经完成全部 schema、依赖和 capability 校验后，才允许
        # 对旧计划发出 replacement 事件，避免事件事实与 checkpoint 分叉。
        for previous_task_id, previous_status in previous_statuses.items():
            if previous_status in {
                TaskStatus.PENDING.value,
                TaskStatus.IN_PROGRESS.value,
            }:
                await self._observer.emit(
                    "task.updated",
                    {
                        "task_id": previous_task_id,
                        "status": TaskStatus.CANCELLED.value,
                        "summary": "计划已被新修订替换",
                    },
                    dedupe_key=f"task:{previous_task_id}:replaced",
                )
        for index, step in enumerate(rendered_steps, start=1):
            await self._observer.emit(
                "task.created",
                {
                    "task_id": step["task_id"],
                    "tool_call_id": f"agent-plan:{revision}:{index}",
                    "title": step["title"],
                    "description": (
                        f"{step['objective']}\n验收：{step['success_criteria']}"
                    ),
                    "capability_name": step["capability_hint"],
                },
                dedupe_key=f"task:{step['task_id']}:created",
            )
        active_task_id = next(
            (
                task_id
                for task_id in task_ids
                if not step_definitions[task_id]["depends_on"]
            ),
            "",
        )
        if active_task_id:
            task_statuses[active_task_id] = TaskStatus.IN_PROGRESS.value
            await self._observer.emit(
                "task.updated",
                {
                    "task_id": active_task_id,
                    "status": TaskStatus.IN_PROGRESS.value,
                    "summary": "计划已激活首个无依赖步骤",
                },
                dedupe_key=f"task:{active_task_id}:activated",
            )
            for step in rendered_steps:
                if step["task_id"] == active_task_id:
                    step["status"] = TaskStatus.IN_PROGRESS.value
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="completed",
                        capability="create_task_plan",
                        summary="显式计划已创建并激活首个可执行步骤。",
                        result={
                            "plan_revision": revision,
                            "rationale": plan.rationale,
                            "steps": rendered_steps,
                        },
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "plan_revision": revision,
            "plan_task_ids": task_ids,
            "plan_task_statuses": task_statuses,
            "plan_step_definitions": step_definitions,
            "plan_step_evidence": {},
            "active_plan_task_id": active_task_id,
            "task_status": TaskStatus.IN_PROGRESS.value,
        }

    async def _update_task_plan(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        update = _UpdateTaskPlanInput.model_validate(invocation.arguments)
        task_id_text = str(update.task_id)
        statuses = dict(invocation.state.get("plan_task_statuses", {}))
        definitions = dict(
            invocation.state.get("plan_step_definitions", {})
        )
        evidence = {
            key: list(value)
            for key, value in invocation.state.get(
                "plan_step_evidence",
                {},
            ).items()
        }
        if task_id_text not in statuses:
            return _failed_tool_update(
                invocation,
                error_code="plan_task_unknown",
                summary="计划步骤不存在或已经被新计划替换。",
                retryable=False,
                recovery_hint="读取当前计划中的 task_id 后重新选择步骤。",
            )
        current_status = statuses[task_id_text]
        active_task_id = str(
            invocation.state.get("active_plan_task_id") or ""
        )
        definition = definitions.get(task_id_text, {})
        if update.status == TaskStatus.IN_PROGRESS.value:
            dependencies = list(definition.get("depends_on", []))
            if current_status != TaskStatus.PENDING.value:
                return _plan_transition_rejected(
                    invocation,
                    "只有 pending 步骤可以进入 in_progress。",
                )
            if active_task_id and active_task_id != task_id_text:
                return _plan_transition_rejected(
                    invocation,
                    "同一时间只能有一个活动计划步骤。",
                )
            if any(
                statuses.get(dependency) != TaskStatus.COMPLETED.value
                for dependency in dependencies
            ):
                return _plan_transition_rejected(
                    invocation,
                    "计划步骤依赖尚未完成。",
                )
            active_task_id = task_id_text
        elif update.status == TaskStatus.COMPLETED.value:
            tool_evidence = dict(invocation.state.get("tool_evidence", {}))
            consumed_elsewhere = [
                tool_call_id
                for tool_call_id in update.evidence_tool_call_ids
                if (
                    tool_evidence.get(tool_call_id, {}).get("plan_task_id")
                    not in {None, "", task_id_text}
                    or any(
                        task_id != task_id_text
                        and tool_call_id in handles
                        for task_id, handles in evidence.items()
                    )
                )
            ]
            missing = [
                tool_call_id
                for tool_call_id in update.evidence_tool_call_ids
                if tool_call_id not in tool_evidence
            ]
            mismatched = [
                tool_call_id
                for tool_call_id in update.evidence_tool_call_ids
                if definition.get("capability_hint")
                and tool_evidence.get(tool_call_id, {}).get("capability")
                != definition["capability_hint"]
            ]
            incomplete = [
                tool_call_id
                for tool_call_id in update.evidence_tool_call_ids
                if (
                    tool_evidence.get(tool_call_id, {}).get("result_status")
                    != CapabilityStatus.COMPLETED.value
                    or tool_evidence.get(tool_call_id, {}).get(
                        "scientific_goal_status"
                    )
                    in {"partial", "unverified"}
                )
            ]
            if current_status != TaskStatus.IN_PROGRESS.value:
                return _plan_transition_rejected(
                    invocation,
                    "只有 in_progress 步骤可以完成。",
                )
            if (
                not update.evidence_tool_call_ids
                or missing
                or mismatched
                or incomplete
                or consumed_elsewhere
            ):
                return _plan_transition_rejected(
                    invocation,
                    "完成步骤必须引用当前 run 中与 capability hint 一致的 "
                    "已验证且未被其他步骤消费的 Tool 调用证据。",
                )
            evidence[task_id_text] = list(update.evidence_tool_call_ids)
            for tool_call_id in update.evidence_tool_call_ids:
                tool_evidence[tool_call_id] = {
                    **tool_evidence[tool_call_id],
                    "plan_task_id": task_id_text,
                }
            active_task_id = ""
        else:
            if current_status not in {
                TaskStatus.PENDING.value,
                TaskStatus.IN_PROGRESS.value,
            }:
                return _plan_transition_rejected(
                    invocation,
                    "终态计划步骤不能再次改写。",
                )
            if not (update.summary or "").strip():
                return _plan_transition_rejected(
                    invocation,
                    "failed/cancelled 步骤必须说明原因。",
                )
            if active_task_id == task_id_text:
                active_task_id = ""
        await self._observer.emit(
            "task.updated",
            {
                "task_id": task_id_text,
                "status": update.status,
                "summary": update.summary,
            },
            dedupe_key=(
                f"task:{task_id_text}:{update.status}:"
                f"{hashlib.sha256((update.summary or '').encode('utf-8')).hexdigest()[:12]}"
            ),
        )
        statuses[task_id_text] = update.status
        if not active_task_id and update.status == TaskStatus.COMPLETED.value:
            active_task_id = await self._activate_next_plan_step(
                statuses,
                definitions,
            )
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="completed",
                        capability="update_task_plan",
                        summary="计划步骤状态已根据证据更新。",
                        result={
                            "task_id": task_id_text,
                            "status": update.status,
                            "summary": update.summary,
                            "evidence_tool_call_ids": update.evidence_tool_call_ids,
                            "next_active_task_id": active_task_id or None,
                        },
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "plan_task_statuses": statuses,
            "plan_step_evidence": evidence,
            "active_plan_task_id": active_task_id,
            "task_status": TaskStatus.IN_PROGRESS.value,
            **(
                {"tool_evidence": tool_evidence}
                if update.status == TaskStatus.COMPLETED.value
                else {}
            ),
        }

    async def _activate_next_plan_step(
        self,
        statuses: dict[str, str],
        definitions: dict[str, dict[str, Any]],
    ) -> str:
        ready = sorted(
            (
                (int(definition.get("index", 0)), task_id)
                for task_id, definition in definitions.items()
                if statuses.get(task_id) == TaskStatus.PENDING.value
                and all(
                    statuses.get(dependency) == TaskStatus.COMPLETED.value
                    for dependency in definition.get("depends_on", [])
                )
            ),
            key=lambda item: item[0],
        )
        if not ready:
            return ""
        task_id = ready[0][1]
        statuses[task_id] = TaskStatus.IN_PROGRESS.value
        await self._observer.emit(
            "task.updated",
            {
                "task_id": task_id,
                "status": TaskStatus.IN_PROGRESS.value,
                "summary": "前置依赖已完成，计划步骤已激活",
            },
            dedupe_key=f"task:{task_id}:activated",
        )
        return task_id

    async def _reconcile_active_plan_step(
        self,
        invocation: AgentToolInvocation,
        *,
        tool_evidence: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        statuses = dict(invocation.state.get("plan_task_statuses", {}))
        definitions = dict(invocation.state.get("plan_step_definitions", {}))
        active_task_id = str(
            invocation.state.get("active_plan_task_id") or ""
        )
        if (
            not active_task_id
            or statuses.get(active_task_id) != TaskStatus.IN_PROGRESS.value
        ):
            return {}, None
        definition = definitions.get(active_task_id, {})
        capability_hint = str(definition.get("capability_hint") or "")
        if capability_hint and capability_hint != invocation.name:
            return {}, None

        evidence_entry = dict(
            tool_evidence.get(invocation.tool_call_id, {})
        )
        if evidence_entry.get("scientific_goal_status") in {
            "partial",
            "unverified",
        }:
            return {}, None
        bound_task_id = str(evidence_entry.get("plan_task_id") or "")
        if bound_task_id and bound_task_id != active_task_id:
            return {}, None
        evidence_entry["plan_task_id"] = active_task_id
        tool_evidence[invocation.tool_call_id] = evidence_entry
        statuses[active_task_id] = TaskStatus.COMPLETED.value
        plan_evidence = {
            key: list(value)
            for key, value in invocation.state.get(
                "plan_step_evidence",
                {},
            ).items()
        }
        plan_evidence[active_task_id] = [invocation.tool_call_id]
        await self._observer.emit(
            "task.updated",
            {
                "task_id": active_task_id,
                "status": TaskStatus.COMPLETED.value,
                "summary": f"{invocation.name} 已完成，计划步骤已自动对账",
            },
            dedupe_key=(
                f"task:{active_task_id}:completed:"
                f"{invocation.tool_call_id}"
            ),
        )
        next_active_task_id = await self._activate_next_plan_step(
            statuses,
            definitions,
        )
        return (
            {
                "plan_task_statuses": statuses,
                "plan_step_evidence": plan_evidence,
                "active_plan_task_id": next_active_task_id,
                "tool_evidence": tool_evidence,
            },
            active_task_id,
        )

    async def _finish_task(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        finished = _FinishTaskInput.model_validate(invocation.arguments)
        statuses = {
            str(task_id): str(status)
            for task_id, status in invocation.state.get(
                "plan_task_statuses",
                {},
            ).items()
        }
        plan_task_ids = {
            str(task_id)
            for task_id in invocation.state.get("plan_task_ids", [])
        } | set(statuses)
        unfinished = [
            task_id
            for task_id in plan_task_ids
            if statuses.get(task_id)
            not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }
        ]
        if unfinished:
            return _failed_tool_update(
                invocation,
                error_code="plan_incomplete",
                summary="显式计划仍有未完成步骤，当前不能结束任务。",
                retryable=False,
                recovery_hint=(
                    "继续执行计划，或用 update_task_plan 将无法继续的步骤"
                    "明确标记为 failed/cancelled。"
                ),
            )
        non_completed = [
            task_id
            for task_id in plan_task_ids
            if statuses.get(task_id)
            in {
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }
        ]
        if non_completed and not finished.limitations:
            return {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability="finish_task",
                            summary="计划包含失败或取消步骤，最终答复必须声明限制。",
                            error_code="completion_limitations_required",
                            retryable=False,
                            recovery_hint="在 limitations 中说明未完成内容及影响。",
                        ),
                        tool_call_id=invocation.tool_call_id,
                    )
                ]
            }
        known_artifact_ids = {
            artifact_id
            for item in invocation.state.get("tool_evidence", {}).values()
            for artifact_id in item.get("artifact_ids", [])
        }
        unknown_artifact_ids = [
            str(artifact_id)
            for artifact_id in finished.evidence_artifact_ids
            if str(artifact_id) not in known_artifact_ids
        ]
        if unknown_artifact_ids:
            return {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="failed",
                            capability="finish_task",
                            summary="完成依据包含不属于当前 run Tool 结果的 artifact。",
                            error_code="completion_evidence_invalid",
                            retryable=False,
                            recovery_hint="只引用本次 run 已验证 Tool 返回的 artifact_id。",
                        ),
                        tool_call_id=invocation.tool_call_id,
                    )
                ]
            }
        scientific_evidence = scientific_evidence_from_state(
            invocation.state
        )
        scientific_failures = validate_scientific_final_response(
            finished.final_response,
            scientific_evidence,
            declared_artifact_ids=[
                str(item)
                for item in finished.evidence_artifact_ids
            ],
        )
        final_response = (
            deterministic_scientific_fallback(
                scientific_evidence,
                scientific_failures,
            )
            if scientific_evidence
            else finished.final_response
        )
        await self._observer.emit(
            "message.completed",
            {
                "role": "assistant",
                "content": final_response,
                "has_tool_calls": False,
                "turn": int(invocation.state.get("turn_count", 0)),
            },
            dedupe_key=f"message:{self._run_id}:final",
        )
        return {
            "messages": [
                ToolMessage(
                    content=render_tool_outcome(
                        status="completed",
                        capability="finish_task",
                        summary="任务已根据计划与当前 run 证据标记完成。",
                        result={
                            "evidence_artifact_ids": [
                                str(item)
                                for item in finished.evidence_artifact_ids
                            ],
                            "limitations": finished.limitations,
                        },
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "task_status": TaskStatus.COMPLETED.value,
            "outcome_status": "completed",
            "final_response": final_response,
        }

    async def _invoke_domain_tool(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        handler = self._capabilities.registry.get(invocation.name)
        raw_skill_resources = list(
            invocation.state.get("loaded_skill_resources", [])
        )
        (
            valid_skill_resources,
            loaded_skills,
            invalid_skill_resources,
        ) = (
            self._validated_skill_resources(raw_skill_resources)
        )
        missing_skills = [
            skill_name
            for skill_name in handler.spec.required_skills
            if skill_name not in loaded_skills
        ]
        if invalid_skill_resources:
            update = _failed_tool_update(
                invocation,
                error_code="skill_context_stale",
                summary="已加载的 Skill 资源与当前目录版本或内容不一致。",
                retryable=not missing_skills,
                recovery_hint=(
                    (
                        "已清理失效方法上下文；请重新调用当前 Tool。"
                        if not missing_skills
                        else (
                            "先调用 load_skill 加载："
                            + ", ".join(missing_skills)
                            + "；再重新调用当前 Tool。"
                        )
                    )
                ),
            )
            update["loaded_skill_resources"] = valid_skill_resources
            return update
        if missing_skills:
            update = _failed_tool_update(
                invocation,
                error_code="skill_required",
                summary="该能力需要先加载对应领域方法。",
                retryable=False,
                recovery_hint=(
                    "先调用 load_skill 加载："
                    + ", ".join(missing_skills)
                ),
            )
            if valid_skill_resources != raw_skill_resources:
                update["loaded_skill_resources"] = valid_skill_resources
            return update
        try:
            hydrated_arguments = cast(
                dict[str, Any],
                self._hydrate_artifact_handles(invocation.arguments),
            )
        except ArtifactBoundaryError:
            return _failed_tool_update(
                invocation,
                error_code="capability_input_invalid",
                summary="输入 artifact 不属于当前 conversation 或尚未登记。",
                retryable=False,
                recovery_hint="只使用 Tool 结果或当前输入上下文给出的 artifact_id。",
            )
        fingerprint = _tool_call_fingerprint(
            invocation.name,
            invocation.arguments,
        )
        evidence = dict(invocation.state.get("tool_evidence", {}))
        existing_evidence = evidence.get(invocation.tool_call_id)
        if existing_evidence is not None:
            if (
                existing_evidence.get("capability") != invocation.name
                or existing_evidence.get("input_digest") != fingerprint
            ):
                return _failed_tool_update(
                    invocation,
                    error_code="tool_call_id_conflict",
                    summary=(
                        "该 Tool call ID 已绑定到当前 run 中的另一项调用。"
                    ),
                    retryable=False,
                    recovery_hint=(
                        "为新的 Tool 调用生成新的 tool_call_id；"
                        "不要复用已有 evidence handle。"
                    ),
                )
            return {
                "messages": [
                    ToolMessage(
                        content=render_tool_outcome(
                            status="completed",
                            capability=invocation.name,
                            summary=(
                                "该调用已在当前 run 中成功完成；"
                                "本次仅重放既有证据，未重复执行或推进计划。"
                            ),
                            evidence_artifact_ids=list(
                                existing_evidence.get("artifact_ids", [])
                            ),
                            evidence_handle=invocation.tool_call_id,
                            plan_task_id=(
                                str(existing_evidence["plan_task_id"])
                                if existing_evidence.get("plan_task_id")
                                else None
                            ),
                        ),
                        tool_call_id=invocation.tool_call_id,
                    )
                ],
                "task_status": TaskStatus.IN_PROGRESS.value,
                "tool_evidence": evidence,
            }
        failure_counts = dict(
            invocation.state.get("tool_failure_counts", {})
        )
        if int(failure_counts.get(fingerprint, 0)) >= 2:
            return _failed_tool_update(
                invocation,
                error_code="repeated_failure_blocked",
                summary="等价 Tool 调用在当前 run 已失败两次，本次未再次执行。",
                retryable=False,
                recovery_hint="修改输入、补齐前置步骤或选择其他能力。",
                failure_counts=failure_counts,
            )
        decision = self._policy.evaluate(
            handler.spec,
            hydrated_arguments,
        )
        if decision.outcome == ToolPolicyOutcome.DENY:
            return _failed_tool_update(
                invocation,
                error_code="tool_policy_denied",
                summary="Tool policy 拒绝执行该能力。",
                retryable=False,
                recovery_hint=decision.reason,
            )
        if decision.outcome == ToolPolicyOutcome.REQUIRE_REVIEW:
            review = ReviewInterrupt(
                review_id=uuid5(
                    _REVIEW_NAMESPACE,
                    f"{self._run_id}:{invocation.tool_call_id}",
                ),
                tool_call_id=invocation.tool_call_id,
                capability=invocation.name,
                reason=decision.reason,
                arguments=hydrated_arguments,
            )
            raw_resolution = interrupt(review.model_dump(mode="json"))
            resolution = ReviewResolution.model_validate(raw_resolution)
            if resolution.review_id != review.review_id:
                raise ValueError("review decision 与当前 interrupt 不匹配")
            if resolution.decision == ReviewDecision.REJECT:
                return _failed_tool_update(
                    invocation,
                    error_code="review_rejected",
                    summary="人工审核拒绝了该 Tool 调用。",
                    retryable=False,
                    recovery_hint="尊重审核决定并选择无需该操作的路径。",
                )

        try:
            result = await self._executor.invoke(
                invocation.name,
                hydrated_arguments,
                tool_call_id=invocation.tool_call_id,
            )
            result_payload = result.model_dump(mode="json")
            result_status = result_payload.get("status")
            if (
                result_status is not None
                and result_status != CapabilityStatus.COMPLETED.value
            ):
                failure_counts[fingerprint] = int(
                    failure_counts.get(fingerprint, 0)
                ) + 1
                return _failed_tool_update(
                    invocation,
                    error_code="capability_not_completed",
                    summary=PUBLIC_CAPABILITY_NOT_COMPLETED_SUMMARY,
                    retryable=False,
                    recovery_hint=(
                        "检查诊断与输入前置条件，改用其他能力或向用户说明限制。"
                    ),
                    failure_counts=failure_counts,
                )
            evidence_artifacts = _collect_artifact_ids(result_payload)
        except RuntimeCleanupError:
            raise
        except CapabilityInputError as exc:
            logger.info(
                "domain Tool input rejected",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": str(self._run_id),
                    "capability": invocation.name,
                },
            )
            failure_counts[fingerprint] = int(
                failure_counts.get(fingerprint, 0)
            ) + 1
            return _failed_tool_update(
                invocation,
                error_code="capability_input_invalid",
                summary="输入不满足该能力的类型或科学前置条件。",
                retryable=False,
                recovery_hint=(
                    "检查输入 artifact 类型和处理状态，并按 Tool 前置条件补齐步骤。"
                ),
                failure_counts=failure_counts,
            )
        except CapabilityExecutionError as exc:
            logger.error(
                "domain Tool execution exhausted retries",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": str(self._run_id),
                    "capability": invocation.name,
                },
            )
            failure_counts[fingerprint] = int(
                failure_counts.get(fingerprint, 0)
            ) + 1
            return _failed_tool_update(
                invocation,
                error_code="capability_execution_failed",
                summary=PUBLIC_CAPABILITY_FAILURE_SUMMARY,
                retryable=False,
                recovery_hint="更换输入或方法；相同参数的自动重试已经耗尽。",
                failure_counts=failure_counts,
            )
        except Exception as exc:
            logger.error(
                "domain Tool invocation failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": str(self._run_id),
                    "capability": invocation.name,
                },
            )
            failure_counts[fingerprint] = int(
                failure_counts.get(fingerprint, 0)
            ) + 1
            return _failed_tool_update(
                invocation,
                error_code="capability_internal_error",
                summary=PUBLIC_CAPABILITY_FAILURE_SUMMARY,
                retryable=False,
                recovery_hint="不要重复相同调用；选择其他能力或向用户说明限制。",
                failure_counts=failure_counts,
            )
        failure_counts.pop(fingerprint, None)
        scientific_evidence = project_scientific_evidence(
            invocation.name,
            result_payload,
            evidence_artifacts,
        )
        result_manifest = result_payload.get("result_manifest")
        scientific_goal_status = (
            str(result_manifest.get("scientific_goal_status") or "")
            if isinstance(result_manifest, dict)
            else ""
        )
        evidence[invocation.tool_call_id] = {
            "capability": invocation.name,
            "input_digest": fingerprint,
            "result_status": CapabilityStatus.COMPLETED.value,
            "artifact_ids": evidence_artifacts,
            "result_digest": hashlib.sha256(
                json.dumps(
                    result_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "plan_task_id": None,
            **(
                {"scientific_evidence": scientific_evidence}
                if scientific_evidence is not None
                else {}
            ),
            **(
                {"scientific_goal_status": scientific_goal_status}
                if scientific_goal_status
                else {}
            ),
        }
        plan_updates, reconciled_task_id = (
            await self._reconcile_active_plan_step(
                invocation,
                tool_evidence=evidence,
            )
        )
        content = render_tool_outcome(
            status="completed",
            capability=invocation.name,
            summary=(
                (
                    (
                        "能力执行完成，但探索性科学目标尚未完全验证；"
                        if scientific_goal_status in {"partial", "unverified"}
                        else "能力执行完成，科学后置条件已验证；"
                    )
                    if scientific_evidence is not None
                    else "能力执行完成，结果已经过类型契约校验；"
                )
                + "当前活动计划步骤已自动完成。"
                if reconciled_task_id
                else (
                    (
                        "能力执行完成，但探索性科学目标尚未完全验证。"
                        if scientific_goal_status in {"partial", "unverified"}
                        else "能力执行完成，科学后置条件已验证。"
                    )
                    if scientific_evidence is not None
                    else "能力执行完成，结果已经过类型契约校验。"
                )
            ),
            result=_model_visible_result(result_payload),
            evidence_artifact_ids=evidence_artifacts,
            evidence_handle=invocation.tool_call_id,
            plan_task_id=reconciled_task_id,
        )
        update = {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "task_status": TaskStatus.IN_PROGRESS.value,
            "tool_failure_counts": failure_counts,
            "tool_evidence": evidence,
        }
        update.update(plan_updates)
        return update


def _build_system_prompt(
    capabilities: DomainCapabilityLayer,
    tools: AgentToolRegistry,
) -> str:
    skill_inventory = capabilities.skills.summaries() or "- (none)"
    tool_inventory = tools.prompt_inventory() or "- (none)"
    return (
        "你是 OmniCell 的顶层科研分析 Agent。你的职责是理解用户目标，"
        "选择最小充分路径，使用注册的 Skill 与 Tool 完成任务。"
        "不能因为 conversation 中存在数据就默认运行任何领域工作流。"
        "每轮只能调用一个 Tool。\n\n"
        "【动态路由】\n"
        "1. 稳定、低风险且不依赖领域方法边界的知识，已有上下文足以可靠回答"
        "时，直接返回非空最终文本，不调用 Tool。\n"
        "2. 只需读取或校验局部事实时，调用检查 Tool。\n"
        "3. 只需完成一个明确科研操作时，直接调用科学语义匹配的 Tool。\n"
        "4. 问题命中可用 Skill 摘要中的领域术语或方法，且答案依赖其操作定义、"
        "适用条件、统计假设、证据边界、组合规则或专业验证标准时，先用 "
        "load_skill 加载匹配 Skill；答复篇幅不是是否加载的判断依据。"
        "若用户只要求解释，加载后直接基于 Skill 方法上下文回答，不得因此"
        "读取数据或执行领域 Tool；只有目标确实要求操作数据时，才使用被激活的"
        "复合 Tool 或受指引的 Tool 组合。\n"
        "5. 只有目标包含至少两个相互依赖、可分别验证的步骤时才创建显式计划。"
        "简单问答和单能力任务禁止形式化建计划。\n\n"
        "【完成规则】\n"
        "吸收每次 Tool 的结构化结果后再决定下一步。没有未完成显式计划时，"
        "用非空最终文本直接结束；finish_task 仅用于需要显式声明 evidence artifact "
        "或 limitations 的结构化完成。"
        "显式计划中的步骤必须绑定实际 Tool 结果或 artifact 证据，"
        "领域 Tool 成功后系统会自动完成当前活动步骤并激活下一步；"
        "不要再次手工完成已自动对账的步骤。未完成步骤必须先完成或明确标记 "
        "failed/cancelled。"
        "Tool 失败时根据 error_code、retryable 和 recovery_hint 改变路径；"
        "不要重复等价的失败调用。\n\n"
        "【Artifact 句柄契约】\n"
        "Agent-facing Tool 只接收当前 conversation 中已登记的 artifact_id；"
        "调用下游 Tool 时逐字复制最新结果给出的 artifact_id，backend 会还原并验证"
        "完整权威引用。不得猜测 identity 或自行构造路径。最终回复只引用 "
        "artifact_id 或页面中的已登记产物，不得输出 workspace:// URI、宿主路径"
        "或 invocation 内部定位符。\n\n"
        "【可用 Skill 摘要】\n"
        f"{skill_inventory}\n\n"
        "【可用 Tool 与调用提示】\n"
        f"{tool_inventory}\n\n"
        f"{render_response_contract()}"
    )


def _render_input_artifacts(
    artifacts: tuple[ArtifactRef, ...],
) -> str:
    if not artifacts:
        return ""
    descriptors = [
        {
            "artifact_id": str(artifact.artifact_id),
            "kind": artifact.kind,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "metadata": artifact.metadata,
        }
        for artifact in artifacts
    ]
    encoded = json.dumps(
        descriptors,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ValueError("Agent input artifact 描述超过 256 KiB")
    return (
        "以下是本次 run 已通过 ownership 校验的输入 artifact 句柄与有界描述。"
        "Tool 参数只传对应 artifact_id；未列出的 artifact 不得作为输入：\n"
        f"{encoded}"
    )


class AgentLoopFactory:
    def __init__(
        self,
        capabilities: DomainCapabilityLayer,
        *,
        llm_factory: LLMFactory | None = None,
        model_factory: Callable[[], Any] | None = None,
        policy: ToolPolicy | None = None,
        capability_invoker_factory: CapabilityInvokerFactory | None = None,
        config: AgentLoopConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (llm_factory is None) == (model_factory is None):
            raise ValueError("llm_factory 与 model_factory 必须且只能提供一个")
        self._capabilities = capabilities
        self._llm_factory = llm_factory
        self._model_factory = model_factory
        self._policy = policy or DefaultToolPolicy()
        self._capability_invoker_factory = (
            capability_invoker_factory or SubprocessCapabilityInvoker
        )
        self._config = config or AgentLoopConfig()
        self._clock = clock

    @property
    def config(self) -> AgentLoopConfig:
        return self._config

    def create(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        capability_context: CapabilityContext,
        checkpointer: Any,
        input_artifacts: tuple[ArtifactRef, ...] = (),
        memory_resolver: MemoryContextResolver | None = None,
        memory_tools: AgentMemoryControlPort | None = None,
        cancellation: CancellationToken | None = None,
        observer: AgentObserver | None = None,
    ) -> AgentExecution:
        if capability_context.conversation_id != conversation_id:
            raise ValueError("Agent conversation 与 capability context 不一致")
        for artifact in input_artifacts:
            if artifact.conversation_id != conversation_id:
                raise ValueError("Agent input artifact 不属于当前 conversation")
        model = (
            self._llm_factory.create(LLMRole.AGENT_PRIMARY)
            if self._llm_factory is not None
            else cast(Callable[[], Any], self._model_factory)()
        )
        active_cancellation = cancellation or CancellationToken()
        active_observer = observer or NullAgentObserver()
        capability_invoker = self._capability_invoker_factory(
            self._capabilities.registry,
            capability_context,
        )
        executor = AsyncCapabilityExecutor(
            capability_invoker,
            active_cancellation,
            active_observer,
            max_retries=self._config.max_tool_retries,
        )
        tools = _OmniCellToolComposition(
            run_id=run_id,
            capabilities=self._capabilities,
            capability_context=capability_context,
            executor=executor,
            observer=active_observer,
            policy=self._policy,
            memory_tools=memory_tools,
        ).build()
        artifact_context = _render_input_artifacts(input_artifacts)
        context_messages = (
            (SystemMessage(content=artifact_context),)
            if artifact_context
            else ()
        )
        hooks: list[AgentHook] = [
            MalformedToolHistoryHook(),
            *(
                [MemoryContextHook(memory_resolver)]
                if memory_resolver is not None
                else []
            ),
            SkillMethodContextHook(self._capabilities.skills),
            ScientificEvidenceCompletionHook(),
            PlanBackpressureHook(),
        ]
        return AgentExecution(
            run_id=run_id,
            conversation_id=conversation_id,
            model=model,
            tools=tools,
            system_prompt=_build_system_prompt(
                self._capabilities,
                tools,
            ),
            context_messages=context_messages,
            checkpointer=checkpointer,
            cancellation=active_cancellation,
            observer=active_observer,
            config=self._config,
            hooks=tuple(hooks),
            clock=self._clock,
            fatal_tool_errors=(RuntimeCleanupError,),
        )


__all__ = ["AgentLoopFactory"]
