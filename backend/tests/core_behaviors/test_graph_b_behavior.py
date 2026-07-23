import json
from pathlib import Path
from typing import Any

import pytest

from omnicell_agent.annotation import graph as graph_b
from omnicell_agent.annotation.nodes import annotator
from omnicell_agent.annotation.nodes.scorer import scorer_node
from omnicell_agent.schema.contract import MarkerGene, MarkerTableContract
from omnicell_agent.schema.state import update_annotation_dict


def test_marker_contract_normalizes_graph_a_export_fields(tmp_path: Path) -> None:
    path = tmp_path / "markers.json"
    path.write_text(
        json.dumps(
            [
                {
                    "gene": "IL7R",
                    "cluster": "0",
                    "pvals": 0.001,
                    "pvals_adj": 0.01,
                    "logfoldchanges": 2.5,
                    "pct.1": 0.8,
                    "pct.2": 0.1,
                    "future_metric": 7.0,
                }
            ]
        ),
        encoding="utf-8",
    )

    contract = MarkerTableContract.load_from_json(path)

    assert contract.metadata == {}
    assert [marker.model_dump() for marker in contract.markers] == [
        {
            "gene_name": "IL7R",
            "cluster_id": "0",
            "p_val": 0.001,
            "p_val_adj": 0.01,
            "log2FC": 2.5,
            "pct_1": 0.8,
            "pct_2": 0.1,
            "score": None,
            "is_surface_protein": None,
            "future_metric": 7.0,
        }
    ]


def test_annotation_reducer_merges_without_mutating_existing() -> None:
    existing = {"0": {"sub_type": "CD4 T cells"}}
    incoming = {"1": {"sub_type": "B cells"}}

    merged = update_annotation_dict(existing, incoming)

    assert merged == {
        "0": {"sub_type": "CD4 T cells"},
        "1": {"sub_type": "B cells"},
    }
    assert existing == {"0": {"sub_type": "CD4 T cells"}}


def _marker(cluster_id: str, index: int) -> MarkerGene:
    prefix = "T" if cluster_id == "0" else "B"
    return MarkerGene(
        gene_name=f"{prefix}_MARKER_{index}",
        cluster_id=cluster_id,
        p_val=0.001 * (index + 1),
        p_val_adj=0.01 * (index + 1),
        log2FC=2.0 - (index * 0.1),
        pct_1=0.8,
        pct_2=0.1,
    )


def _write_contract(tmp_path) -> str:
    path = tmp_path / "markers.json"
    contract = MarkerTableContract(
        metadata={"baseline": "graph-b-controlled-v1"},
        markers=[_marker(cid, index) for cid in ("0", "1") for index in range(5)],
    )
    contract.save_to_json(path)
    return str(path)


def test_graph_b_distribution_contract(tmp_path) -> None:
    contract_path = _write_contract(tmp_path)

    sends = graph_b.distribute_clusters(
        {
            "contract_file_path": contract_path,
            "species": "Human",
            "tissue": "PBMC",
            "cluster_annotations": {},
            "final_report": "",
        }
    )

    projection = [
        {
            "node": send.node,
            "cluster_id": send.arg["cluster_id"],
            "species": send.arg["species"],
            "tissue": send.arg["tissue"],
            "top_n_markers": send.arg["top_n_markers"],
            "retry_count": send.arg["retry_count"],
        }
        for send in sends
    ]
    assert projection == [
        {
            "node": "process_cluster",
            "cluster_id": "0",
            "species": "Human",
            "tissue": "PBMC",
            "top_n_markers": [f"T_MARKER_{index}" for index in range(5)],
            "retry_count": 0,
        },
        {
            "node": "process_cluster",
            "cluster_id": "1",
            "species": "Human",
            "tissue": "PBMC",
            "top_n_markers": [f"B_MARKER_{index}" for index in range(5)],
            "retry_count": 0,
        },
    ]


@pytest.mark.parametrize(
    ("score", "retry_count", "boost_enabled", "expected"),
    [
        (75.0, 0, True, "end"),
        (74.9, 0, True, "boost"),
        (50.0, 1, True, "end"),
        (50.0, 0, False, "end"),
        ("invalid", 0, True, "boost"),
    ],
)
def test_graph_b_post_scorer_route_contract(
    monkeypatch: pytest.MonkeyPatch,
    score: Any,
    retry_count: int,
    boost_enabled: bool,
    expected: str,
) -> None:
    monkeypatch.setattr(graph_b, "ENABLE_BOOST", boost_enabled)
    state = {"quality_scores": {"cs_score": score}, "retry_count": retry_count}

    assert graph_b.post_scorer_route(state) == expected


