from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import insert

from omnicell_agent.api.service import ApiService
from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.models import Conversation


TEST_DSN = os.environ.get("OMNICELL_TEST_POSTGRES_DSN", "").strip()

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="设置 OMNICELL_TEST_POSTGRES_DSN 后运行真实 PostgreSQL 集成测试",
    ),
]


@pytest_asyncio.fixture
async def pagination_runtime():
    suffix = uuid.uuid4().hex[:10]
    settings = PostgresSettings(
        dsn=TEST_DSN,
        app_schema=f"omnicell_page_test_{suffix}",
        checkpoint_schema=f"omnicell_page_checkpoint_test_{suffix}",
        pool_min_size=1,
        pool_max_size=2,
    )
    runtime = PersistenceRuntime(settings)
    await runtime.initialize_schemas()
    await runtime.open()
    try:
        yield runtime
    finally:
        await runtime.close()
        async with await psycopg.AsyncConnection.connect(
            settings.psycopg_conninfo,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            for schema_name in (settings.checkpoint_schema, settings.app_schema):
                await connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )


@pytest.mark.asyncio
async def test_conversation_page_reaches_row_5001_without_false_has_more(
    pagination_runtime: PersistenceRuntime,
) -> None:
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "id": uuid.UUID(int=index),
            "title": f"conversation-{index}",
            "status": "active",
            "workspace_uri": f"workspace://pagination/{index}",
            "created_at": base_time + timedelta(microseconds=index),
            "updated_at": base_time + timedelta(microseconds=index),
        }
        for index in range(1, 5_003)
    ]
    async with pagination_runtime.unit_of_work() as unit_of_work:
        await unit_of_work.session.execute(insert(Conversation), rows)

    service = ApiService(
        pagination_runtime.unit_of_work,
        SimpleNamespace(event_log=object()),
    )
    row_5001 = await service.list_conversations(
        cursor="5000",
        limit=1,
        status=None,
    )
    assert [item.conversation_id for item in row_5001.items] == [uuid.UUID(int=2)]
    assert row_5001.page.has_more is True
    assert row_5001.page.next_cursor == "5001"

    final_row = await service.list_conversations(
        cursor=row_5001.page.next_cursor,
        limit=1,
        status=None,
    )
    assert [item.conversation_id for item in final_row.items] == [uuid.UUID(int=1)]
    assert final_row.page.has_more is False
    assert final_row.page.next_cursor is None
