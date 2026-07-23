import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END

from omnicell_agent.pipeline import graph as graph_a
from omnicell_agent.pipeline.nodes import context_resolver, evaluator, executor


class ControlledPythonSession:
    def __init__(self) -> None:
        self.executed_code: list[str] = []
        self.start_calls = 0
        self.cleanup_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def cleanup(self) -> None:
        self.cleanup_calls += 1

    def execute_code(self, code: str) -> dict[str, Any]:
        self.executed_code.append(code)
        return {
            "status": "success",
            "stdout": "controlled-runtime-ok",
            "stderr": "",
        }


class RetryCleanupPythonSession(ControlledPythonSession):
    def cleanup(self) -> None:
        self.cleanup_calls += 1
        if self.cleanup_calls == 1:
            raise RuntimeError("transient cleanup failure")


def _graph_a_state() -> dict[str, Any]:
    return {
        "raw_data_path": "/app/data/pbmc3k_raw.h5ad",
        "marker_table_path": "/app/data/markers.json",
        "messages": [HumanMessage(content="请对人类 PBMC 做免疫细胞分析")],
        "task_context": {},
        "plan_steps": [],
        "current_step_index": 0,
        "last_generated_code": "",
        "sandbox_execution_result": {},
    }


def test_python_session_scope_retries_transient_owned_cleanup_failure() -> None:
    session = RetryCleanupPythonSession()

    with executor.graph_a_python_session_scope(session):  # type: ignore[arg-type]
        pass

    assert session.start_calls == 1
    assert session.cleanup_calls == 2


def test_pca_clustering_skill_uses_invocation_artifact_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "invocation" / "artifacts" / "graph-a"
    settings = SimpleNamespace(figdir=None)
    calls: list[tuple[str, str]] = []
    fake_scanpy = SimpleNamespace(
        settings=settings,
        pp=SimpleNamespace(),
        tl=SimpleNamespace(),
        pl=SimpleNamespace(
            umap=lambda _adata, **kwargs: calls.append(
                (str(settings.figdir), str(kwargs["save"]))
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "scanpy", fake_scanpy)
    adata = SimpleNamespace(
        obsm={"X_pca": object(), "X_umap": object()},
        obs={"leiden": object()},
    )
    script = (
        Path(__file__).parents[2]
        / "src"
        / "omnicell_agent"
        / "skills"
        / "pca_clustering"
        / "scripts"
        / "execute.py"
    )

    runpy.run_path(
        script,
        init_globals={
            "adata": adata,
            "artifact_output_root": str(output_root),
        },
    )

    assert output_root.is_dir()
    assert calls == [(str(output_root), "_omnicell_umap.png")]


@pytest.mark.parametrize(
    ("eval_record", "current_index", "plan_count", "retries", "expected"),
    [
        ({"status": "success"}, 1, 1, 0, END),
        ({"status": "success"}, 1, 2, 0, "programmer"),
        ({"status": "error"}, 0, 1, 2, "programmer"),
        ({"status": "error"}, 0, 1, 3, END),
    ],
)
def test_graph_a_route_contract(
    eval_record: dict[str, str],
    current_index: int,
    plan_count: int,
    retries: int,
    expected: str,
) -> None:
    state = {
        "task_context": {"eval_record": eval_record, "retry_count": retries},
        "current_step_index": current_index,
        "plan_steps": [{} for _ in range(plan_count)],
    }

    assert graph_a.route_evaluation(state) == expected


def test_graph_a_controlled_end_to_end_contract(
    monkeypatch: pytest.MonkeyPatch,
    controlled_llm_calls: list[str],
) -> None:
    session = ControlledPythonSession()
    monkeypatch.setattr(
        context_resolver,
        "_probe_h5ad_metadata",
        lambda _path: {
            "filename": "pbmc3k_raw.h5ad",
            "uns_keys": [],
            "obs_columns": [],
            "obs_tissue_values": [],
            "obs_organism_values": [],
        },
    )
    monkeypatch.setattr(evaluator, "ENABLE_VISION_EVAL", False)

    with executor.graph_a_python_session_scope(session):  # type: ignore[arg-type]
        final_state = graph_a.build_pipeline_graph().invoke(_graph_a_state())

    resolved = final_state["task_context"]["resolved_context"]
    projection = {
        "context": {
            "species": resolved["species"],
            "tissue": resolved["tissue"],
            "disease_state": resolved["disease_state"],
            "goal_type": resolved["goal_type"],
        },
        "plan_steps": final_state["plan_steps"],
        "current_step_index": final_state["current_step_index"],
        "sandbox_execution_result": final_state["sandbox_execution_result"],
        "eval_record": final_state["task_context"]["eval_record"],
        "retry_count": final_state["task_context"]["retry_count"],
        "failed_attempts": final_state["task_context"]["failed_attempts"],
    }
    assert projection == {
        "context": {
            "species": "Human",
            "tissue": "PBMC",
            "disease_state": "Healthy",
            "goal_type": "immune_profiling",
        },
        "plan_steps": [
            {
                "step_type": "skill_call",
                "skill_name": "normalize_log",
                "instruction": "执行标准归一化与对数变换",
                "background_context": None,
            }
        ],
        "current_step_index": 1,
        "sandbox_execution_result": {
            "status": "success",
            "stdout": "controlled-runtime-ok",
            "stderr": "",
        },
        "eval_record": {"status": "success", "feedback": ""},
        "retry_count": 0,
        "failed_attempts": [],
    }
    assert controlled_llm_calls == ["ContextProfile", "AnalysisPlan"]
    assert "sc.pp.normalize_total" in final_state["last_generated_code"]
    assert session.executed_code[0] == (
        "raw_data_path = '/app/data/pbmc3k_raw.h5ad'\n"
        "marker_table_path = '/app/data/markers.json'\n"
        "artifact_output_root = '/app/data'\n"
    )
    assert len(session.executed_code) == 4
    assert session.start_calls == 1
    assert session.cleanup_calls == 1


def test_graph_a_error_projection_is_deterministic() -> None:
    state = _graph_a_state()
    state.update(
        {
            "last_generated_code": "raise RuntimeError('boom')",
            "sandbox_execution_result": {"status": "error", "stderr": "boom"},
        }
    )

    result = evaluator.run_evaluator(state)

    assert result == {
        "task_context": {
            "retry_count": 1,
            "failed_attempts": [
                {
                    "code": "raise RuntimeError('boom')",
                    "feedback": (
                        "Sandbox Execution Failed! Traceback info:\n\n"
                        "boom\nPlease fix your Python代码。"
                    ),
                }
            ],
            "eval_record": {
                "status": "error",
                "feedback": (
                    "Sandbox Execution Failed! Traceback info:\n\n"
                    "boom\nPlease fix your Python代码。"
                ),
            },
        }
    }
