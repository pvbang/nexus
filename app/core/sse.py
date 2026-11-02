"""SSE formatting helpers with Last-Event-ID resume support.

Server-Sent Events let a browser resume a dropped stream by replaying the
`Last-Event-ID` header. For that to work, the server must emit an ``id:`` line
with each event. nexus already persists events to ``task_events`` with a
per-task ``sequence``; this module lets the SSE chat path expose that sequence
as the SSE event id so a reconnecting client can request a replay.

Mirrors deer-flow's ``StreamEvent.id`` / Last-Event-ID design.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def format_sse_event(data: dict[str, Any], event_id: Optional[int] = None) -> str:
    """Format a dict as an SSE event block.

    When ``event_id`` is provided, an ``id:`` line precedes the data line so
    the client records it as Last-Event-ID. JSON payload preserves Unicode.
    """
    payload = json.dumps(data, ensure_ascii=False)
    if event_id is not None:
        return f"id: {event_id}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def parse_last_event_id(raw: Optional[str]) -> int:
    """Parse the Last-Event-ID header into a non-negative sequence.

    Returns 0 (replay nothing / start fresh) for missing or malformed values.
    """
    if not raw:
        return 0
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return 0
    return value if value > 0 else 0
