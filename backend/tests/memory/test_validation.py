from __future__ import annotations

import hashlib

import pytest

from omnicell_agent.memory.errors import MemoryContentRejectedError
from omnicell_agent.memory.types import MemoryKind
from omnicell_agent.memory.validation import (
    memory_fingerprint,
    validate_memory_content,
    validate_stable_key,
)


def test_validates_normal_preference_and_scoped_scientific_observation() -> None:
    preference = validate_memory_content(
        "回答时优先使用中文，并保留必要的 English identifier。",
        kind=MemoryKind.RESPONSE_PREFERENCE,
    )
    assert preference.content.startswith("回答时")
    assert len(preference.sha256) == 64

    observation = validate_memory_content(
        "历史数据集的 cluster 2 曾呈现 T-cell marker 特征。",
        kind=MemoryKind.SCIENTIFIC_OBSERVATION,
        dataset_scope={"artifact_id": "8b5cbfd1-c8d4-4b2e-aafc-da4cc86fa2cc"},
        provenance=[
            {
                "source_verified": True,
                "conversation_id": "7b5cbfd1-c8d4-4b2e-aafc-da4cc86fa2cc",
                "run_id": "6b5cbfd1-c8d4-4b2e-aafc-da4cc86fa2cc",
                "artifact_id": "8b5cbfd1-c8d4-4b2e-aafc-da4cc86fa2cc",
                "message_ids": ["5b5cbfd1-c8d4-4b2e-aafc-da4cc86fa2cc"],
            }
        ],
    )
    assert observation.dataset_scope == {
        "artifact_id": "8b5cbfd1-c8d4-4b2e-aafc-da4cc86fa2cc"
    }


def test_can_preserve_exact_source_unicode_while_normalizing_fingerprint() -> None:
    source = "  Cafe\u0301\n"

    validated = validate_memory_content(
        source,
        kind=MemoryKind.PROFILE_FACT,
        preserve_original=True,
    )

    assert validated.content == source
    assert validated.sha256 == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert validated.fingerprint == memory_fingerprint("Café")


def test_scientific_observation_requires_scope_and_provenance() -> None:
    with pytest.raises(MemoryContentRejectedError):
        validate_memory_content(
            "历史数据中曾观察到一个细胞群。",
            kind=MemoryKind.SCIENTIFIC_OBSERVATION,
            dataset_scope={"dataset_id": "demo"},
        )


@pytest.mark.parametrize(
    "content",
    [
        "api_key = sk-abcdefghijklmnopqrstuvwxyz123456",
        "输入文件位于 /Users/researcher/private/pbmc.h5ad",
        "patient_id: P001",
        "stdout: exported raw analysis result",
        "%%MatrixMarket matrix coordinate real general",
        "1,2,3,4,5,6,7,8,9,10,11,12,13",
    ],
)
def test_rejects_sensitive_or_raw_content_without_echo(content: str) -> None:
    with pytest.raises(MemoryContentRejectedError) as captured:
        validate_memory_content(
            content,
            kind=MemoryKind.PROJECT_CONTEXT,
        )
    assert content not in str(captured.value)


def test_rejects_sensitive_stable_key_and_dataset_scope() -> None:
    with pytest.raises(MemoryContentRejectedError):
        validate_stable_key(
            "password:supersecretvalue",
            kind=MemoryKind.PROJECT_CONTEXT,
            content_sha256="a" * 64,
        )
    with pytest.raises(MemoryContentRejectedError):
        validate_memory_content(
            "历史数据集背景。",
            kind=MemoryKind.PROJECT_CONTEXT,
            dataset_scope={"patient_id": "P001"},
        )
