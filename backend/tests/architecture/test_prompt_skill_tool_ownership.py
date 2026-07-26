from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from omnicell_agent.agent.factory import _build_system_prompt
from omnicell_agent.agent.response_contract import render_response_contract
from omnicell_agent import llm
from omnicell_agent.capabilities.bootstrap import build_domain_capability_layer
from omnicell_agent.pipeline.nodes.context_resolver import ContextProfile
from omnicell_agent.pipeline.nodes.planner import run_planner


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = BACKEND_ROOT / "src" / "omnicell_agent"
PROMPT_ROOT = PACKAGE_ROOT / "prompts"


EXPECTED_SKILL_TOOLS = {
    "cell-type-annotation": (
        "inspect_marker_table",
        "find_marker_genes",
        "annotate_cell_clusters",
    ),
    "cluster-and-marker-analysis": (
        "inspect_dataset",
        "cluster_cells",
        "find_marker_genes",
        "inspect_marker_table",
    ),
    "exploratory-analysis": (
        "inspect_dataset",
        "run_exploratory_analysis",
    ),
    "scientific-visualization": (
        "inspect_dataset",
        "cluster_cells",
        "plot_pca_clusters",
    ),
    "single-cell-preprocessing": (
        "inspect_dataset",
        "quality_control",
        "normalize_expression",
        "cluster_cells",
    ),
}

EXPECTED_DOMAIN_TOOLS = {
    "annotate_cell_clusters",
    "cluster_cells",
    "find_marker_genes",
    "inspect_dataset",
    "inspect_marker_table",
    "normalize_expression",
    "plot_pca_clusters",
    "quality_control",
    "run_exploratory_analysis",
}


def test_builtin_skill_and_tool_responsibility_matrix_is_complete() -> None:
    layer = build_domain_capability_layer()

    assert {
        skill.name: skill.tools
        for skill in layer.skills.skills
    } == EXPECTED_SKILL_TOOLS
    assert {spec.name for spec in layer.registry.specs} == EXPECTED_DOMAIN_TOOLS

    specs = {spec.name: spec for spec in layer.registry.specs}
    assert specs["inspect_dataset"].mode.value == "inspect"
    assert specs["inspect_marker_table"].mode.value == "inspect"
    assert specs["annotate_cell_clusters"].mode.value == "composite"
    assert specs["run_exploratory_analysis"].mode.value == "composite"
    assert all(spec.prompt_hint.strip() for spec in specs.values())


def test_skill_bodies_own_domain_method_and_evidence_boundaries() -> None:
    layer = build_domain_capability_layer()
    assert {
        skill.name: skill.version
        for skill in layer.skills.skills
    } == {
        "cell-type-annotation": "1.1",
        "cluster-and-marker-analysis": "1.2",
        "exploratory-analysis": "1.1",
        "scientific-visualization": "1.1",
        "single-cell-preprocessing": "1.1",
    }
    bodies = {
        skill.name: skill.load_body()
        for skill in layer.skills.skills
    }

    assert all(
        phrase in bodies["single-cell-preprocessing"]
        for phrase in ("固定经验阈值", "不等同于批次校正", "最新输出")
    )
    assert all(
        phrase in bodies["cluster-and-marker-analysis"]
        for phrase in ("post-clustering", "选择过程", "因果")
    )
    assert all(
        phrase in bodies["cell-type-annotation"]
        for phrase in ("Unknown", "更宽", "不是校准概率", "暂定注释")
    )
    assert all(
        phrase in bodies["scientific-visualization"]
        for phrase in ("不自动证明", "批次校正成功", "细胞身份")
    )
    assert all(
        phrase in bodies["exploratory-analysis"]
        for phrase in ("不能因为用户说“分析一下”", "最少必要步骤", "不依赖其他 Tool")
    )


def test_top_level_response_and_loop_prompt_stay_domain_method_agnostic() -> None:
    fixed_prompt_sources = (
        render_response_contract(),
        inspect.getsource(_build_system_prompt),
    )
    forbidden_domain_facts = (
        "Leiden",
        "Scanpy",
        "rank_genes_groups",
        "pct.1",
        "CD14",
        "线粒体基因",
    )

    for source in fixed_prompt_sources:
        assert all(term not in source for term in forbidden_domain_facts)


def test_conversation_title_prompt_does_not_assume_domain_execution() -> None:
    source = (
        PACKAGE_ROOT / "runs" / "titles.py"
    ).read_text(encoding="utf-8")

    assert "单细胞科研 Agent" not in source
    assert "不要假设用户正在处理数据或执行领域分析" in source
    assert 'return "科研分析对话"' in source


def test_active_internal_prompt_assets_are_unique_and_bounded() -> None:
    active = {
        path.relative_to(PROMPT_ROOT).as_posix()
        for path in PROMPT_ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert active == {
        "__init__.py",
        "evaluator_vision_human.txt",
        "evaluator_vision_system.txt",
        "planner_system.txt",
        "programmer_system.txt",
    }

    combined = "\n".join(
        (PROMPT_ROOT / name).read_text(encoding="utf-8")
        for name in sorted(active)
        if name.endswith(".txt")
    )
    assert all(
        term not in combined
        for term in (
            "世界顶尖",
            "世界级",
            "顶级的计算",
            "Chain-of-Thought",
            "极其精确",
            "致命约束",
            "深潜抢救",
            "无论 Planner 有没有明说",
        )
    )
    assert "不得为避免报错而隐式补做其他分析" in combined
    assert "不能仅凭一张图认定聚类稳健" in combined


def test_annotation_prompts_request_evidence_not_hidden_reasoning() -> None:
    sources = "\n".join(
        (PACKAGE_ROOT / "annotation" / "nodes" / filename).read_text(
            encoding="utf-8"
        )
        for filename in ("annotator.py", "validator.py", "boost.py", "reporter.py")
    )

    assert "Follow Chain-of-Thought" not in sources
    assert "Be extremely harsh" not in sources
    assert "master cell biologist" not in sources
    assert "High Hallucination Risk" not in sources
    assert "Total Clusters Authenticated" not in sources
    assert "暂定注释报告" in sources
    assert "不是校准概率" in sources


def test_unknown_metadata_is_not_silently_promoted_to_human() -> None:
    profile = ContextProfile()

    assert profile.species == "Unknown"
    assert profile.tissue == "Unknown"
    assert profile.goal_type == "unknown"


def test_internal_recipes_do_not_contain_default_dataset_paths() -> None:
    recipe_root = PACKAGE_ROOT / "recipes"
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(recipe_root.glob("*/scripts/execute.py"))
    )

    assert "globals().get('raw_data_path'," not in sources
    assert 'globals().get("raw_data_path",' not in sources
    assert "globals().get('marker_table_path'," not in sources
    assert 'globals().get("marker_table_path",' not in sources
    assert "/app/data/pbmc3k_raw.h5ad" not in sources
    assert "/app/data/spatial_sample.h5ad" not in sources


def test_exploratory_planner_fails_closed_without_default_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStructuredModel:
        def invoke(self, _messages):
            raise RuntimeError("controlled planner failure")

    class BrokenModel:
        def with_structured_output(self, _schema):
            return BrokenStructuredModel()

    monkeypatch.setattr(
        llm,
        "get_llm_by_alias",
        lambda *args, **kwargs: BrokenModel(),
    )

    with pytest.raises(
        RuntimeError,
        match="规划失败，未执行任何科学步骤",
    ):
        run_planner(
            {
                "messages": [HumanMessage(content="绘制一个非标准诊断图")],
                "task_context": {},
            }
        )
