"""
ContextResolver 轻量单元测试。

只验证「抽取结果 -> 规范化 -> 冲突裁决 -> 写入 task_context」的端到端行为，
不实际调用任何外部 LLM：通过 monkeypatch 将 LLMSelector.get_llm 替换为
一个可预测的假对象，使 `with_structured_output(...).invoke(...)` 直接返回
我们预置的 ContextProfile。
"""
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from langchain_core.messages import HumanMessage

from omnicell_agent.pipeline.nodes import context_resolver as cr
from omnicell_agent.pipeline.nodes.context_resolver import (
    ContextProfile,
    _normalize_tissue,
    run_context_resolver,
)


class _FakeStructuredLLM:
    def __init__(self, profile: ContextProfile):
        self._profile = profile

    def invoke(self, _messages):
        return self._profile


class _FakeLLM:
    def __init__(self, profile: ContextProfile):
        self._profile = profile

    def with_structured_output(self, _schema):
        return _FakeStructuredLLM(self._profile)


def _patch_llm(monkeypatch, profile: ContextProfile) -> None:
    monkeypatch.setattr(cr.LLMSelector, "get_llm", staticmethod(lambda **_: _FakeLLM(profile)))


def _patch_h5ad_empty(monkeypatch) -> None:
    monkeypatch.setattr(cr, "_probe_h5ad_metadata", lambda _path: {
        "filename": None,
        "uns_keys": [],
        "obs_columns": [],
        "obs_tissue_values": [],
        "obs_organism_values": [],
    })


def _base_state(prompt: str, data_path: str = "/app/data/pbmc3k_raw.h5ad") -> Dict[str, Any]:
    return {
        "raw_data_path": data_path,
        "marker_table_path": "/app/data/markers.json",
        "messages": [HumanMessage(content=prompt)],
        "task_context": {},
        "plan_steps": [],
        "current_step_index": 0,
        "last_generated_code": "",
        "sandbox_execution_result": {},
    }


def test_normalize_tissue_canonicalizes_common_aliases():
    assert _normalize_tissue("pbmc") == "PBMC"
    assert _normalize_tissue("Peripheral Blood Mononuclear Cells") == "PBMC"
    assert _normalize_tissue("whole blood") == "Blood"
    assert _normalize_tissue("bone marrow sample") == "Bone Marrow"
    assert _normalize_tissue("breast tumor") == "Breast Cancer"
    assert _normalize_tissue("lung adenocarcinoma") == "Lung Cancer"
    assert _normalize_tissue("Visium spatial") == "Spatial"
    assert _normalize_tissue("") == "Unknown"
    assert _normalize_tissue(None) == "Unknown"


def test_resolver_extracts_pbmc_from_prompt(monkeypatch):
    _patch_h5ad_empty(monkeypatch)
    _patch_llm(
        monkeypatch,
        ContextProfile(
            species="Human",
            tissue="PBMC",
            disease_state="Unknown",
            goal_type="immune_profiling",
        ),
    )

    state = _base_state(
        "这里有一份人类外周血（PBMC）的单细胞测序数据，请做免疫亚型注释。"
    )
    out = run_context_resolver(state)

    resolved = out["task_context"]["resolved_context"]
    assert resolved["species"] == "Human"
    assert resolved["tissue"] == "PBMC"
    assert resolved["goal_type"] == "immune_profiling"
    assert "sources" in resolved
    assert resolved["sources"]["raw_profile"]["tissue"] == "PBMC"


def test_resolver_normalizes_noisy_llm_tissue(monkeypatch):
    _patch_h5ad_empty(monkeypatch)
    _patch_llm(
        monkeypatch,
        ContextProfile(
            species="Human",
            tissue="peripheral blood mononuclear cells",
            disease_state="Unknown",
            goal_type="general_annotation",
        ),
    )

    state = _base_state("请对这份血液数据进行聚类与注释。")
    out = run_context_resolver(state)

    assert out["task_context"]["resolved_context"]["tissue"] == "PBMC"


def test_resolver_fills_unknown_tissue_with_filename_hint(monkeypatch):
    monkeypatch.setattr(cr, "_probe_h5ad_metadata", lambda _path: {
        "filename": "pbmc3k_raw.h5ad",
        "uns_keys": [],
        "obs_columns": [],
        "obs_tissue_values": [],
        "obs_organism_values": [],
    })
    _patch_llm(
        monkeypatch,
        ContextProfile(
            species="Human",
            tissue="Unknown",
            disease_state="Unknown",
            goal_type="general_annotation",
        ),
    )

    state = _base_state("随便帮我跑个标准分析")
    out = run_context_resolver(state)

    assert out["task_context"]["resolved_context"]["tissue"] == "PBMC"


def test_resolver_falls_back_when_llm_raises(monkeypatch):
    monkeypatch.setattr(cr, "_probe_h5ad_metadata", lambda _path: {
        "filename": "mouse_mm10_spatial.h5ad",
        "uns_keys": [],
        "obs_columns": [],
        "obs_tissue_values": [],
        "obs_organism_values": [],
    })

    class _BoomLLM:
        def with_structured_output(self, _schema):
            raise RuntimeError("simulated LLM outage")

    monkeypatch.setattr(cr.LLMSelector, "get_llm", staticmethod(lambda **_: _BoomLLM()))

    state = _base_state("随意分析一下", data_path="/app/data/mouse_mm10_spatial.h5ad")
    out = run_context_resolver(state)

    resolved = out["task_context"]["resolved_context"]
    assert resolved["species"] == "Mouse"
    assert resolved["tissue"] == "Spatial"
    assert resolved["goal_type"] == "general_annotation"
