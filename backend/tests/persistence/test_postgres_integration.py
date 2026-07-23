from __future__ import annotations

import asyncio
import datetime as dt
import operator
import os
import uuid
from dataclasses import replace
from typing import Annotated, TypedDict

import psycopg
import pytest
import pytest_asyncio
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy.exc import IntegrityError

from omnicell_agent.persistence.bootstrap import PersistenceRuntime
from omnicell_agent.persistence.checkpointer import (
    CheckpointRetention,
    checkpoint_namespace,
    checkpoint_thread_id,
)
from omnicell_agent.persistence.config import PostgresSettings
from omnicell_agent.persistence.guards import PersistencePayloadTooLargeError
from omnicell_agent.persistence.models import (
    Artifact,
    CheckpointAnchor,
    Conversation,
    Review,
    Run,
    RunTask,
)


TEST_DSN = os.environ.get("OMNICELL_TEST_POSTGRES_DSN", "").strip()

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="设置 OMNICELL_TEST_POSTGRES_DSN 后运行真实 PostgreSQL 集成测试",
    ),
]


@pytest_asyncio.fixture
async def postgres_settings():
    suffix = uuid.uuid4().hex[:10]
    settings = PostgresSettings(
        dsn=TEST_DSN,
        app_schema=f"omnicell_app_test_{suffix}",
        checkpoint_schema=f"omnicell_checkpoint_test_{suffix}",
        pool_min_size=1,
        pool_max_size=8,
    )
    try:
        yield settings
    finally:
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


class CounterState(TypedDict):
    value: int


class FanoutState(TypedDict):
    values: list[int]
    results: Annotated[list[int], operator.add]


class FanoutWorkerState(TypedDict):
    value: int


def _counter_graph(saver):
    builder = StateGraph(CounterState)
    builder.add_node("increment", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    return builder.compile(checkpointer=saver)


def _fanout_graph(saver):
    def distribute(state: FanoutState) -> list[Send]:
        return [Send("worker", {"value": value}) for value in state["values"]]

    def worker(state: FanoutWorkerState) -> dict[str, list[int]]:
        return {"results": [state["value"] * state["value"]]}

    builder = StateGraph(FanoutState)
    builder.add_node("worker", worker)
    builder.add_conditional_edges(START, distribute, ["worker"])
    builder.add_edge("worker", END)
    return builder.compile(checkpointer=saver)


async def _put_namespaced_checkpoints(saver, *, thread_id: str, namespace: str, values: list[int]):
    """Exercise the saver namespace contract independently of root-graph normalization."""

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}
    for step, value in enumerate(values):
        checkpoint = empty_checkpoint()
        version = f"{step + 1:032d}"
        checkpoint["channel_values"] = {"state": {"value": value}}
        checkpoint["channel_versions"] = {"state": version}
        config = await saver.aput(
            config,
            checkpoint,
            {"source": "loop", "step": step, "parents": {}},
            {"state": version},
        )
        await saver.aput_writes(
            config,
            [("task_payload", {"step": step, "detail": "controlled-write"})],
            task_id=f"task-{step}",
        )
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}


