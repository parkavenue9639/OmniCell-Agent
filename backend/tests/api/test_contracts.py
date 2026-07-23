from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omnicell_agent.api.contracts import (
    ArtifactListResponse,
    ArtifactRead,
    ConversationCreateRequest,
    ErrorEnvelope,
    EventReplayRequest,
    EventReplayResponse,
    PageInfo,
    ReviewDecisionRequest,
    RunCancelRequest,
    RunCreateRequest,
    RunHistoryResponse,
    RunRead,
    RunResumeRequest,
)
from omnicell_agent.api.service import project_review
from omnicell_agent.persistence.models import Review
from omnicell_agent.runs.coordinator import capability_task_id
from omnicell_agent.runs.events import (
    AssistantDeltaEvent,
    AssistantDeltaPayload,
    EventType,
    MessageCompletedPayload,
    PERSISTED_EVENT_ADAPTER,
    ReviewResolvedPayload,
    RunCompletedEvent,
    RunCompletedPayload,
    TRANSIENT_EVENT_ADAPTER,
    validate_persisted_event,
    validate_transient_event,
)
from omnicell_agent.runs.status import ReviewDecision, ReviewStatus, RunStatus

NOW = datetime(2026, 7, 23, 10, 30, tzinfo=UTC)


def _event_envelope(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": str(uuid4()),
        "schema_version": 1,
        "conversation_id": str(uuid4()),
        "run_id": str(uuid4()),
        "sequence": 9_007_199_254_740_993,
        "type": event_type,
        "occurred_at": NOW.isoformat(),
        "payload": payload,
    }


def _run_read() -> RunRead:
    return RunRead(
        run_id=uuid4(),
        conversation_id=uuid4(),
        status=RunStatus.RUNNING,
        last_sequence=0,
        created_at=NOW,
        started_at=NOW,
        updated_at=NOW,
    )


def _artifact_read() -> ArtifactRead:
    return ArtifactRead(
        artifact_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
        kind="annotation_report",
        media_type="application/json",
        size_bytes=42,
        sha256="a" * 64,
        metadata={"cluster_count": 7},
        created_at=NOW,
    )


def test_review_snapshot_links_to_deterministic_capability_task() -> None:
    run_id = uuid4()
    tool_call_id = "reviewed-capability"
    review = Review(
        id=uuid4(),
        conversation_id=uuid4(),
        run_id=run_id,
        capability_name="single_cell_analysis",
        tool_call_id=tool_call_id,
        checkpoint_thread_id=str(run_id),
        checkpoint_ns="",
        checkpoint_id="checkpoint-1",
        status=ReviewStatus.PENDING.value,
        request_payload={},
        decision_payload={},
        created_at=NOW,
        updated_at=NOW,
    )

    projected = project_review(review)

    assert projected.task_id == capability_task_id(run_id, tool_call_id)


def test_persisted_event_uses_discriminator_and_decimal_sequence() -> None:
    event = validate_persisted_event(
        _event_envelope(
            EventType.RUN_COMPLETED,
            {"status": RunStatus.COMPLETED, "artifact_ids": []},
        )
    )

    assert isinstance(event, RunCompletedEvent)
    assert event.payload == RunCompletedPayload(status=RunStatus.COMPLETED)
    serialized = event.model_dump(mode="json")
    assert serialized["sequence"] == "9007199254740993"
    assert serialized["type"] == "run.completed"
    assert serialized["schema_version"] == 1


def test_serialized_event_wire_schema_requires_version_and_discriminator() -> None:
    for adapter in (PERSISTED_EVENT_ADAPTER, TRANSIENT_EVENT_ADAPTER):
        schema = adapter.json_schema(mode="serialization")
        for definition in schema["$defs"].values():
            properties = definition.get("properties", {})
            if "schema_version" not in properties or "type" not in properties:
                continue
            required = set(definition.get("required", []))
            assert {"schema_version", "type"} <= required


def test_persisted_event_rejects_wrong_payload_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        validate_persisted_event(
            _event_envelope(
                EventType.RUN_COMPLETED,
                {"status": RunStatus.FAILED, "host_path": "/tmp/internal"},
            )
        )

    envelope = _event_envelope(EventType.RUN_COMPLETED, {"status": "completed"})
    envelope["sequence"] = "01"
    with pytest.raises(ValidationError):
        validate_persisted_event(envelope)


def test_event_types_cover_required_persisted_and_transient_facts() -> None:
    assert {item.value for item in EventType} == {
        "run.created",
        "run.started",
        "agent.turn_started",
        "message.completed",
        "task.created",
        "task.updated",
        "skill.load_started",
        "skill.load_completed",
        "skill.load_failed",
        "capability.started",
        "capability.completed",
        "capability.failed",
        "capability.retrying",
        "runtime.command_started",
        "runtime.output",
        "runtime.command_completed",
        "artifact.created",
        "review.requested",
        "review.resolved",
        "budget.exhausted",
        "run.cancel_requested",
        "run.interrupted",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "assistant.delta",
        "capability.progress",
    }

    persisted_mapping = PERSISTED_EVENT_ADAPTER.json_schema()["discriminator"][
        "mapping"
    ]
    transient_mapping = TRANSIENT_EVENT_ADAPTER.json_schema()["discriminator"][
        "mapping"
    ]
    assert set(persisted_mapping) == {
        item.value
        for item in EventType
        if item is not EventType.ASSISTANT_DELTA
    }
    assert set(transient_mapping) == {EventType.ASSISTANT_DELTA.value}


