from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from omnicell_agent.annotation import graph as graph_b
from omnicell_agent.capabilities.artifacts import (
    ArtifactBoundaryError,
    ConversationArtifactStore,
)
from omnicell_agent.capabilities.contracts import (
    DeepCellAnnotationRequest,
    InspectMarkerContractRequest,
)
from omnicell_agent.capabilities.graph_b import (
    DeepCellAnnotationCapability,
    InspectMarkerContractCapability,
)
from omnicell_agent.capabilities.errors import (
    CapabilityExecutionError,
    CapabilityInputError,
)
from omnicell_agent.capabilities.registry import CapabilityContext
from omnicell_agent.schema.contract import MarkerGene, MarkerTableContract


def _marker(cluster_id: str, gene_name: str, p_val_adj: float) -> MarkerGene:
    return MarkerGene(
        gene_name=gene_name,
        cluster_id=cluster_id,
        p_val=0.001,
        p_val_adj=p_val_adj,
        log2FC=2.0,
        pct_1=0.8,
        pct_2=0.1,
    )


def _artifact_context(tmp_path: Path) -> tuple[CapabilityContext, ConversationArtifactStore]:
    conversation_id = uuid4()
    store = ConversationArtifactStore(conversation_id, tmp_path)
    return CapabilityContext(conversation_id, store), store


def _publish_contract(
    store: ConversationArtifactStore,
    markers: list[MarkerGene],
):
    path = store.workspace / "inputs" / "markers.json"
    MarkerTableContract(
        metadata={"baseline": "graph-b-capability-v1"},
        markers=markers,
    ).save_to_json(path)
    return store.publish(
        path,
        kind="marker_table",
        media_type="application/json",
    )


def test_inspection_sorts_markers_and_reports_bounded_truncation(tmp_path: Path) -> None:
    context, store = _artifact_context(tmp_path / "conversation")
    marker_ref = _publish_contract(
        store,
        [
            _marker("b", "B3", 0.3),
            _marker("b", "B1", 0.1),
            _marker("b", "B2", 0.2),
            _marker("a", "A2", 0.2),
            _marker("a", "A1", 0.1),
        ],
    )

    result = InspectMarkerContractCapability().invoke(
        InspectMarkerContractRequest(
            marker_table=marker_ref,
            top_markers_per_cluster=2,
            max_clusters=1,
        ),
        context,
    )

    assert result.source_marker_table == marker_ref
    assert result.marker_count == 5
    assert result.cluster_count == 2
    assert result.truncated is True
    assert [summary.model_dump() for summary in result.clusters] == [
        {
            "cluster_id": "b",
            "marker_count": 3,
            "top_markers": ["B1", "B2"],
        }
    ]


def test_capabilities_reject_foreign_conversation_artifact(tmp_path: Path) -> None:
    context, _ = _artifact_context(tmp_path / "current")
    _, foreign_store = _artifact_context(tmp_path / "foreign")
    foreign_ref = _publish_contract(
        foreign_store,
        [_marker("0", "IL7R", 0.01)],
    )

    with pytest.raises(ArtifactBoundaryError, match="当前 conversation"):
        InspectMarkerContractCapability().invoke(
            InspectMarkerContractRequest(marker_table=foreign_ref),
            context,
        )
    with pytest.raises(ArtifactBoundaryError, match="当前 conversation"):
        DeepCellAnnotationCapability(graph_factory=lambda: None).invoke(
            DeepCellAnnotationRequest(
                marker_table=foreign_ref,
                species="Human",
                tissue="PBMC",
            ),
            context,
        )


