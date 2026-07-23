from __future__ import annotations

import ast
import json
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.atomic import build_atomic_capabilities
from omnicell_agent.capabilities.contracts import AtomicAnalysisRequest
from omnicell_agent.capabilities.errors import CapabilityExecutionError
from omnicell_agent.capabilities.registry import CapabilityContext


_MARKERS = [
    {
        "cluster": "0",
        "gene_name": "IL7R",
        "pvals": 0.001,
        "pvals_adj": 0.01,
        "logfoldchanges": 2.0,
        "pct.1": 0.8,
        "pct.2": 0.1,
    }
]


class _ControlledAtomicSession:
    def __init__(
        self,
        workspace: Path,
        *,
        empty_markers: bool = False,
    ) -> None:
        self.workspace = workspace
        self.empty_markers = empty_markers
        self.codes: list[str] = []

    def execute_code(self, code: str) -> dict[str, object]:
        self.codes.append(code)
        match = re.search(
            r"^artifact_output_root = (.+)$",
            code,
            flags=re.MULTILINE,
        )
        assert match is not None
        sandbox_root = ast.literal_eval(match.group(1))
        relative_root = str(sandbox_root).removeprefix("/app/data/")
        output_root = self.workspace / relative_root
        output_root.mkdir(parents=True, exist_ok=True)
        operation = output_root.parent.name

        metrics = {
            "n_obs_before": 8,
            "n_obs_after": 6 if operation == "run_qc_and_filter" else 8,
            "n_vars_before": 12,
            "n_vars_after": 10 if operation == "run_qc_and_filter" else 12,
            "has_pca": operation
            in {"run_pca_clustering", "generate_pca_scatter"},
            "has_leiden": operation
            in {
                "run_pca_clustering",
                "extract_marker_genes",
                "generate_pca_scatter",
            },
        }
        (output_root / "summary.json").write_text(
            json.dumps(metrics),
            encoding="utf-8",
        )
        if operation in {
            "run_qc_and_filter",
            "run_normalize_log",
            "run_pca_clustering",
        }:
            (output_root / "dataset.h5ad").write_bytes(
                f"derived:{operation}".encode()
            )
        if operation == "extract_marker_genes":
            markers = [] if self.empty_markers else _MARKERS
            (output_root / "markers.json").write_text(
                json.dumps(markers),
                encoding="utf-8",
            )
        if operation in {"run_pca_clustering", "generate_pca_scatter"}:
            (output_root / "plot.png").write_bytes(b"png")
        return {
            "status": "success",
            "stdout": "controlled",
            "stderr": "",
        }


def _scope_factory(
    sessions: list[_ControlledAtomicSession],
    *,
    empty_markers: bool = False,
):
    @contextmanager
    def scope(workspace: Path):
        session = _ControlledAtomicSession(
            workspace,
            empty_markers=empty_markers,
        )
        sessions.append(session)
        yield session

    return scope


def _context(tmp_path: Path):
    conversation_id = uuid4()
    workspace = tmp_path / "conversation"
    base = ConversationArtifactStore(conversation_id, workspace)
    source = base.write_bytes(
        "uploads/source.h5ad",
        b"source-dataset",
        kind="dataset",
        media_type="application/x-hdf5",
    )
    invocation = ConversationArtifactStore(
        conversation_id,
        workspace,
        invocation_id="a" * 32,
    )
    invocation.register_trusted(source)
    return (
        source,
        CapabilityContext(
            conversation_id=conversation_id,
            artifacts=invocation,
        ),
    )


@pytest.mark.parametrize(
    "operation",
    [
        "run_qc_and_filter",
        "run_normalize_log",
        "run_pca_clustering",
        "extract_marker_genes",
        "generate_pca_scatter",
    ],
)
def test_atomic_capability_publishes_bounded_typed_artifacts(
    tmp_path: Path,
    operation: str,
) -> None:
    source, context = _context(tmp_path)
    sessions: list[_ControlledAtomicSession] = []
    handlers = {
        handler.spec.name: handler
        for handler in build_atomic_capabilities(
            scope_factory=_scope_factory(sessions)
        )
    }

    result = handlers[operation].invoke(
        AtomicAnalysisRequest(dataset=source),
        context,
    )

    assert result.operation == operation
    assert result.source_dataset == source
    assert context.artifacts.resolve(source).read_bytes() == b"source-dataset"
    assert all(
        ref.uri.startswith(
            f"workspace://{context.artifacts.output_scope}/artifacts/atomic/"
        )
        for ref in result.artifacts
    )
    assert all(
        ref.metadata["source_artifact_id"] == str(source.artifact_id)
        for ref in result.artifacts
    )
    if operation in {
        "run_qc_and_filter",
        "run_normalize_log",
        "run_pca_clustering",
    }:
        assert result.output_dataset is not None
        assert result.output_dataset.kind == "dataset"
    else:
        assert result.output_dataset is None
    if operation == "extract_marker_genes":
        assert result.marker_table is not None
        assert result.marker_table.kind == "marker_table"
    if operation == "generate_pca_scatter":
        assert any(ref.kind == "image" for ref in result.artifacts)
    assert sessions and "sc.read_h5ad(raw_data_path)" in sessions[0].codes[0]


def test_atomic_dataset_output_can_be_hydrated_by_next_invocation(
    tmp_path: Path,
) -> None:
    source, context = _context(tmp_path)
    handlers = {
        handler.spec.name: handler
        for handler in build_atomic_capabilities(
            scope_factory=_scope_factory([])
        )
    }

    result = handlers["run_normalize_log"].invoke(
        AtomicAnalysisRequest(dataset=source),
        context,
    )
    assert result.output_dataset is not None
    next_store = ConversationArtifactStore(
        source.conversation_id,
        context.artifacts.workspace,
        invocation_id="b" * 32,
    )
    next_store.register_trusted(result.output_dataset)

    assert next_store.sandbox_path(
        result.output_dataset,
        expected_kind="dataset",
    ).startswith("/app/data/.omnicell-invocations/")


def test_atomic_marker_tool_rejects_empty_contract(tmp_path: Path) -> None:
    source, context = _context(tmp_path)
    handlers = {
        handler.spec.name: handler
        for handler in build_atomic_capabilities(
            scope_factory=_scope_factory([], empty_markers=True)
        )
    }

    with pytest.raises(CapabilityExecutionError, match="为空"):
        handlers["extract_marker_genes"].invoke(
            AtomicAnalysisRequest(dataset=source),
            context,
        )


def test_pca_scatter_adapter_enforces_explicit_scientific_precondition(
    tmp_path: Path,
) -> None:
    source, context = _context(tmp_path)
    sessions: list[_ControlledAtomicSession] = []
    handlers = {
        handler.spec.name: handler
        for handler in build_atomic_capabilities(
            scope_factory=_scope_factory(sessions)
        )
    }

    handlers["generate_pca_scatter"].invoke(
        AtomicAnalysisRequest(dataset=source),
        context,
    )

    assert "ATOMIC_INPUT_ERROR" in sessions[0].codes[0]
    assert "requires X_pca and leiden" in sessions[0].codes[0]
