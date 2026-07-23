from __future__ import annotations

from pathlib import Path

import pytest

from omnicell_agent.capabilities.catalog import (
    SkillCatalog,
    SkillCatalogError,
)


def _write_skill(root: Path) -> None:
    skill = root / "demo-skill"
    (skill / "references").mkdir(parents=True)
    (skill / "examples").mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: demo-skill
description: 只暴露给初始上下文的摘要。
version: "1.0"
tools:
  - inspect_demo
  - run_demo
---

# 私有正文

只有按需加载后才能看到 BODY_SENTINEL。
""",
        encoding="utf-8",
    )
    (skill / "references" / "contract.md").write_text(
        "REFERENCE_SENTINEL",
        encoding="utf-8",
    )
    (skill / "examples" / "simple.md").write_text(
        "EXAMPLE_SENTINEL",
        encoding="utf-8",
    )


def test_skill_catalog_progressively_loads_body_reference_and_example(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path)
    catalog = SkillCatalog.load_from_directory(tmp_path)
    skill = catalog.get("demo-skill")

    assert skill.content is None
    assert "只暴露给初始上下文的摘要" in catalog.summaries()
    assert "BODY_SENTINEL" not in catalog.summaries()
    assert "BODY_SENTINEL" in catalog.load("demo-skill")
    assert catalog.load(
        "demo-skill",
        reference="contract",
    ) == "REFERENCE_SENTINEL"
    assert catalog.load(
        "demo-skill",
        example="examples/simple.md",
    ) == "EXAMPLE_SENTINEL"


def test_skill_catalog_rejects_ambiguous_or_escaping_subdocument(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path)
    catalog = SkillCatalog.load_from_directory(tmp_path)

    with pytest.raises(SkillCatalogError, match="不能同时"):
        catalog.load(
            "demo-skill",
            reference="contract",
            example="simple",
        )
    with pytest.raises(SkillCatalogError, match="非法"):
        catalog.load("demo-skill", reference="../outside")
    with pytest.raises(SkillCatalogError, match="不存在"):
        catalog.load("demo-skill", reference="missing")


def test_skill_catalog_rejects_unknown_frontmatter_list(tmp_path: Path) -> None:
    skill = tmp_path / "bad-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: bad-skill
description: bad
capabilities:
  - old_tool
---
body
""",
        encoding="utf-8",
    )

    with pytest.raises(SkillCatalogError, match="不支持"):
        SkillCatalog.load_from_directory(tmp_path)
