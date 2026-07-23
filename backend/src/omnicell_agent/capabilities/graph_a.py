"""Graph A workflow and its stable read-only inspection tool."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, ContextManager, cast
from uuid import uuid4

from langchain_core.messages import HumanMessage

from omnicell_agent.pipeline.graph import MAX_RETRIES, build_pipeline_graph
from omnicell_agent.pipeline.nodes.context_resolver import run_context_resolver
from omnicell_agent.pipeline.nodes.executor import graph_a_python_session_scope
from omnicell_agent.schema.contract import MarkerTableContract

from .contracts import (
    AnalysisStepSummary,
    CapabilityKind,
    CapabilityRequest,
    CapabilitySpec,
    CapabilityStatus,
    DatasetContext,
    InspectDatasetContextRequest,
    InspectDatasetContextResult,
    SingleCellAnalysisRequest,
    SingleCellAnalysisResult,
)
from .errors import CapabilityExecutionError
from .registry import CapabilityContext


GraphFactory = Callable[[], Any]
PythonSessionScopeFactory = Callable[[Path], ContextManager[Any]]
ContextResolver = Callable[[dict[str, Any]], dict[str, Any]]


def _dataset_context(payload: dict[str, Any]) -> DatasetContext:
    return DatasetContext(
        species=str(payload.get("species") or "Unknown"),
        tissue=str(payload.get("tissue") or "Unknown"),
        disease_state=str(payload.get("disease_state") or "Unknown"),
        goal_type=str(payload.get("goal_type") or "general_annotation"),
    )


class InspectSingleCellContextCapability:
    spec = CapabilitySpec(
        name="inspect_single_cell_context",
        kind=CapabilityKind.READ_ONLY,
        description="读取单细胞数据的轻量元数据并解析物种、组织、疾病状态与任务类型。",
        prompt_hint=(
            "仅在需要确认数据的物种、组织、疾病状态或任务类型时调用；"
            "不要为了普通问答或已经明确的上下文重复检查。"
        ),
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


class SingleCellAnalysisCapability:
    spec = CapabilitySpec(
        name="single_cell_analysis",
        kind=CapabilityKind.WORKFLOW,
        description="运行保留规划、受控执行、评估与重试语义的完整 Graph A。",
        prompt_hint=(
            "仅在用户要求完整、开放式的单细胞分析流程时调用。"
            "若目标只是质控、归一化、聚类、marker 提取或绘图，应优先使用对应原子 Tool；"
            "执行前应加载 single-cell-analysis Skill。"
        ),
    )
    request_model = SingleCellAnalysisRequest
    result_model = SingleCellAnalysisResult

    def __init__(
        self,
        *,
        graph_factory: GraphFactory = build_pipeline_graph,
        scope_factory: PythonSessionScopeFactory | None = None,
    ) -> None:
        self._graph_factory = graph_factory
        self._scope_factory = scope_factory or (
            lambda workspace: graph_a_python_session_scope(
                host_workspace=str(workspace)
            )
        )

    def invoke(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
    ) -> SingleCellAnalysisResult:
        typed = cast(SingleCellAnalysisRequest, request)
        raw_data_path = context.artifacts.sandbox_path(
            typed.dataset,
            expected_kind="dataset",
        )
        output_token = uuid4().hex
        marker_relative = context.artifacts.scoped_output_path(
            f"artifacts/graph-a/{output_token}/markers.json"
        )
        marker_sandbox_path = f"/app/data/{marker_relative}"
        output_sandbox_root = marker_sandbox_path.rsplit("/", 1)[0]
        before = context.artifacts.snapshot_files()
        instruction = (
            f"{typed.goal}\n\n"
            "[SYSTEM INSTRUCTION: Please strictly ensure that the final step exports "
            "the marker genes as a standardized JSON array to the path: "
            f"{marker_sandbox_path}. All generated output files must stay under "
            f"{output_sandbox_root}.]"
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
                step_type=str(step.get("step_type") or "custom_code"),
                skill_name=step.get("skill_name"),
                instruction=step.get("instruction"),
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
            raise RuntimeError("Graph A 在未达到完成或熔断条件时结束")

        marker_path = context.artifacts.workspace / marker_relative
        marker_ref = None
        marker_contract = None
        # 先完成 invocation 输出树的最终边界/配额扫描及安全发布。marker
        # 不能在这条边界之前通过普通路径 API 被跟随或解析。
        produced = context.artifacts.publish_new_files(
            before,
            within_output_scope=context.artifacts.output_scope is not None,
        )
        marker_uri = f"workspace://{marker_relative}"
        if any(ref.uri == marker_uri for ref in produced):
            try:
                candidate_marker_ref = context.artifacts.publish(
                    marker_path,
                    kind="marker_table",
                    media_type="application/json",
                )
                with context.artifacts.open_verified(
                    candidate_marker_ref,
                    expected_kind="marker_table",
                ) as marker_stream:
                    marker_contract = MarkerTableContract.load_from_stream(marker_stream)
            except Exception as exc:
                if completed:
                    raise CapabilityExecutionError(
                        "Graph A 已结束但 mandatory marker contract 无效"
                    ) from exc
            else:
                marker_ref = candidate_marker_ref
        if completed and marker_contract is None:
            raise CapabilityExecutionError(
                "Graph A 已结束但未产出 mandatory marker contract"
            )
        if completed and not marker_contract.markers:
            raise CapabilityExecutionError(
                "Graph A 已结束但 mandatory marker contract 为空"
            )

        if marker_contract is not None:
            assert marker_ref is not None
            produced = [ref for ref in produced if ref.uri != marker_ref.uri]
            produced.append(marker_ref)

        diagnostic = str(
            eval_record.get("feedback")
            or (final_state.get("sandbox_execution_result") or {}).get("stderr")
            or ""
        ).strip()
        return SingleCellAnalysisResult(
            status=status,
            context=resolved_context,
            steps=steps,
            artifacts=produced,
            marker_table=marker_ref,
            diagnostic_summary=diagnostic[:2_000] or None,
        )


__all__ = [
    "InspectSingleCellContextCapability",
    "SingleCellAnalysisCapability",
]