def test_event_payloads_are_bounded() -> None:
    with pytest.raises(ValidationError):
        MessageCompletedPayload(
            message_id=uuid4(),
            role="assistant",
            content="x" * 20_001,
        )

    with pytest.raises(ValidationError):
        AssistantDeltaPayload(
            message_id=uuid4(),
            index=0,
            delta="x" * 4_097,
        )


def test_review_resolution_status_matches_decision() -> None:
    cancelled = ReviewResolvedPayload(
        review_id=uuid4(),
        status=ReviewStatus.CANCELLED,
    )
    assert cancelled.decision is None

    with pytest.raises(ValidationError):
        ReviewResolvedPayload(
            review_id=uuid4(),
            status=ReviewStatus.APPROVED,
            decision=ReviewDecision.REJECT,
        )


def test_transient_events_have_no_persistent_identity_or_sequence() -> None:
    event = validate_transient_event(
        {
            "schema_version": 1,
            "conversation_id": str(uuid4()),
            "run_id": str(uuid4()),
            "type": EventType.ASSISTANT_DELTA,
            "occurred_at": NOW.isoformat(),
            "payload": {
                "message_id": str(uuid4()),
                "index": 0,
                "delta": "hello",
            },
        }
    )

    assert isinstance(event, AssistantDeltaEvent)
    assert "event_id" not in type(event).model_fields
    assert "sequence" not in type(event).model_fields
    assert "event_id" not in event.model_dump(mode="json")
    assert "sequence" not in event.model_dump(mode="json")

    with pytest.raises(ValidationError):
        AssistantDeltaEvent(
            event_id=uuid4(),
            sequence=1,
            conversation_id=uuid4(),
            run_id=uuid4(),
            occurred_at=NOW,
            payload=AssistantDeltaPayload(
                message_id=uuid4(), index=0, delta="hello"
            ),
        )


def test_api_requests_forbid_extras_and_bound_lengths_and_counts() -> None:
    with pytest.raises(ValidationError):
        ConversationCreateRequest(title="analysis", checkpoint={})

    with pytest.raises(ValidationError):
        ConversationCreateRequest(
            title="analysis",
            dataset_artifact_id=uuid4(),
        )

    with pytest.raises(ValidationError):
        RunCreateRequest(goal="x" * 20_001)

    with pytest.raises(ValidationError):
        RunCreateRequest(goal="analysis", input_artifact_ids=[uuid4()] * 101)

    with pytest.raises(ValidationError):
        EventReplayRequest(after_sequence=-1, limit=1)


def test_only_run_creation_exposes_request_key() -> None:
    assert "request_key" in RunCreateRequest.model_fields
    for request_type, payload in (
        (RunCancelRequest, {"request_key": "cancel-1"}),
        (RunResumeRequest, {"request_key": "resume-1"}),
        (
            ReviewDecisionRequest,
            {"decision": ReviewDecision.APPROVE, "request_key": "review-1"},
        ),
    ):
        assert "request_key" not in request_type.model_fields
        with pytest.raises(ValidationError):
            request_type.model_validate(payload)


def test_api_results_are_bounded_and_use_public_projections() -> None:
    artifact = _artifact_read()
    response = ArtifactListResponse(
        conversation_id=artifact.conversation_id,
        items=[artifact],
        page=PageInfo(has_more=False),
    )

    serialized = response.model_dump(mode="json")
    public_artifact = serialized["items"][0]
    assert public_artifact["artifact_id"] == str(artifact.artifact_id)
    for internal_field in (
        "host_path",
        "workspace_uri",
        "raw_checkpoint",
        "provider_secret",
        "orm_row",
    ):
        assert internal_field not in public_artifact

    with pytest.raises(ValidationError):
        ArtifactRead(**artifact.model_dump(), host_path="/tmp/internal")

    with pytest.raises(ValidationError):
        ArtifactRead(
            **artifact.model_dump(exclude={"metadata"}),
            metadata={"provider_secret": "do-not-return"},
        )

    with pytest.raises(ValidationError):
        ArtifactListResponse(
            conversation_id=artifact.conversation_id,
            items=[artifact] * 101,
            page=PageInfo(has_more=False),
        )


def test_replay_and_error_envelopes_are_versioned_and_closed() -> None:
    persisted = validate_persisted_event(
        _event_envelope(
            EventType.RUN_COMPLETED,
            {"status": RunStatus.COMPLETED},
        )
    )
    replay = EventReplayResponse(
        conversation_id=persisted.conversation_id,
        run_id=persisted.run_id,
        events=[persisted],
        next_sequence=persisted.sequence,
        has_more=False,
    )
    assert replay.model_dump(mode="json")["next_sequence"] == "9007199254740993"

    error = ErrorEnvelope(
        request_id=uuid4(),
        error={"code": "not_found", "message": "run 不存在"},
    )
    assert error.schema_version == 1
    with pytest.raises(ValidationError):
        ErrorEnvelope(
            request_id=uuid4(),
            error={"code": "not_found", "message": "run 不存在", "trace": "x"},
        )


def test_run_projection_uses_shared_status_and_string_cursor() -> None:
    run = _run_read()

    assert run.status is RunStatus.RUNNING
    assert run.model_dump(mode="json")["last_sequence"] == "0"
    with pytest.raises(ValidationError):
        RunRead(**run.model_dump(), checkpoint_id=uuid4())


def test_run_history_contract_freezes_newest_first_order() -> None:
    run = _run_read()
    response = RunHistoryResponse(
        conversation_id=run.conversation_id,
        order="newest_first",
        items=[run],
        page=PageInfo(has_more=False),
    )

    assert response.model_dump(mode="json")["order"] == "newest_first"
    with pytest.raises(ValidationError):
        RunHistoryResponse(
            conversation_id=run.conversation_id,
            items=[run],
            page=PageInfo(has_more=False),
        )
