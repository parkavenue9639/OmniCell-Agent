from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from omnicell_agent.memory.ranking import rank_memory_candidates
from omnicell_agent.memory.types import (
    MemoryCandidate,
    MemoryKind,
    MemoryResourceIdentity,
    MemorySelectionReason,
    MemorySourceKind,
)


def _candidate(
    *,
    number: int,
    kind: MemoryKind,
    stable_key: str,
    content: str,
) -> MemoryCandidate:
    return MemoryCandidate(
        identity=MemoryResourceIdentity(
            item_id=UUID(int=number),
            version_id=UUID(int=number + 100),
            version_number=1,
            content_sha256=f"{number:064x}",
            kind=kind,
            source_kind=MemorySourceKind.EXPLICIT,
            selection_reason=MemorySelectionReason.DEFAULT,
        ),
        stable_key=stable_key,
        content=content,
        dataset_scope={},
        provenance=(),
        updated_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def test_ranking_is_lexical_bounded_and_deterministic_for_chinese() -> None:
    candidates = (
        _candidate(
            number=1,
            kind=MemoryKind.RESPONSE_PREFERENCE,
            stable_key="language",
            content="回答时优先使用中文。",
        ),
        _candidate(
            number=2,
            kind=MemoryKind.PROJECT_CONTEXT,
            stable_key="runtime",
            content="本地执行环境使用 OrbStack 和 PostgreSQL。",
        ),
        _candidate(
            number=3,
            kind=MemoryKind.PROFILE_FACT,
            stable_key="role",
            content="用户从事单细胞科研。",
        ),
    )
    first = rank_memory_candidates(
        "当前本地 OrbStack PostgreSQL 环境",
        reversed(candidates),
        limit=2,
    )
    second = rank_memory_candidates(
        "当前本地 OrbStack PostgreSQL 环境",
        candidates,
        limit=2,
    )
    assert first == second
    assert first[0].stable_key == "runtime"
    assert len(first) == 2


def test_unrelated_preferences_do_not_starve_relevant_project_context() -> None:
    preferences = tuple(
        _candidate(
            number=index,
            kind=MemoryKind.RESPONSE_PREFERENCE,
            stable_key=f"preference.{index}",
            content=f"回答格式偏好 {index}。",
        )
        for index in range(1, 9)
    )
    project = _candidate(
        number=20,
        kind=MemoryKind.PROJECT_CONTEXT,
        stable_key="database",
        content="本机数据库采用 PostgreSQL。",
    )

    ranked = rank_memory_candidates(
        "本地数据库连接",
        (*preferences, project),
        limit=8,
    )

    assert project in ranked
    assert sum(
        value.identity.kind is MemoryKind.RESPONSE_PREFERENCE
        for value in ranked
    ) <= 2


def test_unrelated_non_preferences_are_not_loaded() -> None:
    unrelated = (
        _candidate(
            number=31,
            kind=MemoryKind.PROFILE_FACT,
            stable_key="role",
            content="用户从事单细胞科研。",
        ),
        _candidate(
            number=32,
            kind=MemoryKind.PROJECT_CONTEXT,
            stable_key="frontend",
            content="界面使用 React。",
        ),
    )

    assert rank_memory_candidates(
        "如何准备晚餐",
        unrelated,
        limit=8,
    ) == ()
