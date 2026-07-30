"""Exploratory analysis engine and its stable dataset inspection Tool."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, ContextManager, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage

from omnicell_agent.pipeline.graph import (
    MAX_RETRIES,
    build_exploratory_analysis_engine,
)
from omnicell_agent.pipeline.nodes.context_resolver import run_context_resolver
from omnicell_agent.pipeline.nodes.executor import analysis_python_session_scope
from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import (
    AnalysisStepSummary,
    CapabilityEffect,
    CapabilityMode,
    CapabilityRequest,
    CapabilitySpec,
    CapabilityStatus,
    DatasetContext,
    InspectDatasetContextRequest,
    InspectDatasetContextResult,
    ExploratoryAnalysisRequest,
    ExploratoryAnalysisResult,
)
from .errors import CapabilityExecutionError
from .exploratory_evidence import build_exploratory_result_manifest
from .registry import CapabilityContext


AnalysisEngineFactory = Callable[[], Any]
PythonSessionScopeFactory = Callable[[Path], ContextManager[Any]]
ContextResolver = Callable[[dict[str, Any]], dict[str, Any]]


def _dataset_context(payload: dict[str, Any]) -> DatasetContext:
    return DatasetContext(
        species=str(payload.get("species") or "Unknown"),
        tissue=str(payload.get("tissue") or "Unknown"),
        disease_state=str(payload.get("disease_state") or "Unknown"),
        goal_type=str(payload.get("goal_type") or "unknown"),
    )


class InspectDatasetCapability:
    spec = CapabilitySpec(
        name="inspect_dataset",
        mode=CapabilityMode.INSPECT,
        effect=CapabilityEffect.INSPECT,
        description="读取单细胞数据的轻量元数据并解析物种、组织、疾病状态与任务类型。",
        prompt_hint=(
            "仅在需要确认数据的物种、组织、疾病状态或任务类型时调用；"
            "这是只读元数据检查，不执行预处理或领域分析；"
            "不要为了普通问答或已经明确且无需核验的上下文重复检查。"
        ),
        consumes=("dataset",),
        preconditions=("输入是当前 conversation 已登记的 dataset",),
        recommended_skills=("single-cell-preprocessing",),
    )
    request_model = InspectDatasetContextRequest
    result_model = InspectDatasetContextResult

    def __init__(self, resolver: ContextResolver = run_context_resolver) -> None:
        self._resolver = resolver

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> InspectDatasetContextResult:
        typed = cast(InspectDatasetContextRequest, request)
        raw_data_path = context.artifacts.sandbox_path(
            typed.dataset,
            expected_kind="dataset",
        )
        result = self._resolver(
            {
                "raw_data_path": raw_data_path,
                "messages": [HumanMessage(content=typed.goal)],
                "task_context": {
                    "conversation_workspace": str(context.artifacts.workspace)
                },
            }
        )
        resolved = (result.get("task_context") or {}).get("resolved_context") or {}
        return InspectDatasetContextResult(context=_dataset_context(resolved))


class ExploratoryAnalysisCapability:
    spec = CapabilitySpec(
        name="run_exploratory_analysis",
        mode=CapabilityMode.COMPOSITE,
        effect=CapabilityEffect.CUSTOM,
        description="为标准领域 Tool 无法覆盖的开放式单细胞目标执行受控探索性分析。",
        prompt_hint=(
            "仅在用户目标无法由已注册的检查、变换、分析或可视化 Tool 完成时调用；"
            "质量控制、归一化、聚类、marker 提取或绘图必须优先使用对应 Tool；"
            "调用前收敛目标、期望 artifact 和验收标准，不能把宽泛措辞扩张为默认全流程；"
            "执行前必须加载 exploratory-analysis Skill。"
        ),
        consumes=("dataset",),
        produces=("marker_table", "image", "analysis_metadata"),
        preconditions=("用户目标无法由标准 Tool 组合充分完成",),
        recommended_skills=("exploratory-analysis",),
        required_skills=("exploratory-analysis",),
    )
    request_model = ExploratoryAnalysisRequest
    result_model = ExploratoryAnalysisResult

    def __init__(
        self,
        *,
        graph_factory: AnalysisEngineFactory = build_exploratory_analysis_engine,
        scope_factory: PythonSessionScopeFactory | None = None,
    ) -> None:
        self._graph_factory = graph_factory
        self._scope_factory = scope_factory or (
            lambda workspace: analysis_python_session_scope(
                host_workspace=str(workspace)
            )
        )

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> ExploratoryAnalysisResult:
        typed = cast(ExploratoryAnalysisRequest, request)
        raw_data_path = context.artifacts.sandbox_path(
            typed.dataset,
            expected_kind="dataset",
        )
        output_token = uuid4().hex
        marker_relative = context.artifacts.scoped_output_path(
            f"artifacts/exploratory-analysis/{output_token}/markers.json"
        )
        marker_sandbox_path = f"/app/data/{marker_relative}"
        output_sandbox_root = marker_sandbox_path.rsplit("/", 1)[0]
        before = context.artifacts.snapshot_files()
        instruction = (
            f"{typed.goal}\n\n"
            "[SYSTEM INSTRUCTION: All generated output files must stay under "
            f"{output_sandbox_root}. Only if the user's goal actually requires a "
            "marker table, export it as the registered marker JSON contract to "
            f"{marker_sandbox_path}. Do not add marker analysis to unrelated goals.]"
        )
        initial_state = {
            "raw_data_path": raw_data_path,
            "marker_table_path": marker_sandbox_path,
            "messages": [HumanMessage(content=instruction)],
            "task_context": {
                "conversation_workspace": str(context.artifacts.workspace)
            },
            "plan_steps": [],
            "current_step_index": 0,
            "last_generated_code": "",
            "sandbox_execution_result": {},
        }

        with self._scope_factory(context.artifacts.workspace):
            final_state = self._graph_factory().invoke(initial_state)

        task_context = dict(final_state.get("task_context") or {})
        resolved_context = _dataset_context(task_context.get("resolved_context") or {})
        plan_steps = list(final_state.get("plan_steps") or [])
        current_index = int(final_state.get("current_step_index", 0) or 0)
        steps = [
            AnalysisStepSummary(
                index=index,
                execution_mode=(
                    "deterministic"
                    if step.get("step_type") == "recipe_call"
                    else "generated"
                ),
                operation_summary=str(
                    step.get("instruction")
                    or "执行受控探索性分析步骤"
                ),
                status="completed" if index < current_index else "pending",
            )
            for index, step in enumerate(plan_steps)
        ]
        eval_record = dict(task_context.get("eval_record") or {})
        retries = int(task_context.get("retry_count", 0) or 0)
        completed = (
            eval_record.get("status") == "success" and current_index >= len(plan_steps)
        )
        status = CapabilityStatus.COMPLETED if completed else CapabilityStatus.ABORTED
        if not completed and retries < MAX_RETRIES:
            raise RuntimeError("探索性分析在未达到完成或熔断条件时结束")

        marker_ref = None
        marker_contract: MarkerTableContract | None = None
        marker_contracts: dict[str, MarkerTableContract] = {}
        successful_roots = [
            str(item)
            for item in task_context.get("successful_output_roots", [])
        ]
        allowed_prefixes: list[str] = []
        for root in successful_roots:
            if (
                not root.startswith(f"{output_sandbox_root}/attempt-")
                or ".." in root.split("/")
            ):
                raise CapabilityExecutionError(
                    "探索性分析成功输出目录不属于当前 invocation"
                )
            relative = root.removeprefix("/app/data/")
            if relative not in allowed_prefixes:
                allowed_prefixes.append(relative)
        # 先完成 invocation 输出树的最终边界/配额扫描及安全发布。marker
        # 不能在这条边界之前通过普通路径 API 被跟随或解析。
        produced = context.artifacts.publish_new_files(
            before,
            within_output_scope=context.artifacts.output_scope is not None,
            allowed_relative_prefixes=tuple(allowed_prefixes),
        )
        successful_marker_uris = {
            f"workspace://{str(path).removeprefix('/app/data/')}"
            for path in task_context.get("successful_marker_table_paths", [])
            if str(path).startswith(f"{output_sandbox_root}/attempt-")
        }
        marker_candidates = [
            ref for ref in produced if ref.uri in successful_marker_uris
        ]
        if len(marker_candidates) > 1:
            raise CapabilityExecutionError(
                "探索性分析生成了多个 marker table，结果来源不唯一"
            )
        if marker_candidates:
            candidate = marker_candidates[0]
            try:
                candidate_marker_ref = context.artifacts.publish(
                    context.artifacts.workspace
                    / candidate.uri.removeprefix("workspace://"),
                    kind="marker_table",
                    media_type="application/json",
                )
                with context.artifacts.open_verified(
                    candidate_marker_ref,
                    expected_kind="marker_table",
                ) as marker_stream:
                    marker_contract = MarkerTableContract.load_from_stream(marker_stream)
            except Exception as exc:
                raise CapabilityExecutionError(
                    "探索性分析生成的 marker contract 无效"
                ) from exc
            else:
                marker_ref = candidate_marker_ref
                marker_contracts[str(marker_ref.artifact_id)] = marker_contract
        if (
            marker_ref is not None
            and marker_contract is not None
            and not marker_contract.markers
        ):
            raise CapabilityExecutionError(
                "探索性分析生成的 marker contract 为空"
            )

        if marker_ref is not None:
            assert marker_ref is not None
            produced = [ref for ref in produced if ref.uri != marker_ref.uri]
            produced.append(marker_ref)

        result_manifest = build_exploratory_result_manifest(
            context.artifacts,
            produced,
            marker_contracts=marker_contracts,
            source_dataset=typed.dataset,
            acceptance_criterion=typed.acceptance_criterion,
        )
        diagnostic = str(
            eval_record.get("feedback")
            or (final_state.get("sandbox_execution_result") or {}).get("stderr")
            or ""
        ).strip()
        return ExploratoryAnalysisResult(
            status=status,
            context=resolved_context,
            steps=steps,
            artifacts=produced,
            marker_table=marker_ref,
            result_manifest=result_manifest,
            diagnostic_summary=diagnostic[:2_000] or None,
        )


__all__ = [
    "ExploratoryAnalysisCapability",
    "InspectDatasetCapability",
]
