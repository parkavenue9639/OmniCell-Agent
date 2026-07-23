"""Graph B 纯自推理改动：路由、跨簇复核、打分、Boost 不注入虚高分的单测。"""

import pytest

from omnicell_agent.annotation.graph import post_scorer_route
from omnicell_agent.annotation.nodes.consistency_reviewer import consistency_reviewer_node
from omnicell_agent.annotation.nodes.scorer import scorer_node
from omnicell_agent.annotation.nodes.boost import boost_node
from omnicell_agent.annotation.nodes.annotator import AnnotationOutput
from omnicell_agent.schema.contract import MarkerTableContract, MarkerGene


def test_post_scorer_route_high_score_ends():
    assert post_scorer_route({"quality_scores": {"cs_score": 80.0}, "retry_count": 0}) == "end"


def test_post_scorer_route_low_score_first_try_goes_boost():
    assert post_scorer_route({"quality_scores": {"cs_score": 50.0}, "retry_count": 0}) == "boost"


def test_post_scorer_route_low_score_after_boost_ends():
    assert post_scorer_route({"quality_scores": {"cs_score": 50.0}, "retry_count": 1}) == "end"


def test_consistency_reviewer_flags_singleton_minority():
    state = {
        "cluster_annotations": {
            "0": {"general_type": "Immune cells", "sub_type": "T", "cs_score": 95.0},
            "1": {"general_type": "Immune cells", "sub_type": "B", "cs_score": 90.0},
            "2": {"general_type": "Immune cells", "sub_type": "NK", "cs_score": 88.0},
            "3": {"general_type": "Immune cells", "sub_type": "Mono", "cs_score": 85.0},
            "4": {"general_type": "Immune cells", "sub_type": "DC", "cs_score": 82.0},
            "5": {"general_type": "Immune cells", "sub_type": "gdT", "cs_score": 80.0},
            "6": {"general_type": "Immune cells", "sub_type": "MAIT", "cs_score": 78.0},
            "7": {"general_type": "Immune cells", "sub_type": "Plasma", "cs_score": 76.0},
            "8": {"general_type": "Epithelial cells", "sub_type": "weird", "cs_score": 99.0},
        }
    }
    out = consistency_reviewer_node(state)
    ann = out["cluster_annotations"]["8"]
    assert "cross_cluster_outlier" in ann["flags"]
    assert ann["cs_score"] <= 60.0


def test_scorer_applies_self_consistency_penalty():
    st = {
        "cluster_id": "1",
        "top_n_markers": ["A", "B", "C", "D", "E"],
        "predictions": {"sub_type": "CD4+ T cells", "general_type": "Immune"},
        "quality_scores": {"validator_penalty": 0, "self_consistency_ok": 0.0},
    }
    out = scorer_node(st)
    assert out["quality_scores"]["cs_score"] == 85.0  # 100 - 15


def _minimal_marker_row(cid: str, gene: str) -> MarkerGene:
    return MarkerGene(
        gene_name=gene,
        cluster_id=cid,
        p_val=0.01,
        p_val_adj=0.05,
        log2FC=1.0,
        pct_1=0.5,
        pct_2=0.1,
    )


def test_boost_does_not_hardcode_cs_score(monkeypatch):
    """Boost 后分数须由 Validator/Scorer 决定，不得写入 90。"""
    from omnicell_agent.annotation.nodes import boost as boost_mod

    markers = [_minimal_marker_row("0", f"G{i}") for i in range(5)]
    contract = MarkerTableContract(markers=markers)
    monkeypatch.setattr(boost_mod.MarkerTableContract, "load_from_json", lambda p: contract)

    out_struct = AnnotationOutput(
        reasoning_chain="test",
        general_type="Immune cells",
        sub_type="Test cells",
        marker_evidence=["X -> test"],
    )

    class _Structured:
        def invoke(self, messages):
            return out_struct

    class _LLM:
        def with_structured_output(self, schema):
            return _Structured()

    monkeypatch.setattr(boost_mod.llm, "get_llm_by_alias", lambda *a, **k: _LLM())

    state = {
        "cluster_id": "0",
        "species": "Human",
        "tissue": "PBMC",
        "contract_file_path": "/tmp/fake.json",
        "retry_count": 0,
        "reasoning_messages": [],
        "quality_scores": {"validator_penalty": 10},
    }
    result = boost_node(state)
    assert "cs_score" not in result.get("quality_scores", {})
    assert "Boosted" in result["predictions"]["sub_type"]
    assert result["predictions"]["marker_evidence"] == ["X -> test"]
