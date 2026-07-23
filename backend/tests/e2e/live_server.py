"""为 Playwright live E2E 启动受控 FastAPI + PostgreSQL 后端。

该启动器只用于测试：模型与 capability 都是确定性替身，但 API、
RunCoordinator、Agent Loop、PostgreSQL 事件日志、LangGraph checkpointer 和
artifact 边界均使用真实实现。每次进程启动创建独立 schema，退出时删除。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
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
from omnicell_agent.capabilities.catalog import SkillCatalog, SkillDefinition  # noqa: E402
from omnicell_agent.capabilities.contracts import (  # noqa: E402
    ArtifactRef,
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
)
from omnicell_agent.capabilities.registry import (  # noqa: E402
    CapabilityContext,
    CapabilityRegistry,
)
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
        kind=CapabilityKind.ATOMIC,
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


def _finish(final_response: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "finish_task",
                "args": {"final_response": final_response},
                "id": "live-e2e-finish",
                "type": "tool_call",
            }
        ],
    )


def _input_dataset(messages: list[object]) -> dict[str, object]:
    for message in messages:
        if not isinstance(message, SystemMessage):
            continue
        content = str(message.content)
        if "输入 artifact 权威描述" not in content:
            continue
        descriptors = json.loads(content.split("：\n", 1)[1])
        if not isinstance(descriptors, list) or len(descriptors) != 1:
            raise ValueError("live E2E 必须且只能提交一个 dataset artifact")
        descriptor = descriptors[0]
        if not isinstance(descriptor, dict) or descriptor.get("kind") != "dataset":
            raise ValueError("live E2E 输入必须是 dataset artifact")
        return descriptor
    raise ValueError("live E2E Agent 未收到权威输入 artifact 描述")


class ControlledLiveModel:
    """确定性 Agent model；不创建供应商客户端，也不访问网络。"""

    def bind_tools(self, tools: list[dict[str, object]]) -> "ControlledLiveModel":
        names = {
            str(tool["function"]["name"])
            for tool in tools
            if isinstance(tool.get("function"), dict)
        }
        if names != {
            "create_task_plan",
            "finish_task",
            "generate_live_report",
            "load_skill",
            "update_task_plan",
        }:
            raise ValueError(f"live E2E tool surface 非预期：{sorted(names)}")
        return self

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        goal = next(
            (
                str(message.content)
                for message in messages
                if isinstance(message, HumanMessage)
            ),
            "",
        )
        if "受控阻塞" in goal:
            await asyncio.Event().wait()
        if any(isinstance(message, ToolMessage) for message in messages):
            return _finish("真实后端分析完成，报告已经持久化并可下载。")
        dataset = _input_dataset(messages)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "generate_live_report",
                    "args": {"dataset": dataset},
                    "id": "live-e2e-reviewed-report",
                    "type": "tool_call",
                }
            ],
        )


def _capability_layer() -> DomainCapabilityLayer:
    registry = CapabilityRegistry()
    registry.register(GenerateReportCapability())
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
    workspace = tempfile.TemporaryDirectory(
        prefix="workspace-",
        dir=workspace_parent,
    )
    try:
        await persistence.initialize_schemas()
        await persistence.open()
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
            workspace_root=Path(workspace.name) / "workspaces",
        )
        app = create_app(ApiService(persistence.unit_of_work, coordinator))
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
        await _drop_schemas(settings)
        workspace.cleanup()
        print(
            f"LIVE_E2E_CLEANED app_schema={settings.app_schema} "
            f"checkpoint_schema={settings.checkpoint_schema}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(_serve())
