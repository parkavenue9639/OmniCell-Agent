from __future__ import annotations

import io
import json
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import h5py
import numpy as np

from omnicell_agent.capabilities.artifacts import ConversationArtifactStore
from omnicell_agent.capabilities.exploratory_evidence import (
    build_exploratory_result_manifest,
)
from omnicell_agent.capabilities.contracts import (
    ExploratoryAnalysisRequest,
)
from omnicell_agent.capabilities.exploratory_analysis import (
    ExploratoryAnalysisCapability,
)
from omnicell_agent.capabilities.registry import CapabilityContext
from omnicell_agent.schema.contract import MarkerGene, MarkerTableContract


def _marker_selection(
    clusters: list[str],
    selected_counts: dict[str, int],
) -> dict:
    return {
        "statistical_input": "adata.X",
        "method": "wilcoxon",
        "adjusted_p_value_max": 0.05,
        "min_log2_fold_change": 1.0,
        "top_n_per_cluster": 50,
        "all_clusters": clusters,
        "tested_clusters": clusters,
        "reported_clusters": clusters,
        "omitted_clusters": {},
        "selected_counts": selected_counts,
        "marker_count": sum(selected_counts.values()),
        "thresholds_strict": True,
    }


def test_exploratory_manifest_separates_scientific_and_structural_facts(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    contract = MarkerTableContract(
        metadata={
            "selection": _marker_selection(
                ["0", "1"],
                {"0": 1, "1": 1},
            )
        },
        markers=[
            MarkerGene(
                gene_name="CD3D",
                cluster_id="0",
                p_val=0.001,
                p_val_adj=0.01,
                log2FC=2.0,
                pct_1=0.8,
                pct_2=0.2,
            ),
            MarkerGene(
                gene_name="MS4A1",
                cluster_id="1",
                p_val=0.001,
                p_val_adj=0.01,
                log2FC=2.5,
                pct_1=0.7,
                pct_2=0.1,
            ),
        ]
    )
    marker_ref = store.write_json(
        "results/markers.json",
        contract.model_dump(mode="json"),
        kind="marker_table",
    )
    image_bytes = io.BytesIO()
    Image.new("RGB", (32, 24), color="white").save(
        image_bytes,
        format="PNG",
    )
    image_ref = store.write_bytes(
        "results/plot.png",
        image_bytes.getvalue(),
        kind="image",
        media_type="image/png",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [marker_ref, image_ref],
        marker_contracts={str(marker_ref.artifact_id): contract},
        acceptance_criterion="marker_table",
    )

    assert manifest.scientific_goal_status == "validated"
    assert manifest.authoritative_fact_count == 3
    assert [item.verification_level for item in manifest.items] == [
        "scientific",
        "structural",
    ]
    assert manifest.items[0].facts == {
        "marker_count": 2,
        "cluster_count": 2,
        "cluster_ids": ["0", "1"],
    }
    assert manifest.items[1].facts == {
        "width": 32,
        "height": 24,
        "format": "PNG",
    }
    assert manifest.limitations == [
        "图像、通用表格和文本只验证可读性与结构，不验证其科学解释。"
    ]


def test_marker_rows_violating_selection_are_not_scientific_evidence(
    tmp_path,
) -> None:
    store = ConversationArtifactStore(uuid4(), tmp_path)
    contract = MarkerTableContract(
        metadata={
            "selection": _marker_selection(["0"], {"0": 1}),
        },
        markers=[
            MarkerGene(
                gene_name="CD3D",
                cluster_id="0",
                p_val=0.001,
                p_val_adj=0.9,
                log2FC=0.1,
                pct_1=0.8,
                pct_2=0.2,
            )
        ],
    )
    marker_ref = store.write_json(
        "results/invalid-markers.json",
        contract.model_dump(mode="json"),
        kind="marker_table",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [marker_ref],
        marker_contracts={str(marker_ref.artifact_id): contract},
        acceptance_criterion="marker_table",
    )

    assert manifest.scientific_goal_status == "partial"
    assert manifest.authoritative_fact_count == 0
    assert manifest.items[0].verification_level == "structural"
    assert "marker_selection_evidence_invalid" in manifest.items[0].checks
    assert "goal_acceptance_failed" in manifest.acceptance_checks


def test_exploratory_manifest_keeps_unknown_files_as_non_authoritative_drafts(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    draft_ref = store.write_bytes(
        "results/custom.bin",
        b"untyped-result",
        kind="file",
        media_type="application/octet-stream",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [draft_ref],
        marker_contracts={},
        acceptance_criterion="other",
    )

    assert manifest.scientific_goal_status == "partial"
    assert manifest.authoritative_fact_count == 0
    assert manifest.items[0].verification_level == "unverified"
    assert "只能作为草稿" in manifest.limitations[-1]


def test_generic_json_key_projection_stays_within_manifest_boundary(
    tmp_path,
) -> None:
    store = ConversationArtifactStore(uuid4(), tmp_path)
    payload = {
        f"{index:03d}-{'x' * 400}": index
        for index in range(100)
    }
    ref = store.write_json(
        "results/wide.json",
        payload,
        kind="json",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [ref],
        marker_contracts={},
        acceptance_criterion="other",
    )

    evidence = manifest.items[0]
    assert evidence.verification_level == "structural"
    assert len(evidence.facts["top_level_keys"]) == 100
    assert all(
        len(key) <= 96
        for key in evidence.facts["top_level_keys"]
    )


def test_publish_new_files_filters_failed_attempt_directories(tmp_path) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    before = store.snapshot_files()
    successful = tmp_path / "outputs" / "attempt-00-01"
    failed = tmp_path / "outputs" / "attempt-00-00"
    successful.mkdir(parents=True)
    failed.mkdir(parents=True)
    (successful / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (failed / "stale.json").write_text('{"stale": true}', encoding="utf-8")

    refs = store.publish_new_files(
        before,
        allowed_relative_prefixes=("outputs/attempt-00-01",),
    )

    assert [ref.uri for ref in refs] == [
        "workspace://outputs/attempt-00-01/result.json"
    ]


def test_exploratory_capability_only_publishes_successful_attempt_outputs(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    dataset = store.write_bytes(
        "inputs/data.h5ad",
        b"controlled-input",
        kind="dataset",
        media_type="application/x-hdf5",
    )
    marker_payload = {
        "metadata": {
            "selection": _marker_selection(["0"], {"0": 1}),
        },
        "markers": [
            {
                "gene_name": "CD3D",
                "cluster_id": "0",
                "p_val": 0.001,
                "p_val_adj": 0.01,
                "log2FC": 2.0,
                "pct_1": 0.8,
                "pct_2": 0.2,
            }
        ],
    }

    def invoke_graph(initial_state):
        base = initial_state["marker_table_path"].rsplit("/", 1)[0]
        relative_base = base.removeprefix("/app/data/")
        failed_root = tmp_path / relative_base / "attempt-00-00"
        successful_root = tmp_path / relative_base / "attempt-00-01"
        failed_root.mkdir(parents=True)
        successful_root.mkdir(parents=True)
        (failed_root / "stale.json").write_text(
            '{"stale": true}',
            encoding="utf-8",
        )
        (successful_root / "markers.json").write_text(
            json.dumps(marker_payload),
            encoding="utf-8",
        )
        return {
            **initial_state,
            "plan_steps": [
                {
                    "step_type": "custom_code",
                    "instruction": "受控 marker 分析",
                }
            ],
            "current_step_index": 1,
            "task_context": {
                "resolved_context": {
                    "species": "Human",
                    "tissue": "PBMC",
                    "disease_state": "Healthy",
                    "goal_type": "marker_analysis",
                },
                "eval_record": {"status": "success", "feedback": ""},
                "retry_count": 0,
                "successful_output_roots": [f"{base}/attempt-00-01"],
                "successful_marker_table_paths": [
                    f"{base}/attempt-00-01/markers.json"
                ],
                "failed_output_roots": [f"{base}/attempt-00-00"],
            },
        }

    capability = ExploratoryAnalysisCapability(
        graph_factory=lambda: SimpleNamespace(invoke=invoke_graph),
        scope_factory=lambda _workspace: nullcontext(),
    )
    result = capability.invoke(
        ExploratoryAnalysisRequest(
            dataset=dataset,
            goal="提取当前数据的受控 marker table",
            acceptance_criterion="marker_table",
        ),
        CapabilityContext(
            conversation_id=conversation_id,
            artifacts=store,
        ),
    )

    assert result.status.value == "completed"
    assert result.marker_table is not None
    assert result.result_manifest.scientific_goal_status == "validated"
    assert result.result_manifest.authoritative_fact_count == 3
    assert all("stale.json" not in ref.uri for ref in result.artifacts)


def test_cluster_summary_counts_and_proportions_reconcile_to_source_dataset(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    dataset_path = tmp_path / "source.h5ad"
    with h5py.File(dataset_path, "w") as handle:
        handle.create_dataset("X", data=np.zeros((10, 5), dtype=np.float32))
    dataset = store.publish(
        dataset_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    summary = store.write_text(
        "results/clusters.csv",
        "cluster,count,proportion\n0,4,0.4\n1,6,0.6\n",
        kind="file",
        media_type="text/csv",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [summary],
        marker_contracts={},
        source_dataset=dataset,
        acceptance_criterion="cluster_summary",
    )

    assert manifest.scientific_goal_status == "validated"
    assert manifest.items[0].verification_level == "scientific"
    assert manifest.items[0].facts == {
        "cluster_count": 2,
        "cluster_ids": ["0", "1"],
        "cluster_cell_counts": {"0": 4, "1": 6},
        "total_cell_count": 10,
        "source_n_obs": 10,
        "proportion_sum": 1.0,
        "cluster_proportions": {"0": 0.4, "1": 0.6},
    }
    assert "cluster_proportions_verified" in manifest.items[0].checks


def test_cluster_summary_mismatch_is_not_promoted_to_scientific_evidence(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    dataset_path = tmp_path / "source.h5ad"
    with h5py.File(dataset_path, "w") as handle:
        handle.create_dataset("X", data=np.zeros((10, 5), dtype=np.float32))
    dataset = store.publish(
        dataset_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    invalid = store.write_text(
        "results/invalid-clusters.csv",
        "cluster,count,proportion\n0,4,0.5\n1,5,0.5\n",
        kind="file",
        media_type="text/csv",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [invalid],
        marker_contracts={},
        source_dataset=dataset,
        acceptance_criterion="cluster_summary",
    )

    assert manifest.scientific_goal_status == "partial"
    assert manifest.authoritative_fact_count == 0
    assert manifest.items[0].verification_level == "unverified"
    assert "cluster_summary_invariant_failed" in manifest.items[0].checks


def test_unrelated_dataset_shape_does_not_complete_marker_goal(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    output_path = tmp_path / "output.h5ad"
    with h5py.File(output_path, "w") as handle:
        handle.create_dataset("X", data=np.zeros((10, 5), dtype=np.float32))
    output = store.publish(
        output_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [output],
        marker_contracts={},
        acceptance_criterion="marker_table",
    )

    assert manifest.authoritative_fact_count == 2
    assert manifest.scientific_goal_status == "partial"
    assert "goal_acceptance_failed" in manifest.acceptance_checks


def test_cross_artifact_cluster_counts_and_proportions_must_match(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    dataset_path = tmp_path / "source.h5ad"
    with h5py.File(dataset_path, "w") as handle:
        handle.create_dataset("X", data=np.zeros((10, 5), dtype=np.float32))
    dataset = store.publish(
        dataset_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    first = store.write_text(
        "results/clusters-a.csv",
        "cluster,count,proportion\n0,4,0.4\n1,6,0.6\n",
        kind="file",
        media_type="text/csv",
    )
    second = store.write_text(
        "results/clusters-b.csv",
        "cluster,count,proportion\n0,6,0.6\n1,4,0.4\n",
        kind="file",
        media_type="text/csv",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [first, second],
        marker_contracts={},
        source_dataset=dataset,
        acceptance_criterion="cluster_summary",
    )

    assert manifest.scientific_goal_status == "partial"
    assert manifest.authoritative_fact_count == 0
    assert all(
        item.verification_level == "unverified"
        and "cross_artifact_cluster_projection_mismatch" in item.checks
        for item in manifest.items
    )


def test_marker_and_cluster_table_cluster_projection_is_reconciled(
    tmp_path,
) -> None:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    dataset_path = tmp_path / "source.h5ad"
    with h5py.File(dataset_path, "w") as handle:
        handle.create_dataset("X", data=np.zeros((10, 5), dtype=np.float32))
    dataset = store.publish(
        dataset_path,
        kind="dataset",
        media_type="application/x-hdf5",
    )
    contract = MarkerTableContract(
        metadata={
            "selection": _marker_selection(
                ["0", "1"],
                {"0": 1, "1": 1},
            )
        },
        markers=[
            MarkerGene(
                gene_name="CD3D",
                cluster_id="0",
                p_val=0.001,
                p_val_adj=0.01,
                log2FC=2.0,
                pct_1=0.8,
                pct_2=0.2,
            ),
            MarkerGene(
                gene_name="MS4A1",
                cluster_id="1",
                p_val=0.001,
                p_val_adj=0.01,
                log2FC=2.0,
                pct_1=0.8,
                pct_2=0.2,
            ),
        ],
    )
    marker = store.write_json(
        "results/markers.json",
        contract.model_dump(mode="json"),
        kind="marker_table",
    )
    summary = store.write_text(
        "results/clusters.csv",
        "cluster,count,proportion\n0,4,0.4\n1,6,0.6\n",
        kind="file",
        media_type="text/csv",
    )

    manifest = build_exploratory_result_manifest(
        store,
        [marker, summary],
        marker_contracts={str(marker.artifact_id): contract},
        source_dataset=dataset,
        acceptance_criterion="cluster_summary",
    )

    assert manifest.scientific_goal_status == "validated"
    assert all(
        "cross_artifact_cluster_projection_verified" in item.checks
        for item in manifest.items
    )
