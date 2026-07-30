from __future__ import annotations

from uuid import uuid4

from omnicell_agent.agent.scientific_evidence import (
    deterministic_scientific_fallback,
    project_scientific_evidence,
    render_scientific_evidence_context,
    validate_scientific_final_response,
)


def _atomic_payload(*, disposition: str = "executed") -> dict:
    source_artifact_id = uuid4()
    state = {
        "n_obs": 30,
        "n_vars": 120,
        "expression_space": "normalized_log1p",
        "expression_space_basis": "omnicell_operation",
        "has_pca": True,
        "has_neighbors": True,
        "has_leiden": True,
        "cluster_ids": ["0", "1", "2"],
        "quality_control_signature": None,
        "normalization_signature": "a" * 64,
        "pca_signature": "b" * 64,
        "clustering_signature": "c" * 64,
    }
    return {
        "status": "completed",
        "scientific_evidence": {
            "schema_version": 1,
            "evidence_level": "validated_observation",
            "operation": "cluster_cells",
            "source_artifact_id": str(source_artifact_id),
            "disposition": disposition,
            "parameters": {"resolution": 1.0},
            "random_seed": 0,
            "input_state": state,
            "output_state": state,
            "validation_status": "verified",
            "validation_checks": ["cluster_labels_verified"],
            "marker_selection": None,
        },
    }


def test_atomic_projection_and_final_response_gate_reconcile_exact_facts() -> None:
    artifact_id = str(uuid4())
    evidence = project_scientific_evidence(
        "cluster_cells",
        _atomic_payload(disposition="reused"),
        [artifact_id],
    )

    assert evidence is not None
    assert evidence["disposition"] == "reused"
    assert validate_scientific_final_response(
        f"检测到 3 个 cluster，本次复用了聚类结果。产物 {artifact_id}。",
        [evidence],
    ) == []

    failures = validate_scientific_final_response(
        f"检测到 4 个 cluster，本次重新执行了聚类。产物 {uuid4()}。",
        [evidence],
    )
    assert failures == [
        "引用了不属于当前 Run 已验证结果的 artifact_id",
        "cluster_count 与当前 Run 已验证数量不一致",
        "cluster_cells 实际为 reused，不能声称本次重新执行",
    ]


def test_annotation_projection_keeps_method_boundary_and_manual_review() -> None:
    artifact_id = str(uuid4())
    evidence = project_scientific_evidence(
        "annotate_cell_clusters",
        {
            "status": "completed",
            "cluster_count": 3,
            "manual_review_count": 1,
            "marker_coverage_complete": False,
            "omitted_marker_cluster_count": 1,
            "cluster_summaries": [
                {
                    "cluster_id": "0",
                    "general_type": "T cell",
                    "sub_type": "CD4 T",
                    "confidence_score": 80,
                    "validator_status": "supported",
                    "requires_manual_review": False,
                    "flags": [],
                }
            ],
        },
        [artifact_id],
    )

    assert evidence is not None
    failures = validate_scientific_final_response(
        "3 个 cluster 全部验证通过、无需人工复核，所有 cluster 均已覆盖，"
        "cluster 0 已确认为 CD4 T。",
        [evidence],
    )
    assert failures == [
        "当前注释仍有必须人工复核的 cluster",
        "marker 覆盖不完整，不能声称全部覆盖",
        "细胞注释是方法约束下的推断，不能表述为已验证身份",
    ]


def test_unverified_or_unknown_capability_result_is_not_promoted() -> None:
    payload = _atomic_payload()
    payload["scientific_evidence"]["validation_status"] = "unchecked"

    assert (
        project_scientific_evidence(
            "cluster_cells",
            payload,
            [str(uuid4())],
        )
        is None
    )
    assert project_scientific_evidence("custom_tool", {}, []) is None


def test_deterministic_fallback_only_reports_bounded_verified_facts() -> None:
    artifact_id = str(uuid4())
    evidence = project_scientific_evidence(
        "cluster_cells",
        _atomic_payload(disposition="executed"),
        [artifact_id],
    )
    assert evidence is not None

    fallback = deterministic_scientific_fallback(
        [evidence],
        ["cluster_count 与当前 Run 已验证数量不一致"],
    )

    assert "cluster_cells：executed" in fallback
    assert "30 cells × 120 genes" in fallback
    assert artifact_id in fallback
    assert "模型候选未发布原因" in fallback


def test_transient_scientific_context_has_an_aggregate_size_boundary() -> None:
    evidence = [
        {
            "schema_version": 1,
            "kind": "cell_annotation",
            "evidence_level": "method_bounded_inference",
            "capability": "annotate_cell_clusters",
            "artifact_ids": [str(uuid4())],
            "cluster_count": 100,
            "manual_review_count": 100,
            "marker_coverage_complete": False,
            "omitted_marker_cluster_count": 100,
            "cluster_summaries": [
                {
                    "cluster_id": str(cluster),
                    "general_type": "Unknown" * 20,
                    "sub_type": "Unknown" * 20,
                    "confidence_score": 0,
                    "validator_status": "not_run",
                    "requires_manual_review": True,
                    "flags": ["needs_review" * 10],
                }
                for cluster in range(100)
            ],
        }
        for _ in range(32)
    ]

    rendered = render_scientific_evidence_context(evidence)

    assert len(rendered.encode("utf-8")) < 70 * 1024
    assert "method_bounded_inference" in rendered
