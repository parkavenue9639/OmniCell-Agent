from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from omnicell_agent import llm
from omnicell_agent.capabilities.artifacts import (
    ArtifactBoundaryError,
    ConversationArtifactStore,
)
from omnicell_agent.capabilities.contracts import (
    CapabilityStatus,
    InspectDatasetContextRequest,
    SingleCellAnalysisRequest,
)
from omnicell_agent.capabilities.graph_a import (
    InspectSingleCellContextCapability,
    SingleCellAnalysisCapability,
)
from omnicell_agent.capabilities.errors import CapabilityExecutionError
from omnicell_agent.capabilities.registry import CapabilityContext
from omnicell_agent.pipeline.nodes import context_resolver, evaluator, executor
from omnicell_agent.pipeline.nodes.context_resolver import ContextProfile
from omnicell_agent.schema.contract import MarkerTableContract
from omnicell_agent.schema.state import AnalysisPlan, PlanStep


def _context(tmp_path: Path) -> tuple[CapabilityContext, Any]:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path / "conversation")
    dataset = store.workspace / "input" / "pbmc.h5ad"
    dataset.parent.mkdir()
    dataset.write_bytes(b"controlled-h5ad")
    ref = store.publish(
        dataset,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    return CapabilityContext(conversation_id, store), ref


def _invocation_context(tmp_path: Path) -> tuple[CapabilityContext, Any]:
    conversation_id = uuid4()
    workspace = tmp_path / "conversation"
    base_store = ConversationArtifactStore(conversation_id, workspace)
    dataset = base_store.workspace / "input" / "pbmc.h5ad"
    dataset.parent.mkdir()
    dataset.write_bytes(b"controlled-h5ad")
    ref = base_store.publish(
        dataset,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    invocation_store = ConversationArtifactStore(
        conversation_id,
        workspace,
        invocation_id="a" * 32,
    )
    invocation_store.register_trusted(ref)
    return CapabilityContext(conversation_id, invocation_store), ref


def test_context_tool_projects_typed_context_without_exposing_node_state(
    tmp_path: Path,
) -> None:
    context, dataset = _context(tmp_path)
    seen: dict[str, Any] = {}

    def resolver(state: dict[str, Any]) -> dict[str, Any]:
        seen.update(state)
        return {
            "task_context": {
                "resolved_context": {
                    "species": "Human",
                    "tissue": "PBMC",
                    "disease_state": "Healthy",
                    "goal_type": "immune_profiling",
                    "sources": {"private": "not projected"},
                }
            }
        }

    result = InspectSingleCellContextCapability(resolver).invoke(
        InspectDatasetContextRequest(dataset=dataset, goal="识别 PBMC 语境"),
        context,
    )

    assert result.model_dump() == {
        "context": {
            "species": "Human",
            "tissue": "PBMC",
            "disease_state": "Healthy",
            "goal_type": "immune_profiling",
        }
    }
    assert seen["raw_data_path"] == "/app/data/input/pbmc.h5ad"
    assert seen["task_context"] == {
        "conversation_workspace": str(context.artifacts.workspace)
    }


class _ControlledGraph:
    def __init__(
        self,
        *,
        aborted: bool = False,
        incomplete: bool = False,
        omit_marker: bool = False,
    ) -> None:
        self.aborted = aborted
        self.incomplete = incomplete
        self.omit_marker = omit_marker
        self.initial_state: dict[str, Any] | None = None

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.initial_state = state
        workspace = Path(state["task_context"]["conversation_workspace"])
        marker_relative = state["marker_table_path"][len("/app/data/") :]
        if not self.aborted and not self.incomplete and not self.omit_marker:
            marker_path = workspace / marker_relative
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(
                """[{"gene":"IL7R","cluster":"0","pvals":0.001,"pvals_adj":0.01,"logfoldchanges":2.5,"pct.1":0.8,"pct.2":0.1}]""",
                encoding="utf-8",
            )
        status = "error" if self.aborted or self.incomplete else "success"
        retries = 1 if self.incomplete else (3 if self.aborted else 0)
        return {
            **state,
            "plan_steps": [
                {
                    "step_type": "skill_call",
                    "skill_name": "marker_genes_extractor",
                    "instruction": "提取 marker",
                }
            ],
            "current_step_index": 0 if status == "error" else 1,
            "task_context": {
                **state["task_context"],
                "resolved_context": {
                    "species": "Human",
                    "tissue": "PBMC",
                    "disease_state": "Healthy",
                    "goal_type": "marker_discovery",
                },
                "eval_record": {"status": status, "feedback": "boom" if status == "error" else ""},
                "retry_count": retries,
            },
            "sandbox_execution_result": {
                "status": status,
                "stderr": "boom" if status == "error" else "",
            },
        }


def test_graph_a_workflow_owns_scope_and_returns_artifact_projection(
    tmp_path: Path,
) -> None:
    context, dataset = _context(tmp_path)
    graph = _ControlledGraph()
    scoped: list[Path] = []

    @contextmanager
    def scope_factory(workspace: Path):
        scoped.append(workspace)
        yield

    capability = SingleCellAnalysisCapability(
        graph_factory=lambda: graph,
        scope_factory=scope_factory,
    )
    result = capability.invoke(
        SingleCellAnalysisRequest(dataset=dataset, goal="提取 marker"),
        context,
    )

    assert result.status == CapabilityStatus.COMPLETED
    assert result.context.goal_type == "marker_discovery"
    assert [step.status for step in result.steps] == ["completed"]
    assert result.marker_table is not None
    assert result.marker_table.kind == "marker_table"
    assert context.artifacts.resolve(result.marker_table).is_file()
    assert [ref.uri for ref in result.artifacts] == [result.marker_table.uri]
    assert scoped == [context.artifacts.workspace]
    assert graph.initial_state is not None
    assert graph.initial_state["raw_data_path"] == "/app/data/input/pbmc.h5ad"
    assert graph.initial_state["marker_table_path"].startswith(
        "/app/data/artifacts/graph-a/"
    )


def test_graph_a_workflow_projects_max_retry_as_aborted(tmp_path: Path) -> None:
    context, dataset = _context(tmp_path)
    capability = SingleCellAnalysisCapability(
        graph_factory=lambda: _ControlledGraph(aborted=True),
        scope_factory=lambda _workspace: nullcontext(),
    )

    result = capability.invoke(
        SingleCellAnalysisRequest(dataset=dataset, goal="失败路径"),
        context,
    )

    assert result.status == CapabilityStatus.ABORTED
    assert result.marker_table is None
    assert result.diagnostic_summary == "boom"
    assert [step.status for step in result.steps] == ["pending"]


def test_graph_a_workflow_rejects_completed_state_without_mandatory_marker(
    tmp_path: Path,
) -> None:
    context, dataset = _context(tmp_path)
    capability = SingleCellAnalysisCapability(
        graph_factory=lambda: _ControlledGraph(omit_marker=True),
        scope_factory=lambda _workspace: nullcontext(),
    )

    with pytest.raises(CapabilityExecutionError, match="mandatory marker"):
        capability.invoke(
            SingleCellAnalysisRequest(dataset=dataset, goal="必须导出 marker"),
            context,
        )


def test_graph_a_rejects_marker_symlink_before_contract_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, dataset = _invocation_context(tmp_path)
    outside_marker = tmp_path / "outside-marker.json"
    outside_marker.write_text(
        '[{"gene":"HOST","cluster":"0","pvals":0.001,'
        '"pvals_adj":0.01,"logfoldchanges":1.0,"pct.1":0.8,"pct.2":0.1}]',
        encoding="utf-8",
    )
    parser_called = False

    class SymlinkMarkerGraph(_ControlledGraph):
        def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
            result = super().invoke(state)
            workspace = Path(state["task_context"]["conversation_workspace"])
            marker_relative = state["marker_table_path"][len("/app/data/") :]
            marker_path = workspace / marker_relative
            marker_path.unlink()
            marker_path.symlink_to(outside_marker)
            return result

    def fail_if_parsed(cls, stream):
        nonlocal parser_called
        parser_called = True
        raise AssertionError("不应解析越界 marker")

    monkeypatch.setattr(
        MarkerTableContract,
        "load_from_stream",
        classmethod(fail_if_parsed),
    )
    capability = SingleCellAnalysisCapability(
        graph_factory=SymlinkMarkerGraph,
        scope_factory=lambda _workspace: nullcontext(),
    )

    with pytest.raises(ArtifactBoundaryError, match="symlink"):
        capability.invoke(
            SingleCellAnalysisRequest(dataset=dataset, goal="拒绝 marker symlink"),
            context,
        )

    assert parser_called is False


def test_graph_a_rejects_marker_replacement_before_contract_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, dataset = _invocation_context(tmp_path)
    original_publish = context.artifacts.publish
    parser_called = False

    def replacing_publish(path, *, kind, media_type=None, metadata=None):
        ref = original_publish(
            path,
            kind=kind,
            media_type=media_type,
            metadata=metadata,
        )
        if kind == "marker_table":
            marker_path = Path(path)
            marker_path.write_text(
                '[{"gene":"SWAPPED","cluster":"0","pvals":0.001,'
                '"pvals_adj":0.01,"logfoldchanges":1.0,'
                '"pct.1":0.8,"pct.2":0.1}]',
                encoding="utf-8",
            )
        return ref

    def fail_if_parsed(cls, stream):
        nonlocal parser_called
        parser_called = True
        raise AssertionError("不应解析被替换的 marker")

    monkeypatch.setattr(context.artifacts, "publish", replacing_publish)
    monkeypatch.setattr(
        MarkerTableContract,
        "load_from_stream",
        classmethod(fail_if_parsed),
    )
    capability = SingleCellAnalysisCapability(
        graph_factory=_ControlledGraph,
        scope_factory=lambda _workspace: nullcontext(),
    )

    with pytest.raises(CapabilityExecutionError, match="mandatory marker contract 无效"):
        capability.invoke(
            SingleCellAnalysisRequest(dataset=dataset, goal="拒绝 marker 替换"),
            context,
        )

    assert parser_called is False


def test_graph_a_workflow_rejects_non_terminal_projection(tmp_path: Path) -> None:
    context, dataset = _context(tmp_path)
    capability = SingleCellAnalysisCapability(
        graph_factory=lambda: _ControlledGraph(incomplete=True),
        scope_factory=lambda _workspace: nullcontext(),
    )

    with pytest.raises(RuntimeError, match="未达到"):
        capability.invoke(
            SingleCellAnalysisRequest(dataset=dataset, goal="非终态"),
            context,
        )


def test_graph_a_workflow_adapter_preserves_controlled_real_graph_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, dataset = _context(tmp_path)
    calls: list[str] = []

    class StructuredModel:
        def __init__(self, schema: type) -> None:
            self.schema = schema

        def invoke(self, _messages):
            calls.append(self.schema.__name__)
            if self.schema is ContextProfile:
                return ContextProfile(
                    species="Human",
                    tissue="PBMC",
                    disease_state="Healthy",
                    goal_type="immune_profiling",
                )
            if self.schema is AnalysisPlan:
                return AnalysisPlan(
                    steps=[
                        PlanStep(
                            step_type="skill_call",
                            skill_name="normalize_log",
                            instruction="执行标准归一化与对数变换",
                        ),
                        PlanStep(
                            step_type="skill_call",
                            skill_name="marker_genes_extractor",
                            instruction="导出 marker contract",
                        ),
                    ]
                )
            raise AssertionError(self.schema)

    class ChatModel:
        def with_structured_output(self, schema: type):
            return StructuredModel(schema)

    class ControlledPythonSession:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace
            self.start_calls = 0
            self.cleanup_calls = 0
            self.executed_code: list[str] = []
            self.marker_path = ""

        def start(self) -> None:
            self.start_calls += 1

        def execute_code(self, code: str) -> dict[str, Any]:
            self.executed_code.append(code)
            if code.startswith("raw_data_path = "):
                line = next(
                    line for line in code.splitlines() if line.startswith("marker_table_path = ")
                )
                self.marker_path = line.split(" = ", 1)[1].strip("'\"")
            if "rank_genes_groups" in code:
                relative = self.marker_path[len("/app/data/") :]
                marker_path = self.workspace / relative
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text(
                    """[{"gene":"IL7R","cluster":"0","pvals":0.001,"pvals_adj":0.01,"logfoldchanges":2.5,"pct.1":0.8,"pct.2":0.1}]""",
                    encoding="utf-8",
                )
            return {"status": "success", "stdout": "ok", "stderr": ""}

        def cleanup(self) -> None:
            self.cleanup_calls += 1

    session = ControlledPythonSession(context.artifacts.workspace)
    monkeypatch.setattr(llm, "get_llm_by_alias", lambda *args, **kwargs: ChatModel())
    monkeypatch.setattr(
        context_resolver,
        "_probe_h5ad_metadata",
        lambda _path: {
            "filename": "pbmc.h5ad",
            "uns_keys": [],
            "obs_columns": [],
            "obs_tissue_values": [],
            "obs_organism_values": [],
        },
    )
    monkeypatch.setattr(evaluator, "ENABLE_VISION_EVAL", False)
    capability = SingleCellAnalysisCapability(
        scope_factory=lambda _workspace: executor.graph_a_python_session_scope(
            session  # type: ignore[arg-type]
        )
    )
    result = capability.invoke(
        SingleCellAnalysisRequest(dataset=dataset, goal="分析 PBMC"),
        context,
    )

    assert result.status == CapabilityStatus.COMPLETED
    assert result.context.model_dump() == {
        "species": "Human",
        "tissue": "PBMC",
        "disease_state": "Healthy",
        "goal_type": "immune_profiling",
    }
    assert [(step.skill_name, step.status) for step in result.steps] == [
        ("normalize_log", "completed"),
        ("marker_genes_extractor", "completed"),
    ]
    assert calls == ["ContextProfile", "AnalysisPlan"]
    assert session.start_calls == 1
    assert session.cleanup_calls == 1
    assert len(session.executed_code) == 8
    assert result.marker_table is not None


def test_vision_evaluator_resolves_image_from_conversation_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "conversation"
    image_path = workspace / "plots" / "embedding.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"controlled-png")
    calls: list[str] = []

    class VisionStructuredModel:
        def invoke(self, _messages):
            calls.append("vision")
            return evaluator.VisionEvalResult(status="success", feedback="looks good")

    class VisionModel:
        def with_structured_output(self, schema):
            assert schema is evaluator.VisionEvalResult
            return VisionStructuredModel()

    monkeypatch.setattr(evaluator, "ENABLE_VISION_EVAL", True)
    monkeypatch.setattr(llm, "get_llm_by_alias", lambda *args, **kwargs: VisionModel())
    result = evaluator.run_evaluator(
        {
            "messages": [],
            "current_step_index": 0,
            "last_generated_code": "plot()",
            "sandbox_execution_result": {
                "status": "success",
                "stdout": "saving figure to file /app/data/plots/embedding.png",
                "stderr": "",
            },
            "task_context": {"conversation_workspace": str(workspace)},
        }
    )

    assert calls == ["vision"]
    assert result["current_step_index"] == 1
    assert result["task_context"]["eval_record"] == {
        "status": "success",
        "feedback": "",
    }


def test_image_path_rejects_conversation_workspace_symlink_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "conversation"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (workspace / "escape.png").symlink_to(outside)

    assert (
        evaluator.extract_image_path(
            "saving figure to file /app/data/escape.png",
            str(workspace),
        )
        == ""
    )
