"""
Public inboxes: simple token-in-URL endpoints to accept external messages without
needing third-party platform tokens (Telegram/Slack/etc).

Security model:
- Each inbox has a random token (unpredictable).
- Requests must hit /api/v1/public/inbox/{token}
- Token acts as the shared secret. Treat the URL as sensitive.

Persistence:
- Stored as JSON under WORKSPACE_DIR/public_inboxes.json
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
_STORE_PATH = _WORKSPACE_BASE / "public_inboxes.json"
_LOCK = threading.RLock()


@dataclass(frozen=True)
class PublicInbox:
    token: str
    conversation_id: str
    label: str = ""
    created_at: float = 0.0
    enabled: bool = True


def _load_store() -> list[dict[str, Any]]:
    if not _STORE_PATH.exists():
        return []
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []


def _save_store(items: list[dict[str, Any]]) -> None:
    _WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def create_public_inbox(*, label: str = "", conversation_id: str | None = None) -> PublicInbox:
    token = secrets.token_urlsafe(24)  # ~32 chars, URL-safe
    conv = conversation_id or f"public:{token}"
    now = time.time()

    item = {
        "token": token,
        "conversation_id": conv,
        "label": label or "",
        "created_at": now,
        "enabled": True,
    }

    with _LOCK:
        items = _load_store()
        items.append(item)
        # Keep store from growing forever (best-effort).
        if len(items) > 500:
            items = items[-500:]
        _save_store(items)

    logger.info("public_inbox_created", token_len=len(token), conversation_id=conv)
    return PublicInbox(token=token, conversation_id=conv, label=label or "", created_at=now, enabled=True)


def get_public_inbox(token: str) -> PublicInbox | None:
    t = (token or "").strip()
    if not t:
        return None
    with _LOCK:
        for it in reversed(_load_store()):
            if (it.get("token") or "") == t:
                return PublicInbox(
                    token=t,
                    conversation_id=str(it.get("conversation_id") or f"public:{t}"),
                    label=str(it.get("label") or ""),
                    created_at=float(it.get("created_at") or 0.0),
                    enabled=bool(it.get("enabled", True)),
                )
    return None


def list_public_inboxes(limit: int = 50) -> list[PublicInbox]:
    with _LOCK:
        items = list(reversed(_load_store()))
    out: list[PublicInbox] = []
    for it in items[: max(1, min(limit, 200))]:
        out.append(
            PublicInbox(
                token=str(it.get("token") or ""),
                conversation_id=str(it.get("conversation_id") or ""),
                label=str(it.get("label") or ""),
                created_at=float(it.get("created_at") or 0.0),
                enabled=bool(it.get("enabled", True)),
            )
        )
    return out

