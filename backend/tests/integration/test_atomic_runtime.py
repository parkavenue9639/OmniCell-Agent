from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import anndata as ad
import numpy as np
import pandas as pd
import pytest

from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.atomic import build_atomic_capabilities
from omnicell_agent.capabilities.contracts import (
    ArtifactRef,
    AtomicAnalysisRequest,
)
from omnicell_agent.capabilities.registry import CapabilityContext


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OMNICELL_RUN_DOCKER_TESTS") != "1",
        reason="设置 OMNICELL_RUN_DOCKER_TESTS=1 后运行真实原子 Tool 集成测试",
    ),
]


def _synthetic_dataset(path: Path) -> None:
    ad.settings.allow_write_nullable_strings = True
    rng = np.random.default_rng(7)
    matrix = rng.poisson(0.05, size=(80, 500)).astype(np.float32)
    matrix[:40, :20] += rng.poisson(1.5, size=(40, 20))
    matrix[40:, 20:40] += rng.poisson(1.5, size=(40, 20))
    dataset = ad.AnnData(
        matrix,
        obs=pd.DataFrame(index=[f"cell-{index}" for index in range(80)]),
        var=pd.DataFrame(index=[f"GENE{index}" for index in range(500)]),
    )
    dataset.write_h5ad(path)


def _invoke(
    *,
    operation: str,
    source: ArtifactRef,
    workspace: Path,
    invocation_id: str,
    control_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "OMNICELL_CAPABILITY_INVOCATION_ID",
        invocation_id,
    )
    monkeypatch.setenv(
        "OMNICELL_RUNTIME_OWNERSHIP_FILE",
        str(control_root / f"{invocation_id}.json"),
    )
    store = ConversationArtifactStore(
        source.conversation_id,
        workspace,
        invocation_id=invocation_id,
    )
    store.register_trusted(source)
    handlers = {
        handler.spec.name: handler
        for handler in build_atomic_capabilities()
    }
    return handlers[operation].invoke(
        AtomicAnalysisRequest(dataset=source),
        CapabilityContext(
            conversation_id=source.conversation_id,
            artifacts=store,
        ),
    )


def test_real_atomic_dataset_chain_across_isolated_invocations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    workspace = tmp_path / "conversation"
    base = ConversationArtifactStore(conversation_id, workspace)
    input_path = workspace / "uploads" / "synthetic.h5ad"
    input_path.parent.mkdir(parents=True)
    _synthetic_dataset(input_path)
    source = base.publish(
        input_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    control_root = tmp_path / "runtime-control"
    control_root.mkdir()

    normalized = _invoke(
        operation="run_normalize_log",
        source=source,
        workspace=workspace,
        invocation_id="1" * 32,
        control_root=control_root,
        monkeypatch=monkeypatch,
    )
    assert normalized.output_dataset is not None

    clustered = _invoke(
        operation="run_pca_clustering",
        source=normalized.output_dataset,
        workspace=workspace,
        invocation_id="2" * 32,
        control_root=control_root,
        monkeypatch=monkeypatch,
    )
    assert clustered.output_dataset is not None
    assert clustered.metrics["has_pca"] is True
    assert clustered.metrics["has_leiden"] is True
    assert int(clustered.metrics["cluster_count"]) > 0
    assert any(ref.kind == "image" for ref in clustered.artifacts)

    markers = _invoke(
        operation="extract_marker_genes",
        source=clustered.output_dataset,
        workspace=workspace,
        invocation_id="3" * 32,
        control_root=control_root,
        monkeypatch=monkeypatch,
    )
    assert markers.marker_table is not None
    assert int(markers.metrics["marker_count"]) > 0
    assert list(control_root.iterdir()) == []
