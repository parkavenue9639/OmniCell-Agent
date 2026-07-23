"""公开领域能力与 benchmark 评估脚本的离线烟测。"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_supported_domain_capability_layer_exposes_graph_workflows() -> None:
    from omnicell_agent.capabilities.bootstrap import build_domain_capability_layer

    layer = build_domain_capability_layer()

    assert [spec.name for spec in layer.registry.specs if spec.kind.value == "workflow"] == [
        "single_cell_analysis",
        "deep_cell_annotation",
    ]


def test_evaluate_matches_perfect_predictions(tmp_path: Path):
    """若预测与 GT 标签一致，SFM/LMR 应接近 1。"""
    import importlib.util

    ev_path = PROJECT_ROOT / "scripts" / "benchmark" / "evaluate.py"
    spec = importlib.util.spec_from_file_location("eval_bench", ev_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    evaluate_one = mod.evaluate_one

    gt = {
        "clusters": {
            "0": {"label": "CD4 T cells", "ambiguous": False},
            "1": {"label": "B cells", "ambiguous": False},
        }
    }
    meta = {"dataset": "pbmc3k", "condition": "test", "tissue": "PBMC", "skip_eval": False}
    ann = {
        "species": "Human",
        "tissue": "PBMC",
        "cluster_annotations": {
            "0": {
                "general_type": "Immune cells",
                "sub_type": "CD4 T cells",
                "cs_score": 95.0,
                "flags": [],
            },
            "1": {
                "general_type": "Immune cells",
                "sub_type": "B cells",
                "cs_score": 100.0,
                "flags": ["boosted"],
            },
        },
    }
    p = tmp_path / "ann.json"
    p.write_text(json.dumps(ann), encoding="utf-8")
    synonyms = {}
    hall = {"PBMC": ["pancreatic"]}
    r = evaluate_one(p, gt, meta, synonyms, hall)
    assert r["skip_eval"] is False
    assert r["LMR"] == 1.0
    assert r["SFM"] == 1.0
    assert r["HR"] == 0.0
    assert r["boost_rate"] == 0.5
