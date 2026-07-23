from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnicell_agent.api.contracts import ConversationStatus
from omnicell_agent.api.service import ApiResourceNotFoundError, ApiService
from omnicell_agent.persistence.models import Conversation, Run
from omnicell_agent.runs.status import ReviewStatus


NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _UnitOfWork(AbstractAsyncContextManager):
    def __init__(self, repositories: SimpleNamespace) -> None:
        self.repositories = repositories

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


def _service(repositories: SimpleNamespace) -> ApiService:
    return ApiService(
        lambda: _UnitOfWork(repositories),
        SimpleNamespace(event_log=object()),
    )


def _conversation(value: int, *, status: str = "active") -> Conversation:
    return Conversation(
        id=UUID(int=value),
        title=f"conversation-{value}",
        status=status,
        workspace_uri=f"workspace://{value}",
        dataset_uri=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _run(value: int, conversation_id: UUID) -> Run:
    return Run(
        id=UUID(int=value),
        conversation_id=conversation_id,
        request_key=None,
        status="completed",
        request_payload={},
        next_event_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_conversation_and_history_pages_push_offset_and_limit_plus_one() -> None:
    conversation = _conversation(1)
    conversations = SimpleNamespace(
        get=AsyncMock(return_value=conversation),
        list=AsyncMock(return_value=[_conversation(3), _conversation(2), conversation]),
    )
    runs = SimpleNamespace(
        list_for_conversation=AsyncMock(
            return_value=[_run(3, conversation.id), _run(2, conversation.id)]
        )
    )
    repositories = SimpleNamespace(
        conversations=conversations,
        runs=runs,
        artifacts=SimpleNamespace(get_by_uri_for_conversation=AsyncMock()),
    )
    service = _service(repositories)

    conversation_page = await service.list_conversations(
        cursor="17",
        limit=2,
        status=ConversationStatus.ACTIVE,
    )
    conversations.list.assert_awaited_once_with(
        status="active",
        offset=17,
        limit=3,
    )
    assert [item.conversation_id for item in conversation_page.items] == [
        UUID(int=3),
        UUID(int=2),
    ]
    assert conversation_page.page.has_more is True
    assert conversation_page.page.next_cursor == "19"

    history_page = await service.history(conversation.id, cursor="20", limit=1)
    runs.list_for_conversation.assert_awaited_once_with(
        conversation.id,
        offset=20,
        limit=2,
    )
    assert [item.run_id for item in history_page.items] == [UUID(int=3)]
    assert history_page.page.has_more is True
    assert history_page.page.next_cursor == "21"


@pytest.mark.asyncio
async def test_nested_review_and_artifact_pages_push_filters_and_verify_run_owner() -> None:
    conversation = _conversation(10)
    run = _run(20, conversation.id)
    conversations = SimpleNamespace(get=AsyncMock(return_value=conversation))
    runs = SimpleNamespace(get_for_conversation=AsyncMock(return_value=run))
    reviews = SimpleNamespace(
        list_for_conversation=AsyncMock(return_value=[]),
        list_for_run=AsyncMock(return_value=[]),
    )
    artifacts = SimpleNamespace(
        list_for_conversation=AsyncMock(return_value=[]),
        list_for_run=AsyncMock(return_value=[]),
    )
    service = _service(
        SimpleNamespace(
            conversations=conversations,
            runs=runs,
            reviews=reviews,
            artifacts=artifacts,
        )
    )

    review_page = await service.list_reviews(
        conversation.id,
        run_id=run.id,
        status=ReviewStatus.PENDING,
        cursor="8",
        limit=4,
    )
    assert review_page.items == []
    runs.get_for_conversation.assert_awaited_once_with(
        run.id,
        conversation_id=conversation.id,
    )
    reviews.list_for_run.assert_awaited_once_with(
        run.id,
        conversation_id=conversation.id,
        status=ReviewStatus.PENDING,
        offset=8,
        limit=5,
    )

    artifact_page = await service.list_artifacts(
        conversation.id,
        run_id=run.id,
        kind="report",
        cursor="12",
        limit=6,
    )
    assert artifact_page.items == []
    artifacts.list_for_run.assert_awaited_once_with(
        run.id,
        conversation_id=conversation.id,
        kind="report",
        offset=12,
        limit=7,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["reviews", "artifacts"])
async def test_nested_lists_fail_closed_for_missing_or_cross_conversation_run(
    resource: str,
) -> None:
    conversation = _conversation(30)
    conversations = SimpleNamespace(get=AsyncMock(return_value=conversation))
    runs = SimpleNamespace(get_for_conversation=AsyncMock(return_value=None))
    reviews = SimpleNamespace(list_for_run=AsyncMock())
    artifacts = SimpleNamespace(list_for_run=AsyncMock())
    service = _service(
        SimpleNamespace(
            conversations=conversations,
            runs=runs,
            reviews=reviews,
            artifacts=artifacts,
        )
    )
    foreign_run_id = UUID(int=31)

    with pytest.raises(ApiResourceNotFoundError, match=str(foreign_run_id)):
        if resource == "reviews":
            await service.list_reviews(
                conversation.id,
                run_id=foreign_run_id,
                status=None,
                cursor=None,
                limit=10,
            )
        else:
            await service.list_artifacts(
                conversation.id,
                run_id=foreign_run_id,
                kind=None,
                cursor=None,
                limit=10,
            )

    reviews.list_for_run.assert_not_awaited()
    artifacts.list_for_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["history", "reviews", "artifacts"])
async def test_nested_lists_report_missing_conversation(resource: str) -> None:
    conversation_id = UUID(int=40)
    conversations = SimpleNamespace(get=AsyncMock(return_value=None))
    repositories = SimpleNamespace(
        conversations=conversations,
        runs=SimpleNamespace(
            list_for_conversation=AsyncMock(),
            get_for_conversation=AsyncMock(),
        ),
        reviews=SimpleNamespace(list_for_conversation=AsyncMock()),
        artifacts=SimpleNamespace(list_for_conversation=AsyncMock()),
    )
    service = _service(repositories)

    with pytest.raises(ApiResourceNotFoundError, match=str(conversation_id)):
        if resource == "history":
            await service.history(conversation_id, cursor=None, limit=10)
        elif resource == "reviews":
            await service.list_reviews(
                conversation_id,
                run_id=None,
                status=None,
                cursor=None,
                limit=10,
            )
        else:
            await service.list_artifacts(
                conversation_id,
                run_id=None,
                kind=None,
                cursor=None,
                limit=10,
            )
