from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from omnicell_agent.api.app import create_app
from omnicell_agent.api.contracts import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryRunMode,
    MemorySettingsRead,
    PageInfo,
    RunCreateResponse,
    RunMemoryContextRead,
    RunMemoryInputRead,
    RunRead,
)
from omnicell_agent.api.service import ApiService, project_memory
from omnicell_agent.memory.errors import (
    MemoryConflictError,
    MemoryContentRejectedError,
    MemorySourceInvalidError,
)
from omnicell_agent.memory.types import (
    MemoryKind,
    MemoryRecord,
    MemorySourceKind,
    MemoryStatus,
)
from omnicell_agent.runs.status import RunStatus


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)


def _record(
    *,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    include_body: bool = True,
) -> MemoryRecord:
    memory_id = UUID("00000000-0000-0000-0000-000000000101")
    version_id = UUID("00000000-0000-0000-0000-000000000102")
    conversation_id = UUID("00000000-0000-0000-0000-000000000103")
    run_id = UUID("00000000-0000-0000-0000-000000000104")
    message_id = UUID("00000000-0000-0000-0000-000000000105")
    return MemoryRecord(
        item_id=memory_id,
        scope_key="local-default",
        stable_key="response.language",
        kind=MemoryKind.RESPONSE_PREFERENCE,
        status=status,
        current_version=1 if status is not MemoryStatus.PURGED else None,
        version_id=version_id if status is not MemoryStatus.PURGED else None,
        content_sha256=("a" * 64 if status is not MemoryStatus.PURGED else None),
        content=(
            "回答时优先使用中文。"
            if include_body and status is not MemoryStatus.PURGED
            else None
        ),
        source_kind=(
            MemorySourceKind.EXPLICIT
            if status is not MemoryStatus.PURGED
            else None
        ),
        source_refs=(
            {
                "conversation_id": str(conversation_id),
                "run_id": str(run_id),
                "message_ids": [str(message_id)],
                "host_path": "/private/tmp/internal-memory.txt",
                "provider_response": "must-not-leak",
            },
        )
        if status is not MemoryStatus.PURGED
        else (),
        dataset_scope={},
        expires_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


class _MemoryApiService:
    def __init__(self) -> None:
        self.record = _record()
        self.run_calls: list[dict[str, object]] = []
        self.conversation_id = uuid4()
        self.run_id = uuid4()

    async def get_memory_settings(self) -> MemorySettingsRead:
        return self._settings()

    async def update_memory_settings(
        self,
        *,
        expected_version: int,
        use_memory: bool | None,
        generate_candidates: bool | None,
        enable_agent_tools: bool | None,
    ) -> MemorySettingsRead:
        assert expected_version == 1
        return self._settings(
            use_memory=bool(use_memory),
            generate_candidates=bool(generate_candidates),
            enable_agent_tools=bool(enable_agent_tools),
            consent=True,
        )

    async def decide_memory_provider_consent(
        self,
        *,
        decision: str,
        statement_version: str,
        expected_version: int,
    ) -> MemorySettingsRead:
        assert statement_version == "memory-provider-v1"
        assert expected_version == 1
        return self._settings(consent=decision == "grant")

    async def list_memories(
        self,
        *,
        kind,
        status,
        cursor,
        limit,
    ) -> MemoryListResponse:
        del kind, status, cursor, limit
        return MemoryListResponse(
            items=[project_memory(self.record)],
            page=PageInfo(next_cursor=None, has_more=False),
        )

    async def create_memory(self, request):
        if "api_key" in request.content:
            raise MemoryContentRejectedError()
        return project_memory(self.record)

    async def get_memory(self, memory_id):
        assert memory_id == self.record.item_id
        return project_memory(self.record)

    async def approve_memory(self, memory_id, *, expected_version):
        assert memory_id == self.record.item_id
        assert expected_version == 1
        return project_memory(self.record)

    async def correct_memory(self, memory_id, request):
        del memory_id, request
        raise MemoryConflictError()

    async def forget_memory(self, memory_id, *, expected_version):
        assert memory_id == self.record.item_id
        assert expected_version == 1
        return project_memory(_record(status=MemoryStatus.REVOKED))

    async def purge_memory(self, memory_id, *, expected_version):
        assert memory_id == self.record.item_id
        assert expected_version == 1
        return project_memory(_record(status=MemoryStatus.PURGED))

    async def get_run_memory_context(self, run_id):
        return RunMemoryContextRead(
            run_id=run_id,
            snapshot_id=uuid4(),
            mode=MemoryRunMode.SELECTED,
            outcome="loaded",
            inputs=[
                RunMemoryInputRead(
                    item_id=self.record.item_id,
                    version_id=self.record.version_id,
                    version_number=1,
                    content_sha256="a" * 64,
                    kind="response_preference",
                    source_kind="explicit",
                    selection_reason="selected",
                )
            ],
            created_at=NOW,
        )

    async def create_run(self, **kwargs) -> RunCreateResponse:
        self.run_calls.append(dict(kwargs))
        return RunCreateResponse(
            run=RunRead(
                run_id=self.run_id,
                conversation_id=kwargs["conversation_id"],
                status=RunStatus.PENDING,
                last_sequence=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    @staticmethod
    def _settings(
        *,
        use_memory: bool = False,
        generate_candidates: bool = False,
        enable_agent_tools: bool = False,
        consent: bool = False,
    ) -> MemorySettingsRead:
        return MemorySettingsRead(
            version=1,
            use_memory=use_memory,
            generate_candidates=generate_candidates,
            enable_agent_tools=enable_agent_tools,
            provider_consent_granted=consent,
            provider_consent_version=(
                "memory-provider-v1" if consent else None
            ),
            provider_consented_at=NOW if consent else None,
            updated_at=NOW,
        )


def test_memory_crud_routes_are_explicit_and_do_not_project_internal_fields() -> None:
    service = _MemoryApiService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]
    memory_id = str(service.record.item_id)

    settings = client.get("/api/v1/memory/settings")
    assert settings.status_code == 200
    assert "no-store" in settings.headers["cache-control"]
    assert "private" in settings.headers["cache-control"]
    assert settings.json()["use_memory"] is False
    assert settings.json()["provider_consent_granted"] is False
    updated = client.patch(
        "/api/v1/memory/settings",
        json={
            "expected_version": 1,
            "generate_candidates": True,
        },
    )
    assert updated.status_code == 200
    missing_version = client.patch(
        "/api/v1/memory/settings",
        json={"generate_candidates": True},
    )
    assert missing_version.status_code == 422

    consent = client.post(
        "/api/v1/memory/provider-consent",
        json={
            "decision": "grant",
            "statement_version": "memory-provider-v1",
            "confirmed": True,
            "expected_version": 1,
        },
    )
    assert consent.status_code == 200
    assert consent.json()["provider_consent_granted"] is True
    stale_consent = client.post(
        "/api/v1/memory/provider-consent",
        json={
            "decision": "grant",
            "statement_version": "memory-provider-v0",
            "confirmed": True,
            "expected_version": 1,
        },
    )
    assert stale_consent.status_code == 422

    created = client.post(
        "/api/v1/memories",
        json={
            "kind": "response_preference",
            "stable_key": "response.language",
            "content": "回答时优先使用中文。",
        },
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store, private"
    assert created.json()["memory_id"] == memory_id
    assert created.json()["content"] == "回答时优先使用中文。"
    serialized = created.text
    assert "host_path" not in serialized
    assert "/private/tmp" not in serialized
    assert "provider_response" not in serialized
    assert "must-not-leak" not in serialized

    unverified_science = client.post(
        "/api/v1/memories",
        json={
            "kind": "scientific_observation",
            "content": "历史 cluster 2 曾呈现 T-cell marker。",
            "dataset_scope": {"artifact_id": str(uuid4())},
        },
    )
    assert unverified_science.status_code == 422

    listed = client.get("/api/v1/memories")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store, private"
    assert [item["memory_id"] for item in listed.json()["items"]] == [
        memory_id
    ]

    detail = client.get(f"/api/v1/memories/{memory_id}")
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store, private"
    assert set(detail.json()["source"]) == {
        "source_kind",
        "conversation_id",
        "run_id",
        "message_ids",
    }

    forgotten = client.post(
        f"/api/v1/memories/{memory_id}/forget",
        json={"expected_version": 1, "confirmed": True},
    )
    assert forgotten.status_code == 200
    assert forgotten.json()["memory"]["status"] == "revoked"

    purged = client.post(
        f"/api/v1/memories/{memory_id}/purge",
        json={"expected_version": 1, "confirmed": True},
    )
    assert purged.status_code == 200
    assert purged.headers["cache-control"] == "no-store, private"
    assert purged.json()["memory"]["status"] == "purged"
    assert purged.json()["memory"]["content"] is None
    assert purged.json()["memory"]["content_sha256"] is None


def test_memory_errors_are_stable_and_never_echo_rejected_body() -> None:
    service = _MemoryApiService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]
    memory_id = str(service.record.item_id)

    rejected_body = "api_key = sk-abcdefghijklmnopqrstuvwxyz123456"
    rejected = client.post(
        "/api/v1/memories",
        json={
            "kind": "project_context",
            "content": rejected_body,
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "memory_content_rejected"
    assert rejected.json()["error"]["retryable"] is False
    assert rejected_body not in rejected.text

    conflict = client.post(
        f"/api/v1/memories/{memory_id}/correct",
        json={
            "expected_version": 1,
            "content": "新偏好。",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "memory_version_conflict"
    assert conflict.json()["error"]["retryable"] is True
    assert "traceback" not in conflict.text.casefold()


def test_selected_run_shape_reaches_service_as_exact_identity_only() -> None:
    service = _MemoryApiService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]
    item_id = service.record.item_id
    version_id = service.record.version_id
    assert version_id is not None

    response = client.post(
        f"/api/v1/conversations/{service.conversation_id}/runs",
        json={
            "goal": "使用我明确选择的偏好回答",
            "memory_mode": "selected",
            "selected_memories": [
                {
                    "item_id": str(item_id),
                    "version_id": str(version_id),
                }
            ],
        },
    )

    assert response.status_code == 202
    call = service.run_calls[-1]
    assert call["memory_mode"] is MemoryRunMode.SELECTED
    assert call["selected_memories"] == [
        {"item_id": item_id, "version_id": version_id}
    ]
    assert "content" not in str(call["selected_memories"])

    invalid = client.post(
        f"/api/v1/conversations/{service.conversation_id}/runs",
        json={
            "goal": "缺少精确选择",
            "memory_mode": "selected",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "request_validation_failed"
    assert len(service.run_calls) == 1


def test_run_memory_context_is_identity_only() -> None:
    service = _MemoryApiService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]

    response = client.get(f"/api/v1/runs/{service.run_id}/memory-context")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert "private" in response.headers["cache-control"]
    payload = response.json()
    assert payload["mode"] == "selected"
    assert payload["outcome"] == "loaded"
    assert payload["inputs"][0]["item_id"] == str(service.record.item_id)
    assert "content" not in payload["inputs"][0]
    assert "host_path" not in response.text


class _UnitOfWork(AbstractAsyncContextManager):
    def __init__(self, repositories: Any) -> None:
        self.repositories = repositories

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None


@pytest.mark.asyncio
async def test_scientific_memory_source_is_verified_against_authoritative_run() -> None:
    conversation_id, run_id = uuid4(), uuid4()
    message_id, artifact_id = uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id,
        conversation_id=conversation_id,
        status="completed",
        request_payload={"input_artifact_ids": []},
    )
    artifact = SimpleNamespace(id=artifact_id, run_id=run_id, kind="dataset")
    repositories = SimpleNamespace(
        runs=SimpleNamespace(get=AsyncMock(return_value=run)),
        events=SimpleNamespace(
            replay=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        event_type="message.completed",
                        payload={
                            "message_id": str(message_id),
                            "role": "assistant",
                        },
                    )
                ]
            )
        ),
        artifacts=SimpleNamespace(
            get_for_conversation=AsyncMock(return_value=artifact)
        ),
    )
    service = ApiService(
        lambda: _UnitOfWork(repositories),
        SimpleNamespace(event_log=None),
    )
    request = MemoryCreateRequest(
        kind="scientific_observation",
        content="历史 cluster 2 曾呈现 T-cell marker。",
        dataset_scope={"artifact_id": str(artifact_id)},
        source_conversation_id=conversation_id,
        source_run_id=run_id,
        source_message_ids=[message_id],
    )

    provenance = await service._verified_memory_provenance(request)  # noqa: SLF001

    assert provenance[0]["source_verified"] is True
    assert provenance[0]["artifact_id"] == str(artifact_id)
    artifact.kind = "marker_table"
    with pytest.raises(MemorySourceInvalidError):
        await service._verified_memory_provenance(request)  # noqa: SLF001
    artifact.kind = "dataset"
    repositories.artifacts.get_for_conversation.return_value = None
    with pytest.raises(MemorySourceInvalidError):
        await service._verified_memory_provenance(request)  # noqa: SLF001
