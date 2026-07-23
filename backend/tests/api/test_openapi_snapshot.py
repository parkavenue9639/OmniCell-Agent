from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from omnicell_agent.api.app import create_app
from omnicell_agent.api.contracts import ErrorEnvelope
from omnicell_agent.api.service import ApiResourceNotFoundError
from omnicell_agent.runs.coordinator import (
    ArtifactUploadTooLargeError,
    ReviewConflictError,
)
from omnicell_agent.runs.events import PERSISTED_EVENT_ADAPTER, TRANSIENT_EVENT_ADAPTER


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _assert_error_envelope(response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    envelope = ErrorEnvelope.model_validate(response.json())
    assert envelope.error.code == code


class _ErrorService:
    event_log = None

    async def get_run(self, run_id):
        raise ApiResourceNotFoundError(str(run_id))

    async def decide_review(self, review_id, *, decision, comment):
        raise ReviewConflictError(f"review {review_id} 已解决")

    async def resume_run(self, run_id, *, review_id):
        del review_id
        raise ReviewConflictError(f"run {run_id} 仍有 pending review")

    async def upload_artifact(
        self,
        conversation_id,
        *,
        source,
        filename,
        kind,
        media_type,
    ):
        raise ArtifactUploadTooLargeError(f"artifact {filename} 超过上传上限")


def test_openapi_snapshot_matches_offline_application_schema() -> None:
    snapshot = json.loads(
        (REPOSITORY_ROOT / "contracts" / "openapi" / "v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot == create_app().openapi()
    stream = snapshot["paths"]["/api/v1/runs/{run_id}/events/stream"]["get"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]
    operation_ids = [
        operation["operationId"]
        for path in snapshot["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_openapi_declares_the_runtime_error_envelope_for_every_operation() -> None:
    schema = create_app().openapi()
    expected_errors = {
        ("/api/v1/health/live", "get"): {422},
        ("/api/v1/health/ready", "get"): {422},
        ("/api/v1/conversations", "post"): {422},
        ("/api/v1/conversations", "get"): {400, 422},
        ("/api/v1/conversations/{conversation_id}", "get"): {404, 422},
        ("/api/v1/conversations/{conversation_id}/history", "get"): {
            400,
            404,
            422,
        },
        ("/api/v1/conversations/{conversation_id}/runs", "post"): {
            400,
            404,
            409,
            422,
        },
        ("/api/v1/runs/{run_id}", "get"): {404, 422},
        ("/api/v1/runs/{run_id}/events", "get"): {404, 422},
        ("/api/v1/runs/{run_id}/events/stream", "get"): {400, 404, 422},
        ("/api/v1/runs/{run_id}/cancel", "post"): {404, 422},
        ("/api/v1/runs/{run_id}/resume", "post"): {404, 409, 422},
        ("/api/v1/conversations/{conversation_id}/reviews", "get"): {400, 422},
        ("/api/v1/reviews/{review_id}/decision", "post"): {404, 409, 422},
        ("/api/v1/conversations/{conversation_id}/artifacts", "get"): {400, 422},
        ("/api/v1/conversations/{conversation_id}/artifacts", "post"): {
            400,
            404,
            413,
            422,
        },
        ("/api/v1/artifacts/{artifact_id}", "get"): {404, 422},
        ("/api/v1/artifacts/{artifact_id}/content", "get"): {400, 404, 422},
    }
    assert set(expected_errors) == {
        (path, method)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    for (path, method), status_codes in expected_errors.items():
        responses = schema["paths"][path][method]["responses"]
        for status_code in status_codes:
            assert responses[str(status_code)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorEnvelope"
            }

    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert schema["paths"]["/api/v1/health/ready"]["get"]["responses"]["503"][
        "content"
    ]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadinessResponse"
    }


def test_runtime_uses_error_envelope_for_all_public_error_statuses() -> None:
    client = TestClient(create_app(_ErrorService()))
    conversation_id = uuid4()
    run_id = uuid4()
    review_id = uuid4()

    validation = client.get("/api/v1/runs/not-a-uuid")
    _assert_error_envelope(
        validation,
        status_code=422,
        code="request_validation_failed",
    )

    invalid = client.post(
        f"/api/v1/conversations/{conversation_id}/runs",
        headers={"Idempotency-Key": "header-key"},
        json={"goal": "分析", "request_key": "body-key"},
    )
    _assert_error_envelope(invalid, status_code=400, code="invalid_request")

    missing = client.get(f"/api/v1/runs/{run_id}")
    _assert_error_envelope(missing, status_code=404, code="resource_not_found")

    conflict = client.post(
        f"/api/v1/reviews/{review_id}/decision",
        json={"decision": "approve"},
    )
    _assert_error_envelope(conflict, status_code=409, code="lifecycle_conflict")

    resume_conflict = client.post(
        f"/api/v1/runs/{run_id}/resume",
        json={"review_id": str(review_id)},
    )
    _assert_error_envelope(
        resume_conflict,
        status_code=409,
        code="lifecycle_conflict",
    )

    too_large = client.post(
        f"/api/v1/conversations/{conversation_id}/artifacts",
        data={"kind": "dataset"},
        files={"file": ("large.h5ad", b"content", "application/octet-stream")},
    )
    _assert_error_envelope(too_large, status_code=413, code="artifact_too_large")

    framework_missing = client.get("/api/v1/not-a-real-resource")
    _assert_error_envelope(
        framework_missing,
        status_code=404,
        code="resource_not_found",
    )


def test_event_snapshot_matches_pydantic_discriminated_unions() -> None:
    snapshot = json.loads(
        (REPOSITORY_ROOT / "contracts" / "events" / "v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["persisted"] == PERSISTED_EVENT_ADAPTER.json_schema(
        mode="serialization"
    )
    assert snapshot["transient"] == TRANSIENT_EVENT_ADAPTER.json_schema(
        mode="serialization"
    )
    assert snapshot["persisted"]["discriminator"]["propertyName"] == "type"
    assert snapshot["transient"]["discriminator"]["propertyName"] == "type"