@pytest.mark.asyncio
async def test_postgres_checkpoint_guard_rejects_metadata_and_writes_before_insert(
    postgres_settings: PostgresSettings,
) -> None:
    limited_settings = replace(postgres_settings, checkpoint_state_max_bytes=512)
    runtime = PersistenceRuntime(limited_settings)
    await runtime.initialize_schemas()
    await runtime.open()
    saver = runtime.checkpoints.get_saver()
    guard_config = {
        "configurable": {
            "thread_id": "guard-thread",
            "checkpoint_ns": "guard-namespace",
        }
    }
    checkpoint = empty_checkpoint()

    with pytest.raises(PersistencePayloadTooLargeError, match="checkpoint payload"):
        await saver.aput(
            guard_config,
            checkpoint,
            {"source": "loop", "step": 0, "parents": {}, "large": "x" * 4096},
            {},
        )
    with pytest.raises(PersistencePayloadTooLargeError, match="checkpoint writes"):
        await saver.aput_writes(
            {
                "configurable": {
                    **guard_config["configurable"],
                    "checkpoint_id": checkpoint["id"],
                }
            },
            [("tool", {"large": "x" * 4096})],
            task_id="guard-task",
        )

    async with runtime.checkpoints.get_pool().connection() as connection:
        result = await connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM checkpoints WHERE thread_id = %s)
              + (SELECT COUNT(*) FROM checkpoint_writes WHERE thread_id = %s)
              AS count
            """,
            ("guard-thread", "guard-thread"),
        )
        assert int((await result.fetchone())["count"]) == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_postgres_enforces_one_active_run_per_conversation(
    postgres_settings: PostgresSettings,
) -> None:
    runtime = PersistenceRuntime(postgres_settings)
    await runtime.initialize_schemas()
    await runtime.open()
    conversation_id = uuid.uuid4()
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        await repositories.conversations.add(
            Conversation(
                id=conversation_id,
                workspace_uri=f"workspace://{conversation_id}",
            )
        )

    async def create_active_run(index: int) -> uuid.UUID | None:
        run_id = uuid.uuid4()
        try:
            async with runtime.unit_of_work() as unit_of_work:
                repositories = unit_of_work.repositories
                assert repositories is not None
                await repositories.runs.add(
                    Run(
                        id=run_id,
                        conversation_id=conversation_id,
                        request_key=f"concurrent-{index}",
                        status="pending",
                        request_payload={"index": index},
                    )
                )
        except IntegrityError:
            return None
        return run_id

    contenders = await asyncio.gather(create_active_run(1), create_active_run(2))
    winners = [run_id for run_id in contenders if run_id is not None]
    assert len(winners) == 1

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        active = await repositories.runs.get_active_for_conversation(conversation_id)
        assert active is not None
        assert active.id == winners[0]
        task = await repositories.tasks.add(
            RunTask(
                conversation_id=conversation_id,
                run_id=active.id,
                tool_call_id="tool-1",
                capability_name="single_cell_analysis",
                request_payload={"goal": "controlled"},
            )
        )
        review = await repositories.reviews.add(
            Review(
                conversation_id=conversation_id,
                run_id=active.id,
                capability_name="single_cell_analysis",
                tool_call_id="tool-1",
                checkpoint_thread_id=checkpoint_thread_id(str(conversation_id)),
                checkpoint_ns="",
                checkpoint_id="checkpoint-1",
                request_payload={"question": "continue?"},
            )
        )
        assert (
            await repositories.tasks.get(
                task.id,
                conversation_id=conversation_id,
                run_id=active.id,
            )
        ) is task
        assert (
            await repositories.reviews.get(
                review.id,
                conversation_id=conversation_id,
                run_id=active.id,
            )
        ) is review
        await repositories.events.append(
            event_id=uuid.uuid4(),
            run_id=active.id,
            event_type="run.failed",
            payload={"reason": "controlled terminal transition"},
            run_status="failed",
            error_summary="controlled",
        )

    replacement_id = await create_active_run(3)
    assert replacement_id is not None
    await runtime.close()


@pytest.mark.asyncio
async def test_postgres_migration_event_checkpoint_resume_and_retention(
    postgres_settings: PostgresSettings,
) -> None:
    runtime = PersistenceRuntime(postgres_settings)
    await runtime.initialize_schemas()
    await runtime.initialize_schemas()
    await runtime.open()
    assert await runtime.healthcheck() == {
        "application": True,
        "checkpointer": True,
    }

    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    thread_id = checkpoint_thread_id(str(conversation_id))
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        await repositories.conversations.add(
            Conversation(
                id=conversation_id,
                title="PG integration",
                workspace_uri=f"workspace://{conversation_id}",
            )
        )
        await repositories.runs.add(
                Run(
                    id=run_id,
                    conversation_id=conversation_id,
                    request_key="request-1",
                    status="running",
                    request_payload={"instruction": "controlled"},
                checkpoint_thread_id=thread_id,
            )
        )

    event_ids = [uuid.uuid4() for _ in range(8)]

    async def append_event(index: int) -> int:
        async with runtime.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            event = await repositories.events.append(
                event_id=event_ids[index],
                run_id=run_id,
                event_type="run.progress",
                payload={"index": index},
            )
            return event.sequence

    sequences = await asyncio.gather(*(append_event(index) for index in range(8)))
    assert sorted(sequences) == list(range(1, 9))

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        replayed = await repositories.events.append(
            event_id=event_ids[0],
            run_id=run_id,
            event_type="run.progress",
            payload={"index": 0},
        )
        assert replayed.sequence in sequences
        completed = await repositories.events.append(
            event_id=uuid.uuid4(),
            run_id=run_id,
            event_type="run.completed",
            payload={"artifact_count": 0},
            run_status="completed",
        )
        assert completed.sequence == 9

    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        run = await repositories.runs.get(run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.next_event_sequence == 9
        replay = await repositories.events.replay(run_id, after_sequence=5)
        assert [event.sequence for event in replay] == [6, 7, 8, 9]

    rolled_back_artifact_id = uuid.uuid4()
    with pytest.raises(RuntimeError, match="rollback"):
        async with runtime.unit_of_work() as unit_of_work:
            repositories = unit_of_work.repositories
            assert repositories is not None
            await repositories.artifacts.add(
                Artifact(
                    id=rolled_back_artifact_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    kind="report",
                    uri="workspace://report.md",
                    artifact_metadata={"small": True},
                )
            )
            raise RuntimeError("rollback")
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        assert await repositories.artifacts.get(rolled_back_artifact_id) is None

    saver = runtime.checkpoints.get_saver()
    graph = _counter_graph(saver)
    # LangGraph intentionally normalizes the root graph to the empty namespace;
    # nested graph namespaces are framework-managed.  Root persistence is tested
    # end-to-end here, while deterministic namespace isolation is tested against
    # the saver below.
    root_config = {"configurable": {"thread_id": thread_id}}
    for value in range(4):
        result = await graph.ainvoke({"value": value}, root_config, durability="sync")
        assert result["value"] == value + 1

    namespace_a = checkpoint_namespace("graph_a", "compat")
    namespace_b = checkpoint_namespace("graph_b", "compat")
    config_a = await _put_namespaced_checkpoints(
        saver,
        thread_id=thread_id,
        namespace=namespace_a,
        values=[1, 2, 3, 4],
    )
    config_b = await _put_namespaced_checkpoints(
        saver,
        thread_id=thread_id,
        namespace=namespace_b,
        values=[101],
    )

    async def invoke_other_thread(index: int) -> tuple[str, int]:
        other_thread = checkpoint_thread_id(f"other-{index}")
        config = {
            "configurable": {
                "thread_id": other_thread,
            }
        }
        result = await graph.ainvoke({"value": index}, config, durability="sync")
        return other_thread, result["value"]

    other_results = await asyncio.gather(*(invoke_other_thread(index) for index in range(5)))
    assert sorted(value for _, value in other_results) == [1, 2, 3, 4, 5]

    fanout_thread = checkpoint_thread_id("graph-b-fanout")
    fanout_config = {"configurable": {"thread_id": fanout_thread}}
    fanout_graph = _fanout_graph(saver)
    fanout_result = await fanout_graph.ainvoke(
        {"values": [1, 2, 3], "results": []},
        fanout_config,
        durability="sync",
    )
    assert sorted(fanout_result["results"]) == [1, 4, 9]
    fanout_state_before_restart = await fanout_graph.aget_state(fanout_config)

    checkpoints = [item async for item in saver.alist(config_a)]
    assert len(checkpoints) >= 4
    newest_id = str(checkpoints[0].config["configurable"]["checkpoint_id"])
    protected_id = str(checkpoints[-1].config["configurable"]["checkpoint_id"])
    async with runtime.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        await repositories.checkpoint_anchors.add(
            CheckpointAnchor(
                conversation_id=conversation_id,
                run_id=run_id,
                thread_id=thread_id,
                checkpoint_ns=namespace_a,
                checkpoint_id=protected_id,
                anchor_kind="review",
            )
        )

    terminal_at = dt.datetime.now(dt.UTC)
    retention_with_grace = CheckpointRetention(
        runtime.checkpoints,
        app_schema=postgres_settings.app_schema,
        clock=lambda: terminal_at + dt.timedelta(hours=1),
    )
    assert await retention_with_grace.prune_namespace(
        thread_id=thread_id,
        checkpoint_ns=namespace_a,
        terminal_at=terminal_at,
        keep_latest=1,
    ) == 0

    retention = CheckpointRetention(
        runtime.checkpoints,
        app_schema=postgres_settings.app_schema,
        clock=lambda: terminal_at + dt.timedelta(hours=25),
    )
    deleted = await retention.prune_namespace(
        thread_id=thread_id,
        checkpoint_ns=namespace_a,
        terminal_at=terminal_at,
        keep_latest=1,
    )
    assert deleted >= 1
    remaining = [item async for item in saver.alist(config_a)]
    remaining_ids = {
        str(item.config["configurable"]["checkpoint_id"]) for item in remaining
    }
    assert remaining_ids == {newest_id, protected_id}
    assert await retention.prune_namespace(
        thread_id=thread_id,
        checkpoint_ns=namespace_a,
        terminal_at=terminal_at,
        keep_latest=1,
    ) == 0

    async with runtime.checkpoints.get_pool().connection() as connection:
        orphan_writes = await connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM checkpoint_writes cw
            WHERE cw.thread_id = %s
              AND NOT EXISTS (
                SELECT 1 FROM checkpoints c
                WHERE c.thread_id = cw.thread_id
                  AND c.checkpoint_ns = cw.checkpoint_ns
                  AND c.checkpoint_id = cw.checkpoint_id
              )
            """,
            (thread_id,),
        )
        assert int((await orphan_writes.fetchone())["count"]) == 0
        orphan_blobs = await connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM checkpoint_blobs cb
            WHERE cb.thread_id = %s AND cb.checkpoint_ns = %s
              AND NOT EXISTS (
                SELECT 1
                FROM checkpoints c,
                     jsonb_each_text(c.checkpoint->'channel_versions') AS kv
                WHERE c.thread_id = cb.thread_id
                  AND c.checkpoint_ns = cb.checkpoint_ns
                  AND kv.key = cb.channel
                  AND kv.value = cb.version
              )
            """,
            (thread_id, namespace_a),
        )
        assert int((await orphan_blobs.fetchone())["count"]) == 0

    root_state_before_restart = await graph.aget_state(root_config)
    checkpoint_a_before_restart = await saver.aget_tuple(config_a)
    checkpoint_b_before_restart = await saver.aget_tuple(config_b)
    assert checkpoint_a_before_restart is not None
    assert checkpoint_b_before_restart is not None
    assert (
        checkpoint_a_before_restart.checkpoint["channel_values"]
        != checkpoint_b_before_restart.checkpoint["channel_values"]
    )

    await runtime.close()
    await runtime.close()

    restarted = PersistenceRuntime(postgres_settings)
    await restarted.open()
    restarted_saver = restarted.checkpoints.get_saver()
    restarted_graph = _counter_graph(restarted_saver)
    restarted_fanout_graph = _fanout_graph(restarted_saver)
    assert (await restarted_graph.aget_state(root_config)).values == root_state_before_restart.values
    assert (
        await restarted_fanout_graph.aget_state(fanout_config)
    ).values == fanout_state_before_restart.values
    restarted_checkpoint_a = await restarted_saver.aget_tuple(config_a)
    restarted_checkpoint_b = await restarted_saver.aget_tuple(config_b)
    assert restarted_checkpoint_a is not None
    assert restarted_checkpoint_b is not None
    assert (
        restarted_checkpoint_a.checkpoint["channel_values"]
        == checkpoint_a_before_restart.checkpoint["channel_values"]
    )
    assert (
        restarted_checkpoint_b.checkpoint["channel_values"]
        == checkpoint_b_before_restart.checkpoint["channel_values"]
    )
    async with restarted.unit_of_work() as unit_of_work:
        repositories = unit_of_work.repositories
        assert repositories is not None
        assert await repositories.runs.get(run_id) is not None
    await restarted.close()
    await restarted.close()
