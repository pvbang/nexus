"""Safe merge semantics for shared orchestration state.

When multiple agents write to a shared keyed store (e.g. the blackboard's
per-role results), a naive ``store[key] = value`` silently clobbers an earlier
writer's data if two agents share a key. deer-flow solves this with explicit
state reducers that declare how each channel merges. This module ports that
idea as small, pure helpers:

- ``merge_keyed``: insert into a dict with an explicit conflict policy
  (fail-closed default = keep existing + flag, never silently clobber).
- ``dedupe_append``: append to a list only if the item's key is unseen.

Pure functions, no I/O — trivially testable and reused by the blackboard.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class DuplicateKeyResolution(str, Enum):
    INSERTED = "inserted"
    KEPT_EXISTING = "kept_existing"
    OVERWROTE = "overwrote"
    SUFFIXED = "suffixed"


def merge_keyed(
    store: dict[str, Any],
    key: str,
    value: Any,
    *,
    on_conflict: str = "keep",
) -> DuplicateKeyResolution:
    """Insert ``value`` at ``key`` with an explicit conflict policy.

    Policies:
    - ``"keep"`` (default, fail-closed): keep the existing value, flag the
      collision. Never silently lose the first writer's data.
    - ``"overwrite"``: replace (use only when last-writer-wins is correct).
    - ``"suffix"``: store the new value under a uniquified key so nothing is lost.
    """
    if key not in store:
        store[key] = value
        return DuplicateKeyResolution.INSERTED

    if on_conflict == "overwrite":
        store[key] = value
        return DuplicateKeyResolution.OVERWROTE

    if on_conflict == "suffix":
        new_key = f"{key}#{uuid.uuid4().hex[:6]}"
        store[new_key] = value
        logger.warning("state_key_conflict_suffixed", key=key, stored_as=new_key)
        return DuplicateKeyResolution.SUFFIXED

    # Default fail-closed: keep existing, flag loudly.
    logger.warning("state_key_conflict_kept_existing", key=key)
    return DuplicateKeyResolution.KEPT_EXISTING


def dedupe_append(
    target: list,
    seen: set,
    value: Any,
    *,
    key: Optional[str] = None,
) -> bool:
    """Append ``value`` to ``target`` only if its key is unseen.

    Returns True if appended, False if it was a duplicate. ``key`` defaults to
    the value itself (must be hashable).
    """
    k = key if key is not None else value
    if k in seen:
        return False
    seen.add(k)
    target.append(value)
    return True
