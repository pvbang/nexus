"""
MekongAI Nexus - Task Path Resolver.

SINGLE SOURCE OF TRUTH for resolving every task/subtask directory on disk.

Before this module, ~50 sites across the codebase hardcoded
``WORKSPACE_DIR / "tasks" / task_id[:8] / ...``. That made two things impossible:
  1. Changing the directory scheme (e.g. nesting subtasks) without editing 50 places.
  2. Distinguishing a parent task from a subtask at path-resolution time.

Directory layout produced by this module::

    workspace_data/tasks/{parent}/                 # top-level task
    ├── _context/                                  # parent's context docs
    ├── _work/                                     # SHARED codebase (parent + subtasks)
    ├── _snapshots/
    └── subtasks/
        └── {child}/                               # nested subtask (provenance)
            └── _context/                          # subtask's own context docs
            # NOTE: a subtask has NO _work/ of its own — it writes into the
            # parent's _work/ so all agents operate on the same codebase.

Parent resolution (how we know an id is a subtask) is cheap and restart-safe:
  1. ``current_parent_task_id`` contextvar  — authoritative at write time, free.
  2. ``_parent_cache``                       — in-process memo.
  3. ``.task_meta.json``                     — persisted ``parent_task_id``, survives restart.
A DB lookup is intentionally avoided here: most path resolves have no db handle
and run on hot paths.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Workspace base — read once at import (matches _WORKSPACE_BASE convention elsewhere)
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# child_id (full) -> parent_id (full) | None.  None = known top-level task.
_parent_cache: dict[str, Optional[str]] = {}
_cache_lock = threading.Lock()


# ── Truncation — the ONLY place [:8] lives ───────────────────────────────

def dir_name(task_id: str) -> str:
    """Directory name for a task id. The single truncation point in the system.

    Kept at 8 chars for backward compat with existing on-disk folders. To move
    to full ids later, change only this function.
    """
    return task_id[:8]


def tasks_root() -> Path:
    """Root directory holding all top-level task folders."""
    return WORKSPACE_DIR / "tasks"


# ── Parent resolution ─────────────────────────────────────────────────────

def register_parent(child_id: str, parent_id: Optional[str]) -> None:
    """Record the parent of a task id (or None for a confirmed top-level task).

    Called by the delegation flow once a subtask id is known, and by
    ``_write_task_meta`` when persisting task metadata. Safe to call repeatedly.
    """
    if not child_id:
        return
    # A task is never its own parent.
    norm_parent = parent_id or None
    if norm_parent == child_id:
        norm_parent = None
    with _cache_lock:
        _parent_cache[child_id] = norm_parent


def get_parent(task_id: str) -> Optional[str]:
    """Resolve the parent id of a task, or None if it is top-level.

    Order: contextvar (write-time authority) → cache → .task_meta.json.
    Result is memoized in ``_parent_cache``.
    """
    if not task_id:
        return None

    # 1. Contextvar — authoritative while a subtask is actively executing.
    try:
        from app.core.execution_context import current_parent_task_id, current_task_id

        # Only trust the contextvar when it refers to THIS task as the active one.
        if current_task_id.get("") == task_id:
            ctx_parent = current_parent_task_id.get("")
            if ctx_parent and ctx_parent != task_id:
                register_parent(task_id, ctx_parent)
                return ctx_parent
    except Exception:
        pass

    # 2. Cache.
    with _cache_lock:
        if task_id in _parent_cache:
            return _parent_cache[task_id]

    # 3. Persisted metadata at the legacy-flat location (always written there).
    parent = _read_parent_from_meta(task_id)
    register_parent(task_id, parent)
    return parent


def get_root(task_id: str) -> str:
    """Resolve the ROOT (top-level conversation) ancestor of a task.

    Walks the parent chain to the top so that a delegation chain of any depth
    (A→B→C→D) collapses to a single root: every descendant is stored ONE level
    under the root's ``subtasks/`` and shares the root's ``_work/``. This keeps
    the on-disk layout flat-and-bounded (no top-level folder per subtask, no
    Windows path-length blowup) and preserves the "all agents share one
    codebase" invariant regardless of delegation depth.

    Cycle-safe: a corrupt parent cycle terminates and returns the last id seen.
    """
    if not task_id:
        return task_id
    seen: set[str] = set()
    current = task_id
    while True:
        seen.add(current)
        parent = get_parent(current)
        if not parent or parent == current or parent in seen:
            return current
        current = parent


def _read_parent_from_meta(task_id: str) -> Optional[str]:
    """Read parent_task_id from a task's .task_meta.json.

    A subtask's meta lives at the NESTED path
    (``tasks/{parent}/subtasks/{child}/.task_meta.json``), but at cold-cache
    resolution time we don't yet know the parent — so we also scan nested
    locations. Raw directory globbing only (no get_parent call) to avoid
    recursion.
    """
    target = dir_name(task_id)
    candidates = [tasks_root() / target / ".task_meta.json"]

    root = tasks_root()
    if root.exists():
        try:
            for parent_entry in root.iterdir():
                nested_meta = parent_entry / "subtasks" / target / ".task_meta.json"
                if nested_meta.exists():
                    candidates.append(nested_meta)
        except OSError:
            pass

    for meta_path in candidates:
        try:
            if meta_path.exists():
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                parent = data.get("parent_task_id")
                if parent and parent != task_id:
                    return str(parent)
        except Exception as exc:
            logger.debug("task_meta_read_failed", task_id=target, error=str(exc)[:200])
    return None


# ── Core resolvers ─────────────────────────────────────────────────────────

def task_dir(task_id: str) -> Path:
    """Resolve a task's own directory.

    A descendant of any depth is stored ONE level under its ROOT conversation
    folder (``tasks/{root}/subtasks/{id}``); a top-level task gets its own
    folder. Using the root (not the direct parent) means a deep delegation
    chain never spawns extra top-level folders or deeply nested paths.
    """
    root = get_root(task_id)
    if root and root != task_id:
        return tasks_root() / dir_name(root) / "subtasks" / dir_name(task_id)
    return tasks_root() / dir_name(task_id)


def subtask_dir(parent_id: str, child_id: str) -> Path:
    """Subtask directory builder — nested under the ROOT ancestor of parent_id.

    Resolves parent_id's root so the child lands under the conversation root
    even when parent_id is itself a nested subtask.
    """
    root = get_root(parent_id) if parent_id else child_id
    return tasks_root() / dir_name(root) / "subtasks" / dir_name(child_id)


def context_dir(task_id: str) -> Path:
    """``_context/`` directory of a task (nested for subtasks)."""
    d = task_dir(task_id) / "_context"
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir(task_id: str) -> Path:
    """``_work/`` directory — SHARED with parent for subtasks.

    Subtasks have no separate _work; they write into the ROOT's so all agents
    across a delegation chain of any depth operate on the same codebase.
    Mirrors the long-standing ``get_task_workspace`` semantics, now centralized.
    """
    owner = get_root(task_id)
    d = tasks_root() / dir_name(owner) / "_work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshots_dir(task_id: str) -> Path:
    """``_snapshots/`` directory of a task (nested for subtasks)."""
    d = task_dir(task_id) / "_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def context_file(task_id: str, name: str) -> Path:
    """Path to a single file inside a task's ``_context/``."""
    return context_dir(task_id) / name


# ── Transition-safe lookup ─────────────────────────────────────────────────

def find_task_dir(task_id: str) -> Optional[Path]:
    """Locate an existing task directory, checking BOTH nested and legacy-flat.

    Read-side helper for API/tool sites that must work during the transition
    while some folders are still flat. Returns None if neither exists.
    """
    if not task_id:
        return None

    # Nested (if we can resolve a parent).
    parent = get_parent(task_id)
    if parent:
        nested = subtask_dir(parent, task_id)
        if nested.exists():
            return nested

    # Legacy flat.
    flat = tasks_root() / dir_name(task_id)
    if flat.exists():
        return flat

    # Last resort: scan for the id as a nested subtask under any parent.
    root = tasks_root()
    if root.exists():
        target = dir_name(task_id)
        try:
            for parent_entry in root.iterdir():
                cand = parent_entry / "subtasks" / target
                if cand.exists():
                    register_parent(task_id, parent_entry.name)
                    return cand
        except OSError:
            pass

    return None
