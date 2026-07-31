"""为 Playwright live E2E 启动受控 FastAPI + PostgreSQL 后端。

该启动器只用于测试：模型与 capability 都是确定性替身，但 API、
RunCoordinator、Agent Loop、PostgreSQL 事件日志、LangGraph checkpointer 和
artifact 边界均使用真实实现。默认在退出时删除独立 schema；显式 inspect
模式会保留 schema 与 workspace，供人工查看刚刚完成的真实产品记录。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SOURCE = REPOSITORY_ROOT / "backend" / "src"
if str(BACKEND_SOURCE) not in sys.path:
    sys.path.insert(0, str(BACKEND_SOURCE))

import psycopg  # noqa: E402
import uvicorn  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from psycopg import sql  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from omnicell_agent.agent import (  # noqa: E402
    AgentLoopConfig,
    AgentLoopFactory,
    CooperativeInProcessCapabilityInvoker,
    DefaultToolPolicy,
)
from omnicell_agent.api.app import create_app  # noqa: E402
from omnicell_agent.api.service import ApiService  # noqa: E402
from omnicell_agent.capabilities.bootstrap import DomainCapabilityLayer  # noqa: E402
from omnicell_agent.capabilities.atomic import build_atomic_capabilities  # noqa: E402
from omnicell_agent.capabilities.catalog import SkillCatalog, SkillDefinition  # noqa: E402
from omnicell_agent.capabilities.contracts import (  # noqa: E402
    ArtifactRef,
    CapabilityEffect,
    CapabilityMode,
    CapabilityRequest,
    CapabilitySpec,
)
from omnicell_agent.capabilities.registry import (  # noqa: E402
    CapabilityContext,
    CapabilityRegistry,
)
from omnicell_agent.memory import MemoryService, PostgresMemoryRuntime  # noqa: E402
from omnicell_agent.persistence.bootstrap import PersistenceRuntime  # noqa: E402
from omnicell_agent.persistence.config import PostgresSettings  # noqa: E402
from omnicell_agent.runs.coordinator import RunCoordinator  # noqa: E402


class GenerateReportRequest(CapabilityRequest):
    dataset: ArtifactRef


class GenerateReportResult(BaseModel):
    report: ArtifactRef


class GenerateReportCapability:
    spec = CapabilitySpec(
        name="generate_live_report",
        mode=CapabilityMode.ATOMIC,
        effect=CapabilityEffect.CUSTOM,
        description="生成用于真实产品闭环测试的确定性分析报告。",
        prompt_hint="仅在真实闭环测试要求生成报告时调用。",
    )
    request_model = GenerateReportRequest
    result_model = GenerateReportResult

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> GenerateReportResult:
        normalized = GenerateReportRequest.model_validate(request)
        context.artifacts.resolve(normalized.dataset, expected_kind="dataset")
        report = context.artifacts.write_text(
            "live-analysis-report.csv",
            "cluster,label\n0,T cell\n1,B cell\n",
            kind="report",
            media_type="text/csv",
            metadata={"filename": "live-analysis-report.csv"},
        )
        return GenerateReportResult(report=report)


def _input_dataset(messages: list[object]) -> dict[str, object]:
    for message in messages:
        if not isinstance(message, SystemMessage):
            continue
        content = str(message.content)
        if "本次 run 已通过 ownership 校验的输入 artifact 句柄" not in content:
            continue
        descriptors = json.loads(content.split("：\n", 1)[1])
        if not isinstance(descriptors, list) or len(descriptors) != 1:
            raise ValueError("live E2E 必须且只能提交一个 dataset artifact")
        descriptor = descriptors[0]
        if not isinstance(descriptor, dict) or descriptor.get("kind") != "dataset":
            raise ValueError("live E2E 输入必须是 dataset artifact")
        return descriptor
    raise ValueError("live E2E Agent 未收到权威输入 artifact 描述")


def _has_live_greeting_memory(messages: list[object]) -> bool:
    """Inspect only the transient Memory Hook view used by the controlled model."""

    policy_present = any(
        isinstance(message, SystemMessage)
        and message.name == "cross_conversation_memory_policy"
        for message in messages
    )
    data_present = any(
        isinstance(message, HumanMessage)
        and message.name == "cross_conversation_memory_data"
        and "小木" in str(message.content)
        for message in messages
    )
    if data_present and not policy_present:
        raise ValueError("Memory data 缺少独立的不可信数据策略")
    return data_present


def _live_greeting_memory_identity(
    messages: list[object],
) -> tuple[str, str] | None:
    for message in messages:
        if (
            not isinstance(message, HumanMessage)
            or message.name != "cross_conversation_memory_data"
        ):
            continue
        try:
            payload = json.loads(str(message.content))
        except json.JSONDecodeError as exc:
            raise ValueError("live E2E Memory data 非法") from exc
        if not isinstance(payload, list):
            raise ValueError("live E2E Memory data 必须是列表")
        for item in payload:
            if (
                isinstance(item, dict)
                and "小木" in str(item.get("content", ""))
                and isinstance(item.get("item_id"), str)
                and isinstance(item.get("version_id"), str)
            ):
                return str(item["item_id"]), str(item["version_id"])
    return None


def _memory_source_message_ids(messages: list[object]) -> list[str]:
    for message in reversed(messages):
        if (
            isinstance(message, SystemMessage)
            and message.name == "memory_source_identities"
        ):
            try:
                payload = json.loads(str(message.content).rsplit("\n", 1)[1])
            except (IndexError, json.JSONDecodeError) as exc:
                raise ValueError("live E2E memory source identity 非法") from exc
            if not isinstance(payload, list) or not all(
                isinstance(item, str) for item in payload
            ):
                raise ValueError("live E2E memory source identity 必须是字符串列表")
            return payload
    return []


@dataclass(frozen=True, slots=True)
class MemoryBehaviorCase:
    """One semantic expectation used by the deterministic product harness."""

    case_id: str
    goal: str
    route: Literal["direct", "propose", "forget", "recall"]
    response: str
    proposal_kind: (
        Literal["response_preference", "profile_fact", "project_context"] | None
    ) = None


MEMORY_BEHAVIOR_CASES = (
    MemoryBehaviorCase(
        case_id="one_off_instruction",
        goal="这次只用一句话说明当前状态。",
        route="direct",
        response="这是一条仅适用于当前回答的临时要求。",
    ),
    MemoryBehaviorCase(
        case_id="mixed_durable_and_current_task",
        goal="以后和我打招呼时称我为“小木”，现在只用一句话说明当前状态。",
        route="direct",
        response="已按当前要求简要回应，但混合消息不会被提议为长期记忆。",
    ),
    MemoryBehaviorCase(
        case_id="current_scientific_observation",
        goal="当前数据集的这个聚类看起来像 T 细胞。",
        route="direct",
        response="这是当前数据相关的观察，不会被提议为跨会话记忆。",
    ),
    MemoryBehaviorCase(
        case_id="small_talk",
        goal="今天阳光很好，随便聊聊吧。",
        route="direct",
        response="当然可以，普通闲聊不会被提议为长期记忆。",
    ),
    MemoryBehaviorCase(
        case_id="durable_preference_without_keyword",
        goal="以后和我打招呼时都称我为“小木”。",
        route="propose",
        response="好的，这条称呼偏好正在等待你确认。",
        proposal_kind="response_preference",
    ),
    MemoryBehaviorCase(
        case_id="stable_profile_fact",
        goal="我长期在 macOS 上使用 OrbStack 作为本机 Docker 环境。",
        route="propose",
        response="这条稳定环境信息正在等待你确认。",
        proposal_kind="profile_fact",
    ),
    MemoryBehaviorCase(
        case_id="stable_project_context",
        goal="OmniCell-Agent 是我的研究生毕业设计项目。",
        route="propose",
        response="这条项目背景正在等待你确认。",
        proposal_kind="project_context",
    ),
    MemoryBehaviorCase(
        case_id="two_independent_durable_facts",
        goal="我长期使用 macOS；OmniCell-Agent 是我的毕业设计项目。",
        route="direct",
        response="这条消息包含多个独立事实，不会被自动提议为单条长期记忆。",
    ),
    MemoryBehaviorCase(
        case_id="quoted_memory_like_text",
        goal="请把“以后回答先给结论”翻译成英文。",
        route="direct",
        response="Please lead with the conclusion in future answers.",
    ),
    MemoryBehaviorCase(
        case_id="temporary_preference",
        goal="这周回答都先给结论。",
        route="direct",
        response="好的，本次按当前要求继续；这类有时效的要求不会被提议为长期记忆。",
    ),
    MemoryBehaviorCase(
        case_id="semantic_forget_without_keyword",
        goal="以后不要再使用这个称呼偏好了。",
        route="forget",
        response="已发起停止使用该称呼偏好的确认请求。",
    ),
    MemoryBehaviorCase(
        case_id="cross_conversation_recall",
        goal="请按跨会话称呼偏好向我问好。",
        route="recall",
        response="",
    ),
)

_MEMORY_BEHAVIOR_BY_GOAL = {
    case.goal: case for case in MEMORY_BEHAVIOR_CASES
}
if len(_MEMORY_BEHAVIOR_BY_GOAL) != len(MEMORY_BEHAVIOR_CASES):
    raise ValueError("live E2E memory behavior goal 必须唯一")


class ControlledLiveModel:
    """确定性 Agent model；不创建供应商客户端，也不访问网络。"""

    def bind_tools(self, tools: list[dict[str, object]]) -> "ControlledLiveModel":
        names = {
            str(tool["function"]["name"])
            for tool in tools
            if isinstance(tool.get("function"), dict)
        }
        base_names = {
            "normalize_expression",
            "create_task_plan",
            "finish_task",
            "generate_live_report",
            "load_skill",
            "update_task_plan",
        }
        memory_names = {
            "search_memory",
            "propose_memory",
            "forget_memory",
        }
        if frozenset(names) not in {
            frozenset(base_names),
            frozenset(base_names | memory_names),
        }:
            raise ValueError(f"live E2E tool surface 非预期：{sorted(names)}")
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        goal = next(
            (
                str(message.content)
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
                and message.name != "cross_conversation_memory_data"
            ),
            "",
        )
        if "受控阻塞" in goal:
            await asyncio.Event().wait()
        if "marker gene" in goal:
            return AIMessage(
                content=(
                    "聚类只把表达模式相近的细胞分成群，并不会自动说明每个群的"
                    "生物学身份。marker gene 可以揭示各群的特征表达，帮助进行"
                    "细胞类型注释并检查聚类是否具有生物学意义。"
                )
            )
        if "归一化和 log1p" in goal:
            normalization_call_id = "live-e2e-normalize-expression"
            normalized = any(
                isinstance(message, ToolMessage)
                and message.tool_call_id == normalization_call_id
                for message in messages
            )
            if not normalized:
                dataset = _input_dataset(messages)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "normalize_expression",
                            "args": {
                                "dataset": {
                                    "artifact_id": dataset["artifact_id"],
                                }
                            },
                            "id": normalization_call_id,
                            "type": "tool_call",
                        }
                    ],
                )
            scientific_reprompt = any(
                isinstance(message, SystemMessage)
                and "当前候选回复与当前 Run 已验证科研证据冲突" in str(
                    message.content
                )
                for message in messages
            )
            if scientific_reprompt:
                return AIMessage(
                    content=(
                        "本次执行了归一化和 log1p，并生成了新的版本化 "
                        "dataset 产物。"
                    )
                )
            return AIMessage(
                content="本次复用了归一化和 log1p，没有重新执行。"
            )
        memory_case = _MEMORY_BEHAVIOR_BY_GOAL.get(goal)
        if memory_case is not None and memory_case.route == "direct":
            return AIMessage(content=memory_case.response)
        if memory_case is not None and memory_case.route == "propose":
            source_message_ids = _memory_source_message_ids(messages)
            if not source_message_ids:
                raise ValueError("live E2E Agent 未收到可提议的 message identity")
            tool_call_id = (
                f"live-e2e-propose-memory-{source_message_ids[-1]}"
            )
            if any(
                isinstance(message, ToolMessage)
                and message.tool_call_id == tool_call_id
                for message in messages
            ):
                return AIMessage(content=memory_case.response)
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "propose_memory",
                        "args": {
                            "kind": memory_case.proposal_kind,
                            "source_message_id": source_message_ids[-1],
                        },
                        "id": tool_call_id,
                        "type": "tool_call",
                    }
                ],
            )
        if memory_case is not None and memory_case.route == "forget":
            identity = _live_greeting_memory_identity(messages)
            if identity is None:
                raise ValueError("live E2E Agent 未收到待撤销的记忆 identity")
            item_id, version_id = identity
            tool_call_id = f"live-e2e-forget-memory-{version_id}"
            if any(
                isinstance(message, ToolMessage)
                and message.tool_call_id == tool_call_id
                for message in messages
            ):
                return AIMessage(content=memory_case.response)
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "forget_memory",
                        "args": {
                            "item_id": item_id,
                            "version_id": version_id,
                        },
                        "id": tool_call_id,
                        "type": "tool_call",
                    }
                ],
            )
        if memory_case is not None and memory_case.route == "recall":
            if _has_live_greeting_memory(messages):
                return AIMessage(content="你好，小木。很高兴继续和你一起工作。")
            return AIMessage(
                content="你好。我目前没有可用的跨会话称呼偏好。"
            )
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(
                content="真实后端分析完成，报告已经持久化并可下载。"
            )
        dataset = _input_dataset(messages)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "generate_live_report",
                    "args": {
                        "dataset": {
                            "artifact_id": dataset["artifact_id"],
                        }
                    },
                    "id": "live-e2e-reviewed-report",
                    "type": "tool_call",
                }
            ],
        )


def _capability_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(GenerateReportCapability())
    normalize = next(
        capability
        for capability in build_atomic_capabilities()
        if capability.spec.name == "normalize_expression"
    )
    registry.register(normalize)
    skills = SkillCatalog()
    skills.register(
        SkillDefinition(
            name="live-e2e-analysis",
            description="真实产品闭环测试使用的受控分析 Skill。",
            tools=("generate_live_report",),
            content="只在输入数据集通过 ownership 校验后生成确定性 CSV 报告。",
        )
    )
    return DomainCapabilityLayer(registry=registry, skills=skills)


async def _drop_schemas(settings: PostgresSettings) -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.psycopg_conninfo,
        autocommit=True,
    ) as connection:
        for schema_name in (settings.checkpoint_schema, settings.app_schema):
            await connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


async def _serve() -> None:
    dsn = os.environ.get("OMNICELL_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("运行 live E2E 前必须设置 OMNICELL_TEST_POSTGRES_DSN")
    port = int(os.environ.get("OMNICELL_LIVE_API_PORT", "18080"))
    if not 1 <= port <= 65535:
        raise ValueError("OMNICELL_LIVE_API_PORT 超出合法端口范围")

    suffix = f"{os.getpid()}_{uuid4().hex[:10]}"
    settings = PostgresSettings(
        dsn=dsn,
        app_schema=os.environ.get(
            "OMNICELL_LIVE_APP_SCHEMA", f"omnicell_live_app_{suffix}"
        ),
        checkpoint_schema=os.environ.get(
            "OMNICELL_LIVE_CHECKPOINT_SCHEMA",
            f"omnicell_live_checkpoint_{suffix}",
        ),
        pool_min_size=1,
        pool_max_size=6,
    )
    persistence = PersistenceRuntime(settings)
    coordinator: RunCoordinator | None = None
    workspace_parent = os.environ.get("OMNICELL_LIVE_WORKSPACE")
    preserve_data = os.environ.get("OMNICELL_LIVE_E2E_PRESERVE_DATA") == "1"
    workspace: tempfile.TemporaryDirectory[str] | None = None
    if preserve_data:
        if workspace_parent is None:
            raise RuntimeError("inspect 模式必须设置 OMNICELL_LIVE_WORKSPACE")
        workspace_path = Path(workspace_parent) / "backend"
        workspace_path.mkdir(parents=True, exist_ok=False)
    else:
        workspace = tempfile.TemporaryDirectory(
            prefix="workspace-",
            dir=workspace_parent,
        )
        workspace_path = Path(workspace.name)
    try:
        await persistence.initialize_schemas()
        await persistence.open()
        memory_service = MemoryService(persistence.unit_of_work)
        memory_runtime = PostgresMemoryRuntime(
            persistence.unit_of_work,
            service=memory_service,
        )
        agent_factory = AgentLoopFactory(
            _capability_layer(),
            model_factory=ControlledLiveModel,
            policy=DefaultToolPolicy(
                review_capabilities=frozenset({"generate_live_report"})
            ),
            capability_invoker_factory=CooperativeInProcessCapabilityInvoker,
            config=AgentLoopConfig(
                max_turns=6,
                max_model_calls=8,
                max_tool_calls=6,
                timeout_seconds=120,
            ),
        )
        coordinator = RunCoordinator(
            persistence.unit_of_work,
            checkpointer=persistence.checkpoints.get_saver(),
            agent_factory=agent_factory,
            workspace_root=workspace_path / "workspaces",
            memory_runtime=memory_runtime,
        )
        app = create_app(
            ApiService(
                persistence.unit_of_work,
                coordinator,
                memory_service=memory_service,
            )
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        print(
            f"LIVE_E2E_READY app_schema={settings.app_schema} "
            f"checkpoint_schema={settings.checkpoint_schema}",
            flush=True,
        )
        await server.serve()
    finally:
        if coordinator is not None:
            await coordinator.close()
        await persistence.close()
        if preserve_data:
            print(
                f"LIVE_E2E_PRESERVED app_schema={settings.app_schema} "
                f"checkpoint_schema={settings.checkpoint_schema} "
                f"workspace={workspace_path}",
                flush=True,
            )
        else:
            await _drop_schemas(settings)
            if workspace is not None:
                workspace.cleanup()
            print(
                f"LIVE_E2E_CLEANED app_schema={settings.app_schema} "
                f"checkpoint_schema={settings.checkpoint_schema}",
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(_serve())