def test_workflow_projects_input_and_publishes_full_outputs(tmp_path: Path) -> None:
    context, store = _artifact_context(tmp_path / "conversation")
    marker_ref = _publish_contract(
        store,
        [_marker(str(index), f"G{index}", 0.01) for index in range(4)],
    )
    captured: list[dict] = []
    annotations = {
        "0": {
            "general_type": "Immune cells",
            "sub_type": "CD4 T cells",
            "cs_score": 95.0,
            "flags": [],
        },
        "1": {
            "general_type": "Immune cells",
            "sub_type": "Rare cells",
            "cs_score": 59.0,
            "flags": [],
        },
        "2": {
            "general_type": "Immune cells",
            "sub_type": "B cells",
            "cs_score": 90.0,
            "flags": ["cross_cluster_outlier"],
        },
        "3": {
            "general_type": "Immune cells",
            "sub_type": "Ambiguous cells (NeedsReview)",
            "cs_score": 90.0,
            "flags": [],
        },
    }
    report = "# Deep Annotation Report\n\nComplete."

    class RecordingGraph:
        def invoke(self, state):
            captured.append(state)
            return {
                **state,
                "cluster_annotations": annotations,
                "final_report": report,
            }

    capability = DeepCellAnnotationCapability(graph_factory=RecordingGraph)
    result = capability.invoke(
        DeepCellAnnotationRequest(
            marker_table=marker_ref,
            species="Human",
            tissue="PBMC",
        ),
        context,
    )

    assert len(captured) == 1
    captured_state = captured[0]
    pinned_contract = Path(captured_state.pop("contract_file_path"))
    assert captured_state == {
        "species": "Human",
        "tissue": "PBMC",
        "cluster_annotations": {},
        "final_report": "",
    }
    assert pinned_contract != store.resolve(marker_ref)
    assert "internal/graph-b" in pinned_contract.as_posix()
    assert pinned_contract.read_bytes() == store.resolve(marker_ref).read_bytes()
    assert result.source_marker_table == marker_ref
    assert result.cluster_count == 4
    assert result.manual_review_count == 3
    assert result.annotations.conversation_id == context.conversation_id
    assert result.report.conversation_id == context.conversation_id

    annotations_path = store.resolve(
        result.annotations,
        expected_kind="cluster_annotations",
    )
    payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "source_marker_table_id": str(marker_ref.artifact_id),
        "species": "Human",
        "tissue": "PBMC",
        "cluster_annotations": annotations,
    }
    report_path = store.resolve(result.report, expected_kind="annotation_report")
    assert report_path.read_text(encoding="utf-8") == report
    assert annotations_path.is_relative_to(store.workspace)
    assert report_path.is_relative_to(store.workspace)


def test_workflow_preserves_graph_b_review_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, store = _artifact_context(tmp_path / "conversation")
    marker_ref = _publish_contract(
        store,
        [_marker("7", "RARE", 0.01)],
    )

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
    graph_projection = graph_b.process_cluster_wrapper({"cluster_id": "7"})

    class ProjectionGraph:
        def invoke(self, state):
            return {
                **state,
                **graph_projection,
                "final_report": "controlled report",
            }

    result = DeepCellAnnotationCapability(graph_factory=ProjectionGraph).invoke(
        DeepCellAnnotationRequest(
            marker_table=marker_ref,
            species="Human",
            tissue="PBMC",
        ),
        context,
    )
    payload = json.loads(store.resolve(result.annotations).read_text(encoding="utf-8"))

    assert payload["cluster_annotations"] == graph_projection["cluster_annotations"]
    assert result.manual_review_count == 1


@pytest.mark.parametrize("payload", [b"not-json", b'{"metadata":{},"markers":[]}'])
def test_workflow_rejects_invalid_or_empty_marker_contract(
    tmp_path: Path,
    payload: bytes,
) -> None:
    context, store = _artifact_context(tmp_path / "conversation")
    path = store.workspace / "inputs" / "invalid.json"
    path.parent.mkdir()
    path.write_bytes(payload)
    marker_ref = store.publish(
        path,
        kind="marker_table",
        media_type="application/json",
    )

    with pytest.raises(CapabilityInputError, match="marker contract"):
        DeepCellAnnotationCapability(graph_factory=lambda: None).invoke(
            DeepCellAnnotationRequest(
                marker_table=marker_ref,
                species="Human",
                tissue="PBMC",
            ),
            context,
        )


@pytest.mark.parametrize(
    "final_state",
    [
        {"cluster_annotations": {}, "final_report": "report"},
        {
            "cluster_annotations": {
                "0": {"sub_type": "T cell", "cs_score": 90, "flags": []}
            },
            "final_report": "",
        },
    ],
)
def test_workflow_rejects_incomplete_graph_b_terminal_projection(
    tmp_path: Path,
    final_state: dict,
) -> None:
    context, store = _artifact_context(tmp_path / "conversation")
    marker_ref = _publish_contract(store, [_marker("0", "IL7R", 0.01)])

    class IncompleteGraph:
        def invoke(self, state):
            return {**state, **final_state}

    with pytest.raises(CapabilityExecutionError, match="未完整收敛|有效"):
        DeepCellAnnotationCapability(graph_factory=IncompleteGraph).invoke(
            DeepCellAnnotationRequest(
                marker_table=marker_ref,
                species="Human",
                tissue="PBMC",
            ),
            context,
        )
