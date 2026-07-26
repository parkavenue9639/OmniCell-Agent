"""Bounded conversation title generation."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from omnicell_agent.llm.factory import LLMFactory
from omnicell_agent.llm.types import LLMRole


logger = logging.getLogger(__name__)

DEFAULT_CONVERSATION_TITLE = "新分析对话"
MAX_AUTO_TITLE_LENGTH = 40
_AUTO_TITLE_PLACEHOLDERS = frozenset({"", DEFAULT_CONVERSATION_TITLE})
_LEADING_REQUEST_WORDS = re.compile(
    r"^(?:请帮我|请你|请|麻烦你|麻烦|帮我|我想要|我想|能否|可以帮我|可以)\s*"
)
_TITLE_PREFIX = re.compile(r"^(?:标题|对话标题|会话标题)\s*[:：]\s*", re.IGNORECASE)
_TITLE_DECORATION = re.compile(r"^[#*`\"'“”‘’《》\s]+|[#*`\"'“”‘’《》\s]+$")


class ConversationTitleGenerator(Protocol):
    async def generate(self, goal: str) -> str: ...


def is_auto_title_placeholder(title: str | None) -> bool:
    return title is None or title.strip() in _AUTO_TITLE_PLACEHOLDERS


def sanitize_conversation_title(value: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    normalized = _TITLE_DECORATION.sub("", normalized)
    normalized = _TITLE_PREFIX.sub("", normalized)
    normalized = _TITLE_DECORATION.sub("", normalized)
    normalized = normalized.rstrip("。.!！?？;；:：,，")
    if len(normalized) > MAX_AUTO_TITLE_LENGTH:
        normalized = f"{normalized[: MAX_AUTO_TITLE_LENGTH - 1].rstrip()}…"
    return normalized


def fallback_conversation_title(goal: str) -> str:
    normalized = " ".join(goal.split())
    normalized = _LEADING_REQUEST_WORDS.sub("", normalized)
    first_clause = re.split(r"[。！？!?；;\n]", normalized, maxsplit=1)[0]
    candidate = sanitize_conversation_title(first_clause)
    if candidate:
        return candidate
    return "科研分析对话"


@dataclass(frozen=True, slots=True)
class DeterministicConversationTitleGenerator:
    async def generate(self, goal: str) -> str:
        return fallback_conversation_title(goal)


@dataclass(frozen=True, slots=True)
class LLMConversationTitleGenerator:
    llm_factory: LLMFactory
    timeout_seconds: float = 8.0

    async def generate(self, goal: str) -> str:
        fallback = fallback_conversation_title(goal)
        try:
            model = self.llm_factory.create(LLMRole.SUMMARY, temperature=0.0)
            response = await asyncio.wait_for(
                model.ainvoke(
                    [
                        SystemMessage(
                            content=(
                                "你负责为 OmniCell 科研 Agent 的对话生成简洁中文标题。"
                                "只输出标题，不要解释、引号、Markdown 或句末标点；"
                                "忠实概括当前用户目标；不要假设用户正在处理数据或执行领域分析。"
                                "保留关键对象与目标，长度不超过 24 个汉字或 40 个字符。"
                            )
                        ),
                        HumanMessage(content=goal[:4_000]),
                    ]
                ),
                timeout=self.timeout_seconds,
            )
            content = response.content
            rendered = (
                content
                if isinstance(content, str)
                else " ".join(
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
            title = sanitize_conversation_title(rendered)
            return title or fallback
        except Exception:
            logger.warning(
                "conversation title generation failed; using deterministic fallback",
                exc_info=True,
            )
            return fallback


__all__ = [
    "DEFAULT_CONVERSATION_TITLE",
    "ConversationTitleGenerator",
    "DeterministicConversationTitleGenerator",
    "LLMConversationTitleGenerator",
    "fallback_conversation_title",
    "is_auto_title_placeholder",
    "sanitize_conversation_title",
]
