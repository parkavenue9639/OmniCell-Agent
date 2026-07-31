from __future__ import annotations

import ast
import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.atomic import build_atomic_capabilities
from omnicell_agent.capabilities.contracts import (
    ClusterCellsRequest,
    FindMarkerGenesRequest,
    NormalizeExpressionRequest,
    PlotPcaClustersRequest,
    QualityControlRequest,
)
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
        parameters_match = re.search(
            r"^tool_parameters = (.+)$",
            code,
            flags=re.MULTILINE,
        )
        source_match = re.search(
            r"^_atomic_source_artifact_id = (.+)$",
            code,
            flags=re.MULTILINE,
        )
        assert parameters_match is not None
        assert source_match is not None
        parameters = ast.literal_eval(parameters_match.group(1))
        source_artifact_id = ast.literal_eval(source_match.group(1))
        parameter_signature = hashlib.sha256(
            json.dumps(
                parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        def state(
            *,
            expression_space: str,
            expression_space_basis: str,
            has_pca: bool = False,
            has_neighbors: bool = False,
            has_leiden: bool = False,
            quality_control_signature: str | None = None,
            normalization_signature: str | None = None,
            pca_signature: str | None = None,
            clustering_signature: str | None = None,
        ) -> dict[str, object]:
            return {
                "n_obs": 8,
                "n_vars": 12,
                "expression_space": expression_space,
                "expression_space_basis": expression_space_basis,
                "has_pca": has_pca,
                "has_neighbors": has_neighbors,
                "has_leiden": has_leiden,
                "cluster_ids": ["0"] if has_leiden else [],
                "quality_control_signature": quality_control_signature,
                "normalization_signature": normalization_signature,
                "pca_signature": pca_signature,
                "clustering_signature": clustering_signature,
            }

        input_state = state(
            expression_space=(
                "unknown"
                if operation in {"quality_control", "normalize_expression"}
                else "normalized_log1p"
            ),
            expression_space_basis=(
                "none"
                if operation in {"quality_control", "normalize_expression"}
                else "anndata_log1p_metadata"
            ),
            has_pca=operation == "plot_pca_clusters",
            has_neighbors=operation == "plot_pca_clusters",
            has_leiden=operation
            in {"find_marker_genes", "plot_pca_clusters"},
        )
        output_state = dict(input_state)
        if operation == "quality_control":
            output_state.update(
                {
                    "n_obs": 6,
                    "n_vars": 10,
                    "quality_control_signature": parameter_signature,
                }
            )
        elif operation == "normalize_expression":
            output_state.update(
                {
                    "expression_space": "normalized_log1p",
                    "expression_space_basis": "omnicell_operation",
                    "normalization_signature": parameter_signature,
                }
            )
        elif operation == "cluster_cells":
            output_state.update(
                {
                    "has_pca": True,
                    "has_neighbors": True,
                    "has_leiden": True,
                    "cluster_ids": ["0"],
                    "pca_signature": parameter_signature,
                    "clustering_signature": parameter_signature,
                }
            )

        marker_selection = None
        if operation == "find_marker_genes":
            marker_count = 0 if self.empty_markers else len(_MARKERS)
            marker_selection = {
                "statistical_input": "adata.X",
                "method": parameters["method"],
                "adjusted_p_value_max": parameters["adjusted_p_value_max"],
                "min_log2_fold_change": parameters["min_log2_fold_change"],
                "top_n_per_cluster": parameters["top_n_per_cluster"],
                "all_clusters": ["0"],
                "tested_clusters": ["0"],
                "reported_clusters": [] if self.empty_markers else ["0"],
                "omitted_clusters": (
                    {"0": "no_threshold_hits"} if self.empty_markers else {}
                ),
                "selected_counts": (
                    {} if self.empty_markers else {"0": marker_count}
                ),
                "marker_count": marker_count,
                "thresholds_strict": True,
            }
        scientific_evidence = {
            "schema_version": 1,
            "evidence_level": "validated_observation",
            "operation": operation,
            "source_artifact_id": source_artifact_id,
            "disposition": "executed",
            "parameters": parameters,
            "random_seed": 0 if operation == "cluster_cells" else None,
            "input_state": input_state,
            "output_state": output_state,
            "validation_status": "verified",
            "validation_checks": ["controlled_postcondition_verified"],
        }
        if marker_selection is not None:
            scientific_evidence["marker_selection"] = marker_selection

        metrics = {
            "n_obs_before": 8,
            "n_obs_after": 6 if operation == "quality_control" else 8,
            "n_vars_before": 12,
            "n_vars_after": 10 if operation == "quality_control" else 12,
            "has_pca": operation
            in {"cluster_cells", "plot_pca_clusters"},
            "has_leiden": operation
            in {
                "cluster_cells",
                "find_marker_genes",
                "plot_pca_clusters",
            },
            "scientific_evidence": scientific_evidence,
        }
        (output_root / "summary.json").write_text(
            json.dumps(metrics),
            encoding="utf-8",
        )
        if operation in {
            "quality_control",
            "normalize_expression",
            "cluster_cells",
        }:
            (output_root / "dataset.h5ad").write_bytes(
                f"derived:{operation}".encode()
            )
        if operation == "find_marker_genes":
            markers = [] if self.empty_markers else _MARKERS
            (output_root / "markers.json").write_text(
                json.dumps(
                    {
                        "metadata": {"selection": marker_selection},
                        "markers": markers,
                    }
                ),
                encoding="utf-8",
            )
        if operation in {"cluster_cells", "plot_pca_clusters"}:
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


class _InProcessAtomicSession:
    """Execute the exact generated deterministic wrapper on a tiny real fixture."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def execute_code(self, code: str) -> dict[str, object]:
        translated = code.replace(
            "/app/data/",
            f"{self.workspace.as_posix()}/",
        )
        namespace: dict[str, Any] = {}
        try:
            exec(
                compile(translated, "<in-process-atomic-fixture>", "exec"),
                namespace,
                namespace,
            )
        except Exception as exc:
            return {
                "status": "error",
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
            }
        return {"status": "success", "stdout": "", "stderr": ""}


def _real_dataset_context(tmp_path: Path, adata: Any):
    import anndata as ad

    if hasattr(ad, "settings") and hasattr(
        ad.settings,
        "allow_write_nullable_strings",
    ):
        ad.settings.allow_write_nullable_strings = True
    conversation_id = uuid4()
    workspace = tmp_path / "real-conversation"
    base = ConversationArtifactStore(conversation_id, workspace)
    source_path = workspace / "uploads" / "source.h5ad"
    source_path.parent.mkdir(parents=True)
    adata.write_h5ad(source_path)
    source = base.publish(
        source_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    invocation = ConversationArtifactStore(
        conversation_id,
        workspace,
        invocation_id="c" * 32,
    )
    invocation.register_trusted(source)

    @contextmanager
    def scope(_: Path):
        yield _InProcessAtomicSession(workspace)

    return (
        source,
        CapabilityContext(
            conversation_id=conversation_id,
            artifacts=invocation,
        ),
        scope,
    )


@pytest.mark.parametrize(
    "operation",
    [
        "quality_control",
        "normalize_expression",
        "cluster_cells",
        "find_marker_genes",
        "plot_pca_clusters",
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

    request_models = {
        "quality_control": QualityControlRequest,
        "normalize_expression": NormalizeExpressionRequest,
        "cluster_cells": ClusterCellsRequest,
        "find_marker_genes": FindMarkerGenesRequest,
        "plot_pca_clusters": PlotPcaClustersRequest,
    }
    result = handlers[operation].invoke(request_models[operation](dataset=source), context)

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
        "quality_control",
        "normalize_expression",
        "cluster_cells",
    }:
        assert result.output_dataset is not None
        assert result.output_dataset.kind == "dataset"
    else:
        assert result.output_dataset is None
    if operation == "find_marker_genes":
        assert result.marker_table is not None
        assert result.marker_table.kind == "marker_table"
    if operation == "plot_pca_clusters":
        assert any(ref.kind == "image" for ref in result.artifacts)
    assert sessions and "sc.read_h5ad(raw_data_path)" in sessions[0].codes[0]
    compile(sessions[0].codes[0], f"<generated:{operation}>", "exec")
    assert result.scientific_evidence.validation_status == "verified"
    assert result.scientific_evidence.source_artifact_id == source.artifact_id
    for ref in result.artifacts:
        assert ref.metadata["scientific_validation"]["status"] == "verified"
        assert ref.metadata["operation_disposition"] == "executed"


def test_marker_recipe_keeps_requested_thresholds_as_a_hard_contract(
    tmp_path: Path,
) -> None:
    source, context = _context(tmp_path)
    sessions: list[_ControlledAtomicSession] = []
    handler = {
        item.spec.name: item
        for item in build_atomic_capabilities(
            scope_factory=_scope_factory(sessions)
        )
    }["find_marker_genes"]

    result = handler.invoke(
        FindMarkerGenesRequest(
            dataset=source,
            adjusted_p_value_max=0.02,
            min_log2_fold_change=1.5,
        ),
        context,
    )

    selection = result.scientific_evidence.marker_selection
    assert selection is not None
    assert selection.adjusted_p_value_max == 0.02
    assert selection.min_log2_fold_change == 1.5
    rendered = sessions[0].codes[0]
    assert 'use_raw=False' in rendered
    assert 'df.sort_values("pvals_adj").head' not in rendered
    assert "df[df[\"pvals_adj\"] < adjusted_p_value_max]" not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("p_val", float("nan")),
        ("p_val_adj", float("nan")),
        ("p_val_adj", 1.1),
        ("log2FC", float("nan")),
        ("pct_1", float("inf")),
        ("pct_2", -0.1),
    ],
)
def test_marker_contract_rejects_nonfinite_or_out_of_range_statistics(
    field: str,
    value: float,
) -> None:
    from omnicell_agent.schema.contract import MarkerGene

    payload = {
        "gene_name": "IL7R",
        "cluster_id": "0",
        "p_val": 0.001,
        "p_val_adj": 0.01,
        "log2FC": 2.0,
        "pct_1": 0.8,
        "pct_2": 0.1,
        field: value,
    }

    with pytest.raises(ValueError):
        MarkerGene(**payload)


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

    result = handlers["normalize_expression"].invoke(
        NormalizeExpressionRequest(dataset=source),
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
        handlers["find_marker_genes"].invoke(
            FindMarkerGenesRequest(dataset=source),
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

    handlers["plot_pca_clusters"].invoke(
        PlotPcaClustersRequest(dataset=source),
        context,
    )

    assert "ATOMIC_INPUT_ERROR" in sessions[0].codes[0]
    assert "input requires X_pca" in sessions[0].codes[0]
    assert "input requires leiden" in sessions[0].codes[0]


def test_real_normalization_fixture_verifies_target_sum_and_state(
    tmp_path: Path,
) -> None:
    import anndata as ad

    adata = ad.AnnData(
        np.asarray(
            [
                [4.0, 0.0, 2.0, 0.0],
                [0.0, 3.0, 0.0, 1.0],
                [2.0, 1.0, 1.0, 0.0],
                [0.0, 2.0, 3.0, 1.0],
            ]
        )
    )
    source, context, scope = _real_dataset_context(tmp_path, adata)
    handler = {
        item.spec.name: item
        for item in build_atomic_capabilities(scope_factory=scope)
    }["normalize_expression"]

    result = handler.invoke(
        NormalizeExpressionRequest(dataset=source, target_sum=100.0),
        context,
    )

    assert result.output_dataset is not None
    output = ad.read_h5ad(context.artifacts.resolve(result.output_dataset))
    restored_totals = np.expm1(np.asarray(output.X)).sum(axis=1)
    assert np.allclose(restored_totals, 100.0, rtol=5e-3, atol=1e-2)
    evidence = result.scientific_evidence
    assert evidence.disposition.value == "executed"
    assert evidence.input_state.expression_space == "unknown"
    assert evidence.output_state.expression_space == "normalized_log1p"
    assert "target_sum_verified" in evidence.validation_checks
    assert result.output_dataset.metadata["dataset_state"] == [
        "normalized_log1p"
    ]


def test_stale_log1p_metadata_cannot_reuse_raw_integer_counts(
    tmp_path: Path,
) -> None:
    import anndata as ad

    adata = ad.AnnData(
        np.asarray(
            [
                [1.0, 2.0, 3.0],
                [4.0, 5.0, 6.0],
                [2.0, 2.0, 2.0],
            ]
        )
    )
    adata.uns["log1p"] = {"base": None}
    source, context, scope = _real_dataset_context(tmp_path, adata)
    handler = {
        item.spec.name: item
        for item in build_atomic_capabilities(scope_factory=scope)
    }["normalize_expression"]

    result = handler.invoke(
        NormalizeExpressionRequest(dataset=source, target_sum=100.0),
        context,
    )

    assert result.output_dataset is not None
    assert result.scientific_evidence.disposition.value == "executed"
    assert result.scientific_evidence.input_state.expression_space == "unknown"
    output = ad.read_h5ad(context.artifacts.resolve(result.output_dataset))
    restored_totals = np.expm1(np.asarray(output.X)).sum(axis=1)
    assert np.allclose(restored_totals, 100.0, rtol=5e-3, atol=1e-2)


def test_real_marker_fixture_fails_closed_when_strict_threshold_has_no_hits(
    tmp_path: Path,
) -> None:
    import anndata as ad
    import pandas as pd
    import scanpy as sc

    matrix = np.asarray(
        [
            [8.0, 1.0, 0.0, 0.0],
            [7.0, 1.0, 0.0, 0.0],
            [9.0, 1.0, 0.0, 0.0],
            [8.0, 2.0, 0.0, 0.0],
            [0.0, 0.0, 8.0, 1.0],
            [0.0, 0.0, 7.0, 1.0],
            [0.0, 0.0, 9.0, 1.0],
            [0.0, 0.0, 8.0, 2.0],
        ]
    )
    adata = ad.AnnData(matrix)
    adata.var_names = ["T_MARKER", "T_SUPPORT", "B_MARKER", "B_SUPPORT"]
    adata.obs["leiden"] = pd.Categorical(["0"] * 4 + ["1"] * 4)
    sc.pp.normalize_total(adata, target_sum=100.0)
    sc.pp.log1p(adata)
    source, context, scope = _real_dataset_context(tmp_path, adata)
    handler = {
        item.spec.name: item
        for item in build_atomic_capabilities(scope_factory=scope)
    }["find_marker_genes"]

    with pytest.raises(CapabilityExecutionError, match="empty|为空"):
        handler.invoke(
            FindMarkerGenesRequest(
                dataset=source,
                method="wilcoxon",
                adjusted_p_value_max=0.05,
                min_log2_fold_change=100.0,
            ),
            context,
        )


def test_real_clustering_fixture_reuses_only_an_exact_operation_signature(
    tmp_path: Path,
) -> None:
    import anndata as ad
    import scanpy as sc

    rng = np.random.default_rng(7)
    matrix = rng.poisson(2.0, size=(30, 120)).astype(float)
    matrix[:15, :12] += rng.poisson(5.0, size=(15, 12))
    matrix[15:, 12:24] += rng.poisson(5.0, size=(15, 12))
    adata = ad.AnnData(matrix)
    adata.var_names = [f"gene_{index}" for index in range(120)]
    sc.pp.normalize_total(adata, target_sum=10_000.0)
    sc.pp.log1p(adata)
    source, context, scope = _real_dataset_context(tmp_path, adata)
    handler = {
        item.spec.name: item
        for item in build_atomic_capabilities(scope_factory=scope)
    }["cluster_cells"]
    request = ClusterCellsRequest(
        dataset=source,
        n_top_genes=100,
        n_pcs=5,
        n_neighbors=5,
        resolution=0.5,
    )

    first = handler.invoke(request, context)

    assert first.output_dataset is not None
    assert first.scientific_evidence.disposition.value == "executed"
    first_signature = first.scientific_evidence.output_state.clustering_signature
    assert first_signature

    second_store = ConversationArtifactStore(
        source.conversation_id,
        context.artifacts.workspace,
        invocation_id="d" * 32,
    )
    second_store.register_trusted(first.output_dataset)
    second_context = CapabilityContext(
        conversation_id=source.conversation_id,
        artifacts=second_store,
    )
    second = handler.invoke(
        request.model_copy(update={"dataset": first.output_dataset}),
        second_context,
    )

    assert second.output_dataset is not None
    assert second.scientific_evidence.disposition.value == "reused"
    assert (
        second.scientific_evidence.output_state.clustering_signature
        == first_signature
    )

    third_store = ConversationArtifactStore(
        source.conversation_id,
        context.artifacts.workspace,
        invocation_id="e" * 32,
    )
    third_store.register_trusted(second.output_dataset)
    third_context = CapabilityContext(
        conversation_id=source.conversation_id,
        artifacts=third_store,
    )
    changed = handler.invoke(
        request.model_copy(
            update={
                "dataset": second.output_dataset,
                "resolution": 1.0,
            }
        ),
        third_context,
    )

    assert changed.scientific_evidence.disposition.value == "executed"
    assert (
        changed.scientific_evidence.output_state.clustering_signature
        != first_signature
    )
