"""Thin API projections over the run coordinator and repositories."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO
from uuid import UUID

from omnicell_agent.persistence.models import Artifact, Conversation, Review, Run
from omnicell_agent.runs.coordinator import RunCoordinator, capability_task_id
from omnicell_agent.runs.event_log import RunEventLog, UnitOfWorkFactory
from omnicell_agent.runs.status import ReviewDecision, ReviewStatus

from .contracts import (
    ArtifactListResponse,
    ArtifactRead,
    ConversationListResponse,
    ConversationRead,
    ConversationStatus,
    EventReplayResponse,
    PageInfo,
    ReviewDecisionResponse,
    ReviewListResponse,
    ReviewRead,
    RunCancelResponse,
    RunCreateResponse,
    RunHistoryResponse,
    RunRead,
    RunResumeResponse,
)


class ApiResourceNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    stream: BinaryIO
    filename: str
    media_type: str | None
    size_bytes: int


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isascii() or not cursor.isdecimal():
        raise ValueError("cursor 必须是非负十进制 offset")
    value = int(cursor)
    if value < 0 or value > 1_000_000:
        raise ValueError("cursor 超出范围")
    return value


def _page(items: Sequence[Any], *, offset: int, limit: int) -> tuple[list[Any], PageInfo]:
    """Project a repository window fetched with ``limit + 1``."""

    selected = list(items[:limit])
    has_more = len(items) > limit
    return selected, PageInfo(
        next_cursor=str(offset + len(selected)) if has_more else None,
        has_more=has_more,
    )


def project_conversation(
    conversation: Conversation,
    *,
    dataset_artifact_id: UUID | None = None,
) -> ConversationRead:
    return ConversationRead(
        conversation_id=conversation.id,
        title=conversation.title,
        status=ConversationStatus(conversation.status),
        dataset_artifact_id=dataset_artifact_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def project_run(run: Run) -> RunRead:
    return RunRead(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        last_sequence=run.next_event_sequence,
        created_at=run.created_at,
        started_at=run.started_at,
        updated_at=run.updated_at,
        completed_at=run.finished_at,
        error_summary=run.error_summary,
    )


def project_review(review: Review) -> ReviewRead:
    decision_value = review.decision_payload.get("decision")
    return ReviewRead(
        review_id=review.id,
        conversation_id=review.conversation_id,
        run_id=review.run_id,
        task_id=capability_task_id(review.run_id, review.tool_call_id),
        status=review.status,
        prompt=f"确认是否执行 capability: {review.capability_name}",
        decision=ReviewDecision(decision_value) if decision_value else None,
        comment=review.decision_payload.get("comment"),
        requested_at=review.created_at,
        resolved_at=review.decided_at,
    )


def _safe_public_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    safe: dict[str, str | int | float | bool | None] = {}
    forbidden = {
        "apikey",
        "checkpoint",
        "dsn",
        "hostpath",
        "ormrow",
        "password",
        "providersecret",
        "rawcheckpoint",
        "secret",
        "uri",
        "workspaceuri",
    }
    for key, value in metadata.items():
        normalized = "".join(character for character in key.casefold() if character.isalnum())
        if not key or len(key) > 128 or normalized in forbidden:
            continue
        if value is None or isinstance(value, (str, bool)):
            safe[key] = value[:2_000] if isinstance(value, str) else value
        elif isinstance(value, int) and abs(value) <= 10**18:
            safe[key] = value
        elif isinstance(value, float) and math.isfinite(value) and abs(value) <= 10**18:
            safe[key] = value
        if len(safe) >= 50:
            break
    return safe


def project_artifact(artifact: Artifact) -> ArtifactRead:
    if artifact.size_bytes is None or artifact.sha256 is None:
        raise ValueError("artifact 缺少公共 identity 元数据")
    return ArtifactRead(
        artifact_id=artifact.id,
        conversation_id=artifact.conversation_id,
        run_id=artifact.run_id,
        source_event_id=artifact.source_event_id,
        kind=artifact.kind,
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata=_safe_public_metadata(artifact.artifact_metadata),
        created_at=artifact.created_at,
    )


class ApiService:
    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        coordinator: RunCoordinator,
        *,
        event_log: RunEventLog | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self.coordinator = coordinator
        self.event_log = event_log or coordinator.event_log

    async def create_conversation(self, *, title: str | None) -> ConversationRead:
        return project_conversation(
            await self.coordinator.create_conversation(title=title)
        )

    async def list_conversations(
        self,
        *,
        cursor: str | None,
        limit: int,
        status: ConversationStatus | None,
    ) -> ConversationListResponse:
        offset = _offset(cursor)
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            rows = list(
                await repositories.conversations.list(
                    status=status.value if status is not None else None,
                    offset=offset,
                    limit=limit + 1,
                )
            )
        selected, page = _page(rows, offset=offset, limit=limit)
        dataset_ids: dict[UUID, UUID] = {}
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            for row in selected:
                if row.dataset_uri is None:
                    continue
                artifact = await repositories.artifacts.get_by_uri_for_conversation(
                    row.dataset_uri,
                    conversation_id=row.id,
                )
                if artifact is not None:
                    dataset_ids[row.id] = artifact.id
        return ConversationListResponse(
            items=[
                project_conversation(
                    row,
                    dataset_artifact_id=dataset_ids.get(row.id),
                )
                for row in selected
            ],
            page=page,
        )

    async def get_conversation(self, conversation_id: UUID) -> ConversationRead:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            row = await repositories.conversations.get(conversation_id)
            dataset = (
                await repositories.artifacts.get_by_uri_for_conversation(
                    row.dataset_uri,
                    conversation_id=conversation_id,
                )
                if row is not None and row.dataset_uri is not None
                else None
            )
        if row is None:
            raise ApiResourceNotFoundError(str(conversation_id))
        return project_conversation(
            row,
            dataset_artifact_id=dataset.id if dataset is not None else None,
        )

    async def create_run(
        self,
        *,
        conversation_id: UUID,
        goal: str,
        input_artifact_ids: list[UUID],
        request_key: str | None,
    ) -> RunCreateResponse:
        run = await self.coordinator.submit_run(
            conversation_id=conversation_id,
            goal=goal,
            input_artifact_ids=input_artifact_ids,
            request_key=request_key,
        )
        return RunCreateResponse(run=await self.get_run(run.id))

    async def get_run(self, run_id: UUID) -> RunRead:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            row = await repositories.runs.get(run_id)
        if row is None:
            raise ApiResourceNotFoundError(str(run_id))
        return project_run(row)

    async def history(
        self,
        conversation_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> RunHistoryResponse:
        offset = _offset(cursor)
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            if await repositories.conversations.get(conversation_id) is None:
                raise ApiResourceNotFoundError(str(conversation_id))
            rows = list(
                await repositories.runs.list_for_conversation(
                    conversation_id,
                    offset=offset,
                    limit=limit + 1,
                )
            )
        selected, page = _page(rows, offset=offset, limit=limit)
        return RunHistoryResponse(
            conversation_id=conversation_id,
            order="newest_first",
            items=[project_run(row) for row in selected],
            page=page,
        )

    async def replay(self, run_id: UUID, *, after_sequence: int, limit: int) -> EventReplayResponse:
        page = await self.event_log.replay(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        run = await self.get_run(run_id)
        return EventReplayResponse(
            conversation_id=run.conversation_id,
            run_id=run_id,
            events=list(page.events),
            next_sequence=page.next_sequence,
            has_more=page.has_more,
        )

    async def cancel_run(self, run_id: UUID, *, reason: str | None) -> RunCancelResponse:
        accepted = await self.coordinator.request_cancel(run_id, reason=reason)
        return RunCancelResponse(run=await self.get_run(run_id), accepted=accepted)

    async def resume_run(self, run_id: UUID, *, review_id: UUID | None) -> RunResumeResponse:
        accepted = await self.coordinator.resume_run(run_id, review_id=review_id)
        return RunResumeResponse(run=await self.get_run(run_id), accepted=accepted)

    async def list_reviews(
        self,
        conversation_id: UUID,
        *,
        run_id: UUID | None,
        status: ReviewStatus | None,
        cursor: str | None,
        limit: int,
    ) -> ReviewListResponse:
        offset = _offset(cursor)
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            if await repositories.conversations.get(conversation_id) is None:
                raise ApiResourceNotFoundError(str(conversation_id))
            if (
                run_id is not None
                and await repositories.runs.get_for_conversation(
                    run_id,
                    conversation_id=conversation_id,
                )
                is None
            ):
                raise ApiResourceNotFoundError(str(run_id))
            if run_id is None:
                rows = list(
                    await repositories.reviews.list_for_conversation(
                        conversation_id,
                        status=status,
                        offset=offset,
                        limit=limit + 1,
                    )
                )
            else:
                rows = list(
                    await repositories.reviews.list_for_run(
                        run_id,
                        conversation_id=conversation_id,
                        status=status,
                        offset=offset,
                        limit=limit + 1,
                    )
                )
        selected, page = _page(rows, offset=offset, limit=limit)
        return ReviewListResponse(
            conversation_id=conversation_id,
            items=[project_review(row) for row in selected],
            page=page,
        )

    async def decide_review(
        self,
        review_id: UUID,
        *,
        decision: ReviewDecision,
        comment: str | None,
    ) -> ReviewDecisionResponse:
        review = await self.coordinator.resolve_review(
            review_id,
            decision=decision,
            comment=comment,
        )
        return ReviewDecisionResponse(
            review=project_review(review),
            run=await self.get_run(review.run_id),
        )

    async def list_artifacts(
        self,
        conversation_id: UUID,
        *,
        run_id: UUID | None,
        kind: str | None,
        cursor: str | None,
        limit: int,
    ) -> ArtifactListResponse:
        offset = _offset(cursor)
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            if await repositories.conversations.get(conversation_id) is None:
                raise ApiResourceNotFoundError(str(conversation_id))
            if (
                run_id is not None
                and await repositories.runs.get_for_conversation(
                    run_id,
                    conversation_id=conversation_id,
                )
                is None
            ):
                raise ApiResourceNotFoundError(str(run_id))
            if run_id is None:
                rows = list(
                    await repositories.artifacts.list_for_conversation(
                        conversation_id,
                        kind=kind,
                        offset=offset,
                        limit=limit + 1,
                    )
                )
            else:
                rows = list(
                    await repositories.artifacts.list_for_run(
                        run_id,
                        conversation_id=conversation_id,
                        kind=kind,
                        offset=offset,
                        limit=limit + 1,
                    )
                )
        selected, page = _page(rows, offset=offset, limit=limit)
        return ArtifactListResponse(
            conversation_id=conversation_id,
            items=[project_artifact(row) for row in selected],
            page=page,
        )

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRead:
        async with self._unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            row = await repositories.artifacts.get(artifact_id)
        if row is None:
            raise ApiResourceNotFoundError(str(artifact_id))
        return project_artifact(row)

    async def upload_artifact(
        self,
        conversation_id: UUID,
        *,
        source: BinaryIO,
        filename: str | None,
        kind: str,
        media_type: str | None,
    ) -> ArtifactRead:
        artifact = await self.coordinator.import_artifact(
            conversation_id,
            source=source,
            filename=filename,
            kind=kind,
            media_type=media_type,
        )
        return project_artifact(artifact)

    async def get_artifact_content(self, artifact_id: UUID) -> ArtifactContent:
        artifact, stream = await self.coordinator.open_artifact(artifact_id)
        try:
            public = project_artifact(artifact)
            filename = public.metadata.get("filename")
            return ArtifactContent(
                stream=stream,
                filename=filename if isinstance(filename, str) else f"{artifact.id}",
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes or 0,
            )
        except BaseException:
            stream.close()
            raise


__all__ = [
    "ApiResourceNotFoundError",
    "ApiService",
    "ArtifactContent",
    "project_artifact",
    "project_conversation",
    "project_review",
    "project_run",
]
