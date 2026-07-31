import runpy
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END

from omnicell_agent.pipeline import graph as analysis_engine
from omnicell_agent.pipeline.nodes import (
    context_resolver,
    evaluator,
    executor,
    programmer,
)


class ControlledPythonSession:
    def __init__(self) -> None:
        self.executed_code: list[str] = []
        self.ensured_directories: list[str] = []
        self.start_calls = 0
        self.cleanup_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def cleanup(self) -> None:
        self.cleanup_calls += 1

    def ensure_dir(self, path: str) -> None:
        self.ensured_directories.append(path)

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


class ExecutingRecipeSession(ControlledPythonSession):
    def __init__(self, adata: Any) -> None:
        super().__init__()
        self.globals: dict[str, Any] = {"adata": adata}

    def execute_code(self, code: str) -> dict[str, Any]:
        self.executed_code.append(code)
        try:
            exec(code, self.globals, self.globals)
        except Exception as exc:
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "stderr": str(exc),
            }
        return {"status": "success", "stdout": "executed", "stderr": ""}


class _MinimalAdata:
    def __init__(self) -> None:
        self.X = np.asarray([[50.0, 100.0]])
        self.uns: dict[str, Any] = {}

    def uns_keys(self) -> list[str]:
        return list(self.uns)

    def copy(self) -> "_MinimalAdata":
        return deepcopy(self)


def _analysis_state() -> dict[str, Any]:
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

    with executor.analysis_python_session_scope(session):  # type: ignore[arg-type]
        pass

    assert session.start_calls == 1
    assert session.cleanup_calls == 2


def test_pca_clustering_recipe_uses_invocation_artifact_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "invocation" / "artifacts" / "analysis"
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
        obsp={"connectivities": object(), "distances": object()},
        uns={
            "neighbors": {},
            "omnicell_scientific_state": {
                "expression_space": "normalized_log1p",
                "pca_signature": "controlled-signature",
                "clustering_signature": "controlled-signature",
            },
        },
        X=np.asarray([[0.1, 1.2], [0.3, 2.4]]),
    )
    script = (
        Path(__file__).parents[2]
        / "src"
        / "omnicell_agent"
        / "recipes"
        / "pca_clustering"
        / "scripts"
        / "execute.py"
    )

    runpy.run_path(
        script,
        init_globals={
            "adata": adata,
            "artifact_output_root": str(output_root),
            "_atomic_parameter_signature": "controlled-signature",
            "tool_parameters": {
                "n_top_genes": 2_000,
                "n_pcs": 40,
                "n_neighbors": 10,
                "resolution": 1.0,
            },
        },
    )

    assert output_root.is_dir()
    assert calls == [(str(output_root), "_omnicell_umap.png")]


def test_pca_recipe_rejects_stale_log_metadata_on_raw_count_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "scanpy",
        SimpleNamespace(settings=SimpleNamespace(figdir=None)),
    )
    script = (
        Path(__file__).parents[2]
        / "src"
        / "omnicell_agent"
        / "recipes"
        / "pca_clustering"
        / "scripts"
        / "execute.py"
    )
    adata = SimpleNamespace(
        X=np.asarray([[1.0, 2.0], [50.0, 100.0]]),
        uns={
            "log1p": {},
            "omnicell_scientific_state": {
                "expression_space": "normalized_log1p",
            },
        },
    )

    with pytest.raises(
        RuntimeError,
        match="requires log-normalized expression",
    ):
        runpy.run_path(
            script,
            init_globals={
                "adata": adata,
                "artifact_output_root": str(tmp_path / "attempt"),
                "tool_parameters": {
                    "n_top_genes": 2_000,
                    "n_pcs": 40,
                    "n_neighbors": 10,
                    "resolution": 1.0,
                },
            },
        )


def test_internal_recipe_executes_with_registry_default_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalize_calls: list[float] = []
    fake_scanpy = SimpleNamespace(
        pp=SimpleNamespace(
            normalize_total=lambda adata, target_sum: normalize_calls.append(
                float(target_sum)
            ),
            log1p=lambda adata: adata.uns.__setitem__("log1p", {}),
        ),
    )
    monkeypatch.setitem(sys.modules, "scanpy", fake_scanpy)
    state = _analysis_state()
    state.update(
        {
            "plan_steps": [
                {
                    "step_type": "recipe_call",
                    "recipe_name": "normalize_log",
                    "instruction": "执行标准归一化",
                    "background_context": None,
                }
            ],
            "last_generated_code": "",
        }
    )
    state.update(programmer.run_programmer(state))
    session = ExecutingRecipeSession(_MinimalAdata())

    with executor.analysis_python_session_scope(session):  # type: ignore[arg-type]
        result = executor.run_executor(state)

    assert result["sandbox_execution_result"]["status"] == "success"
    assert normalize_calls == [10_000.0]
    assert session.globals["tool_parameters"] == {"target_sum": 10_000.0}
    assert session.globals["adata"].uns["log1p"] == {}
    assert session.ensured_directories == ["/app/data/attempt-00-00"]


