"""SSE transport adapter over the database-authoritative event log."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

from omnicell_agent.runs.event_log import RunEventLog
from omnicell_agent.runs.events import DecimalCursor
from pydantic import TypeAdapter


_CURSOR_ADAPTER = TypeAdapter(DecimalCursor)


def resolve_sse_cursor(
    after_sequence: str | None,
    last_event_id: str | None,
) -> int:
    query = _CURSOR_ADAPTER.validate_python(after_sequence) if after_sequence is not None else None
    header = _CURSOR_ADAPTER.validate_python(last_event_id) if last_event_id is not None else None
    if query is not None and header is not None and query != header:
        raise ValueError("after_sequence 与 Last-Event-ID 不一致")
    return int(query or header or "0")


async def sse_frames(
    event_log: RunEventLog,
    run_id: UUID,
    *,
    after_sequence: int,
    heartbeat_seconds: float = 15,
) -> AsyncIterator[str]:
    cursor = after_sequence
    while True:
        observed = event_log.notifier.revision(run_id)
        page = await event_log.replay(run_id, after_sequence=cursor, limit=200)
        for event in page.events:
            sequence = int(event.sequence)
            if sequence <= cursor:
                continue
            cursor = sequence
            data = json.dumps(
                event.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n"
        if page.has_more:
            continue
        if page.terminal:
            return
        revision = await event_log.notifier.wait_for_change(
            run_id,
            observed,
            timeout_seconds=heartbeat_seconds,
        )
        if revision == observed:
            yield ": heartbeat\n\n"


__all__ = ["resolve_sse_cursor", "sse_frames"]
