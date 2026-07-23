"""OmniCell composition root for the domain-neutral Agent Loop."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer
from omnicell_agent.capabilities.catalog import SkillCatalogError
from omnicell_agent.capabilities.contracts import ArtifactRef
from omnicell_agent.capabilities.errors import PUBLIC_CAPABILITY_FAILURE_SUMMARY
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
from .loop import (
    AgentExecution,
    AgentLoopConfig,
    ReviewInterrupt,
    ReviewResolution,
)
from .observer import AgentObserver, NullAgentObserver
from .policy import DefaultToolPolicy, ToolPolicy, ToolPolicyOutcome
from .tooling import (
    AgentToolDefinition,
    AgentToolInvocation,
    AgentToolRegistry,
)


logger = logging.getLogger(__name__)

_REVIEW_NAMESPACE = UUID("510b1e62-91cb-49ab-887e-b06bdc7f148e")
_PLAN_NAMESPACE = UUID("f5f693f6-f36b-4b75-9864-eb5ba865c10e")


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
    ] = "workflow_guidance"


class _FinishTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_response: str = Field(min_length=1, max_length=20_000)


class _PlanStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2_000)
    capability_name: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class _CreateTaskPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1, max_length=2_000)
    steps: list[_PlanStepInput] = Field(min_length=2, max_length=12)


class _UpdateTaskPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: Literal["in_progress", "completed", "failed", "cancelled"]
    summary: str | None = Field(default=None, max_length=2_000)


class _OmniCellToolComposition:
    def __init__(
        self,
        *,
        run_id: UUID,
        capabilities: DomainCapabilityLayer,
        executor: AsyncCapabilityExecutor,
        observer: AgentObserver,
        policy: ToolPolicy,
    ) -> None:
        self._run_id = run_id
        self._capabilities = capabilities
        self._executor = executor
        self._observer = observer
        self._policy = policy

    def build(self) -> AgentToolRegistry:
        registry = AgentToolRegistry()
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
            ),
            self._load_skill,
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
            ),
            self._create_task_plan,
        )
        registry.register(
            AgentToolDefinition(
                name="update_task_plan",
                description="更新当前显式计划中一个步骤的权威状态。",
                prompt_hint=(
                    "执行步骤前标记 in_progress，吸收结果后标记 "
                    "completed/failed/cancelled；无显式计划时不要调用。"
                ),
                input_model=_UpdateTaskPlanInput,
            ),
            self._update_task_plan,
        )
        registry.register(
            AgentToolDefinition(
                name="finish_task",
                description="结束当前目标并返回面向用户的最终答复。",
                prompt_hint=(
                    "已有上下文足以直接回答，或所有执行步骤已经收敛时调用；"
                    "不要为了直接回答而先调用领域 Tool。"
                ),
                input_model=_FinishTaskInput,
            ),
            self._finish_task,
        )
        for spec in self._capabilities.registry.specs:
            handler = self._capabilities.registry.get(spec.name)
            registry.register(
                AgentToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    prompt_hint=spec.prompt_hint,
                    input_model=handler.request_model,
                ),
                self._invoke_domain_tool,
            )
        return registry

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
        resource = (
            f"{request.skill_name}:reference:{request.reference}"
            if request.reference
            else (
                f"{request.skill_name}:example:{request.example}"
                if request.example
                else f"{request.skill_name}:body"
            )
        )
        activity = {
            "tool_call_id": invocation.tool_call_id,
            "skill_name": request.skill_name,
            "resource_kind": resource_kind,
            "resource_name": resource_name,
            "purpose": request.purpose,
        }
        await self._observer.emit(
            "skill.load_started",
            activity,
            dedupe_key=f"skill:{invocation.tool_call_id}:started",
        )
        loaded = list(invocation.state.get("loaded_skill_resources", []))
        if resource in loaded:
            content = "该 Skill 资源已经加载；请复用对话中的既有内容。"
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
            try:
                content = self._capabilities.skills.load(
                    request.skill_name,
                    reference=request.reference,
                    example=request.example,
                )
            except SkillCatalogError as exc:
                logger.info(
                    "skill resource load failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={
                        "run_id": str(self._run_id),
                        "skill": request.skill_name,
                        "resource_kind": resource_kind,
                    },
                )
                content = "Skill 加载失败：资源不存在或不符合加载契约。"
                await self._observer.emit(
                    "skill.load_failed",
                    activity,
                    dedupe_key=f"skill:{invocation.tool_call_id}:failed",
                )
            else:
                loaded.append(resource)
                await self._observer.emit(
                    "skill.load_completed",
                    {
                        **activity,
                        "outcome": "loaded",
                        "content_bytes": len(content.encode("utf-8")),
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

        revision = int(invocation.state.get("plan_revision", 0)) + 1
        task_ids: list[str] = []
        task_statuses: dict[str, str] = {}
        rendered_steps: list[dict[str, Any]] = []
        for index, step in enumerate(plan.steps, start=1):
            task_id = uuid5(
                _PLAN_NAMESPACE,
                f"{self._run_id}:plan:{revision}:step:{index}",
            )
            task_id_text = str(task_id)
            task_ids.append(task_id_text)
            task_statuses[task_id_text] = TaskStatus.PENDING.value
            await self._observer.emit(
                "task.created",
                {
                    "task_id": task_id_text,
                    "tool_call_id": f"agent-plan:{revision}:{index}",
                    "title": step.title,
                    "description": step.description,
                    "capability_name": step.capability_name,
                },
                dedupe_key=f"task:{task_id_text}:created",
            )
            rendered_steps.append(
                {
                    "task_id": task_id_text,
                    "title": step.title,
                    "capability_name": step.capability_name,
                    "status": TaskStatus.PENDING.value,
                }
            )
        return {
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "plan_revision": revision,
                            "rationale": plan.rationale,
                            "steps": rendered_steps,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "plan_revision": revision,
            "plan_task_ids": task_ids,
            "plan_task_statuses": task_statuses,
            "task_status": TaskStatus.IN_PROGRESS.value,
        }

    async def _update_task_plan(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        update = _UpdateTaskPlanInput.model_validate(invocation.arguments)
        task_id_text = str(update.task_id)
        statuses = dict(invocation.state.get("plan_task_statuses", {}))
        if task_id_text not in statuses:
            return {
                "messages": [
                    ToolMessage(
                        content="计划步骤不存在或已经被新计划替换。",
                        tool_call_id=invocation.tool_call_id,
                    )
                ]
            }
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
        return {
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "task_id": task_id_text,
                            "status": update.status,
                            "summary": update.summary,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "plan_task_statuses": statuses,
            "task_status": TaskStatus.IN_PROGRESS.value,
        }

    async def _finish_task(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        unfinished = [
            task_id
            for task_id, status in invocation.state.get(
                "plan_task_statuses",
                {},
            ).items()
            if status
            in {
                TaskStatus.PENDING.value,
                TaskStatus.IN_PROGRESS.value,
            }
        ]
        if unfinished:
            return {
                "messages": [
                    ToolMessage(
                        content=(
                            "显式计划仍有未完成步骤。请继续执行，或先用 "
                            "update_task_plan 将无法继续的步骤明确标记为 "
                            "failed/cancelled。"
                        ),
                        tool_call_id=invocation.tool_call_id,
                    )
                ]
            }
        finished = _FinishTaskInput.model_validate(invocation.arguments)
        await self._observer.emit(
            "message.completed",
            {
                "role": "assistant",
                "content": finished.final_response,
                "has_tool_calls": False,
                "turn": int(invocation.state.get("turn_count", 0)),
            },
            dedupe_key=f"message:{self._run_id}:final",
        )
        await self._observer.emit(
            "task.updated",
            {"status": TaskStatus.COMPLETED.value},
            dedupe_key=f"task:{self._run_id}:completed",
        )
        return {
            "messages": [
                ToolMessage(
                    content="任务已标记完成。",
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "task_status": TaskStatus.COMPLETED.value,
            "outcome_status": "completed",
            "final_response": finished.final_response,
        }

    async def _invoke_domain_tool(
        self,
        invocation: AgentToolInvocation,
    ) -> dict[str, Any]:
        handler = self._capabilities.registry.get(invocation.name)
        decision = self._policy.evaluate(
            handler.spec,
            invocation.arguments,
        )
        if decision.outcome == ToolPolicyOutcome.DENY:
            return {
                "messages": [
                    ToolMessage(
                        content=f"Tool policy 拒绝执行：{decision.reason}",
                        tool_call_id=invocation.tool_call_id,
                    )
                ]
            }
        if decision.outcome == ToolPolicyOutcome.REQUIRE_REVIEW:
            review = ReviewInterrupt(
                review_id=uuid5(
                    _REVIEW_NAMESPACE,
                    f"{self._run_id}:{invocation.tool_call_id}",
                ),
                tool_call_id=invocation.tool_call_id,
                capability=invocation.name,
                reason=decision.reason,
                arguments=invocation.arguments,
            )
            raw_resolution = interrupt(review.model_dump(mode="json"))
            resolution = ReviewResolution.model_validate(raw_resolution)
            if resolution.review_id != review.review_id:
                raise ValueError("review decision 与当前 interrupt 不匹配")
            if resolution.decision == ReviewDecision.REJECT:
                return {
                    "messages": [
                        ToolMessage(
                            content="人工审核拒绝了该 Tool 调用。",
                            tool_call_id=invocation.tool_call_id,
                        )
                    ]
                }

        try:
            result = await self._executor.invoke(
                invocation.name,
                invocation.arguments,
                tool_call_id=invocation.tool_call_id,
            )
            content = result.model_dump_json()
        except RuntimeCleanupError:
            raise
        except Exception as exc:
            logger.error(
                "domain Tool invocation failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "run_id": str(self._run_id),
                    "capability": invocation.name,
                },
            )
            content = (
                f"Tool 执行失败：{PUBLIC_CAPABILITY_FAILURE_SUMMARY}"
            )
        return {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=invocation.tool_call_id,
                )
            ],
            "task_status": TaskStatus.IN_PROGRESS.value,
        }


def _build_system_prompt(
    capabilities: DomainCapabilityLayer,
    tools: AgentToolRegistry,
) -> str:
    skill_inventory = capabilities.skills.summaries() or "- (none)"
    tool_inventory = tools.prompt_inventory() or "- (none)"
    return (
        "你是 OmniCell 的顶层生物分析 Agent。你的职责是理解用户目标，"
        "选择最小充分路径，使用注册的 Skill 与 Tool 完成任务。"
        "不能因为 conversation 中存在数据就默认运行任何领域工作流。"
        "每轮只能调用一个 Tool。\n\n"
        "【动态路由】\n"
        "1. 已有上下文足以可靠回答时，直接调用 finish_task。\n"
        "2. 只需读取或校验局部事实时，调用只读 Tool。\n"
        "3. 只需完成一个明确科研操作时，直接调用对应原子 Tool，不运行完整工作流。\n"
        "4. 需要完整领域结果、方法选择或专业验证规则时，先用 load_skill "
        "加载匹配 Skill，再选择完整 workflow Tool 或受指引的 Tool 组合。\n"
        "5. 只有目标包含至少两个相互依赖、可分别验证的步骤时才创建显式计划。"
        "简单问答和单能力任务禁止形式化建计划。\n\n"
        "【完成规则】\n"
        "吸收每次 Tool 的结构化结果后再决定下一步。目标完成时调用 finish_task；"
        "显式计划中的未完成步骤必须先完成或明确标记 failed/cancelled。\n\n"
        "【ArtifactRef 权威契约】\n"
        "artifact_id、conversation_id、kind、uri、media_type、size_bytes、sha256、"
        "metadata 八个字段共同构成不可改写的权威引用。Tool 参数必须逐字段原样复制，"
        "包括 null 与空对象；不得省略、猜测、改写或自行构造路径。\n\n"
        "【可用 Skill 摘要】\n"
        f"{skill_inventory}\n\n"
        "【可用 Tool 与调用提示】\n"
        f"{tool_inventory}"
    )


def _render_input_artifacts(
    artifacts: tuple[ArtifactRef, ...],
) -> str:
    if not artifacts:
        return ""
    descriptors = [
        artifact.model_dump(mode="json") for artifact in artifacts
    ]
    encoded = json.dumps(
        descriptors,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ValueError("Agent input artifact 描述超过 256 KiB")
    return (
        "以下是本次 run 已通过 ownership 校验的输入 artifact 权威描述。"
        "每个引用的八个字段必须在 Tool 参数中显式出现并逐字段原样复用；"
        "未列出的 artifact 不得作为输入：\n"
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
            executor=executor,
            observer=active_observer,
            policy=self._policy,
        ).build()
        artifact_context = _render_input_artifacts(input_artifacts)
        context_messages = (
            (SystemMessage(content=artifact_context),)
            if artifact_context
            else ()
        )
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
            clock=self._clock,
            fatal_tool_errors=(RuntimeCleanupError,),
        )


__all__ = ["AgentLoopFactory"]
