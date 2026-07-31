"""Small deterministic lexical ranker for the local research prototype."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .types import MemoryCandidate, MemoryKind


_TIE_BREAK_PRIORITY = {
    MemoryKind.RESPONSE_PREFERENCE: 3,
    MemoryKind.PROFILE_FACT: 2,
    MemoryKind.PROJECT_CONTEXT: 1,
    MemoryKind.SCIENTIFIC_OBSERVATION: 0,
}


def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words = set(re.findall(r"[\w.-]{2,}", normalized))
    compact = "".join(character for character in normalized if not character.isspace())
    # Character bigrams keep Chinese retrieval deterministic without adding a
    # tokenizer or vector service to the local prototype.
    words.update(compact[index : index + 2] for index in range(len(compact) - 1))
    return words


def _lexical_match(query: str, candidate: MemoryCandidate) -> tuple[int, int]:
    query_terms = _terms(query)
    content_terms = _terms(f"{candidate.stable_key} {candidate.content}")
    overlap = len(query_terms & content_terms)
    phrase_bonus = 25 if query.casefold() in candidate.content.casefold() else 0
    return overlap, phrase_bonus


def lexical_score(query: str, candidate: MemoryCandidate) -> int:
    overlap, phrase_bonus = _lexical_match(query, candidate)
    return (
        overlap * 4
        + phrase_bonus
        + _TIE_BREAK_PRIORITY[candidate.identity.kind]
    )


def rank_memory_candidates(
    query: str,
    candidates: Iterable[MemoryCandidate],
    *,
    limit: int,
    include_always_on_preferences: bool = True,
) -> tuple[MemoryCandidate, ...]:
    if not 1 <= limit <= 32:
        raise ValueError("memory rank limit must be between 1 and 32")
    preferences: list[MemoryCandidate] = []
    relevant: list[MemoryCandidate] = []
    for candidate in candidates:
        overlap, phrase_bonus = _lexical_match(query, candidate)
        is_preference = candidate.identity.kind is MemoryKind.RESPONSE_PREFERENCE
        if is_preference and include_always_on_preferences:
            preferences.append(candidate)
        elif overlap > 0 or phrase_bonus > 0:
            relevant.append(candidate)

    def sort_key(candidate: MemoryCandidate) -> tuple[int, str, str, str]:
        return (
            -lexical_score(query, candidate),
            candidate.identity.kind.value,
            candidate.stable_key,
            str(candidate.identity.item_id),
        )

    preferences.sort(key=sort_key)
    relevant.sort(key=sort_key)
    selected_preferences = preferences[: min(2, limit)]
    remaining = limit - len(selected_preferences)
    selected = [*selected_preferences, *relevant[:remaining]]
    selected.sort(key=sort_key)
    return tuple(selected)


__all__ = ["lexical_score", "rank_memory_candidates"]