@pytest.mark.parametrize(
    ("sub_type", "marker_count", "penalty", "self_ok", "expected"),
    [
        ("Unknown", 5, 0, 1.0, 0.0),
        ("CD4 T cells", 5, 10, 1.0, 90.0),
        ("CD4 T cells", 5, 10, 0.0, 75.0),
        ("CD4 T cells", 4, 10, 1.0, 80.0),
        ("CD4 T cells", 2, 90, 1.0, 0.0),
    ],
)
def test_graph_b_score_contract(
    sub_type: str,
    marker_count: int,
    penalty: int,
    self_ok: float,
    expected: float,
) -> None:
    result = scorer_node(
        {
            "cluster_id": "0",
            "top_n_markers": [f"G{i}" for i in range(marker_count)],
            "predictions": {"sub_type": sub_type},
            "quality_scores": {
                "validator_penalty": penalty,
                "self_consistency_ok": self_ok,
            },
        }
    )

    assert result["quality_scores"]["cs_score"] == expected


def test_graph_b_result_projection_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class ControlledClusterApp:
        def invoke(self, state):
            return {
                **state,
                "cluster_id": "7",
                "predictions": {
                    "general_type": "Immune cells",
                    "sub_type": "Rare cells (Boosted)",
                },
                "quality_scores": {
                    "cs_score": 50.0,
                    "self_consistency_ok": 0.0,
                },
                "retry_count": 1,
            }

    monkeypatch.setattr(graph_b, "single_cluster_app", ControlledClusterApp())

    assert graph_b.process_cluster_wrapper({"cluster_id": "7"}) == {
        "cluster_annotations": {
            "7": {
                "general_type": "Immune cells",
                "sub_type": "Rare cells (Boosted) (NeedsReview)",
                "cs_score": 50.0,
                "self_consistency_ok": 0.0,
                "flags": ["low_self_consistency", "boosted", "needs_review"],
            }
        }
    }


def test_graph_b_controlled_end_to_end_contract(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    controlled_llm_calls: list[str],
) -> None:
    from collections import Counter

    monkeypatch.setattr(annotator, "ENABLE_SELF_CONSISTENCY", True)
    monkeypatch.setattr(graph_b, "ENABLE_BOOST", True)
    monkeypatch.setattr(graph_b, "ENABLE_CONSISTENCY_REVIEWER", True)
    contract_path = _write_contract(tmp_path)

    final_state = graph_b.build_annotation_graph().invoke(
        {
            "contract_file_path": contract_path,
            "species": "Human",
            "tissue": "PBMC",
            "cluster_annotations": {},
            "final_report": "",
        }
    )

    assert final_state["cluster_annotations"] == {
        "0": {
            "general_type": "Immune cells",
            "sub_type": "CD4 T cells",
            "reasoning_chain": "Controlled reasoning for CD4 T cells",
            "marker_evidence": ["IL7R -> CD4 T cell lineage"],
            "cs_score": 95.0,
            "self_consistency_ok": 1.0,
            "flags": [],
        },
        "1": {
            "general_type": "Immune cells",
            "sub_type": "B cells",
            "reasoning_chain": "Controlled reasoning for B cells",
            "marker_evidence": ["MS4A1 -> B cell lineage"],
            "cs_score": 95.0,
            "self_consistency_ok": 1.0,
            "flags": [],
        },
    }
    assert final_state["final_report"] == "\n".join(
        [
            "# OmniCell-Agent 深度共识细胞鉴定报告 (Deep Annotation Report)",
            "\n**Species**: `Human` | **Tissue**: `PBMC`",
            "**Total Clusters Authenticated**: `2`",
            "\n| Cluster ID | General Lineage | Specific Sub-Type | CS Score | Flags | Validated Evidence |",
            "| :---: | :--- | :--- | :---: | :--- | :--- |",
            "| 0 | Immune cells | **CD4 T cells** | 95.0 | — | ✅ Verified |",
            "| 1 | Immune cells | **B cells** | 95.0 | — | ✅ Verified |",
            "\n## 需人工复核清单 (Manual review queue)\n",
            "_No clusters flagged for mandatory review._",
        ]
    )
    assert Counter(controlled_llm_calls) == Counter(
        {"AnnotationOutput": 6, "ValidatorOutput": 2}
    )