def test_programmer_rejects_missing_authoritative_dataset_path() -> None:
    state = _analysis_state()
    del state["raw_data_path"]
    state["plan_steps"] = [
        {
            "step_type": "custom_code",
            "instruction": "检查数据",
            "background_context": "只读取当前输入",
        }
    ]

    with pytest.raises(ValueError, match="raw_data_path"):
        programmer.run_programmer(state)


def test_executor_rejects_missing_authoritative_output_path_without_running() -> None:
    state = _analysis_state()
    del state["marker_table_path"]
    state["last_generated_code"] = "print('must not run')"
    session = ControlledPythonSession()

    with executor.analysis_python_session_scope(session):  # type: ignore[arg-type]
        result = executor.run_executor(state)

    assert result["sandbox_execution_result"]["status"] == "error"
    assert "marker_table_path" in result["sandbox_execution_result"]["error"]
    assert session.executed_code == []


def test_marker_recipe_rejects_missing_cluster_without_hidden_preprocessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_scanpy = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "scanpy", fake_scanpy)
    script = (
        Path(__file__).parents[2]
        / "src"
        / "omnicell_agent"
        / "recipes"
        / "marker_genes_extractor"
        / "scripts"
        / "execute.py"
    )

    with pytest.raises(RuntimeError, match="requires existing leiden"):
        runpy.run_path(
            script,
            init_globals={
                "adata": SimpleNamespace(obs={}),
                "tool_parameters": {
                    "method": "wilcoxon",
                    "top_n_per_cluster": 50,
                    "adjusted_p_value_max": 0.05,
                    "min_log2_fold_change": 1.0,
                },
            },
        )


@pytest.mark.parametrize(
    ("eval_record", "current_index", "plan_count", "retries", "expected"),
    [
        ({"status": "success"}, 1, 1, 0, END),
        ({"status": "success"}, 1, 2, 0, "programmer"),
        ({"status": "error"}, 0, 1, 2, "programmer"),
        ({"status": "error"}, 0, 1, 3, END),
    ],
)
def test_exploratory_analysis_route_contract(
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

    assert analysis_engine.route_analysis_evaluation(state) == expected


def test_exploratory_analysis_controlled_end_to_end_contract(
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

    with executor.analysis_python_session_scope(session):  # type: ignore[arg-type]
        final_state = analysis_engine.build_exploratory_analysis_engine().invoke(
            _analysis_state()
        )

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
                "step_type": "recipe_call",
                "recipe_name": "normalize_log",
                "instruction": "执行标准归一化与对数变换",
                "background_context": None,
                "parameters": {"target_sum": 10_000.0},
            }
        ],
        "current_step_index": 1,
        "sandbox_execution_result": {
                "status": "success",
                "stdout": "controlled-runtime-ok",
                "stderr": "",
                "attempt_output_root": "/app/data/attempt-00-00",
                "attempt_marker_table_path": (
                    "/app/data/attempt-00-00/markers.json"
                ),
            },
        "eval_record": {"status": "success", "feedback": ""},
        "retry_count": 0,
        "failed_attempts": [],
    }
    assert controlled_llm_calls == ["ContextProfile", "AnalysisPlan"]
    assert "sc.pp.normalize_total" in final_state["last_generated_code"]
    assert session.executed_code[0] == (
        "raw_data_path = '/app/data/pbmc3k_raw.h5ad'\n"
        "marker_table_path = '/app/data/attempt-00-00/markers.json'\n"
        "artifact_output_root = '/app/data/attempt-00-00'\n"
        "tool_parameters = {'target_sum': 10000.0}\n"
    )
    assert len(session.executed_code) == 4
    assert session.start_calls == 1
    assert session.cleanup_calls == 1


def test_exploratory_analysis_error_projection_is_deterministic() -> None:
    state = _analysis_state()
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
