from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from omnicell_agent.llm.types import LLMRole
from omnicell_agent.runs.titles import (
    LLMConversationTitleGenerator,
    fallback_conversation_title,
    sanitize_conversation_title,
)


class _SummaryModel:
    def __init__(self, response: str | BaseException) -> None:
        self.response = response

    async def ainvoke(self, _messages):
        if isinstance(self.response, BaseException):
            raise self.response
        return AIMessage(content=self.response)


class _SummaryFactory:
    def __init__(self, response: str | BaseException) -> None:
        self.response = response
        self.aliases: list[LLMRole] = []

    def create(self, alias, **_overrides):
        self.aliases.append(alias)
        return _SummaryModel(self.response)


def test_fallback_title_uses_first_meaningful_clause() -> None:
    title = fallback_conversation_title(
        "请帮我检查这份 PBMC 数据的物种和组织；不要执行聚类。"
    )

    assert title == "检查这份 PBMC 数据的物种和组织"


def test_title_sanitizer_removes_decoration_and_bounds_length() -> None:
    title = sanitize_conversation_title(
        '### 标题："' + ("PBMC 细胞注释与 Marker 分析" * 4) + '。"'
    )

    assert title.startswith("PBMC 细胞注释")
    assert len(title) == 40
    assert title.endswith("…")
    assert '"' not in title


@pytest.mark.asyncio
async def test_llm_title_generator_uses_summary_alias() -> None:
    factory = _SummaryFactory("**PBMC 细胞类型注释。**")
    generator = LLMConversationTitleGenerator(factory)  # type: ignore[arg-type]

    title = await generator.generate("帮我完成 PBMC 细胞类型注释")

    assert title == "PBMC 细胞类型注释"
    assert factory.aliases == [LLMRole.SUMMARY]


@pytest.mark.asyncio
async def test_llm_title_generator_falls_back_without_failing_run() -> None:
    factory = _SummaryFactory(RuntimeError("provider unavailable"))
    generator = LLMConversationTitleGenerator(factory)  # type: ignore[arg-type]

    title = await generator.generate("请解释为什么聚类后还需要 Marker Gene")

    assert title == "解释为什么聚类后还需要 Marker Gene"
