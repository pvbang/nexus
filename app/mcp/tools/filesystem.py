"""
MekongAI Nexus - Filesystem Tools.

Register read_file, write_file, list_files, delete_file, create_directory,
move_file, copy_file into the global tool registry.
"""

import asyncio
import fnmatch
import mimetypes
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# Workspace cho agents tạo/sửa files — consistent via env var
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
WORKSPACE_DIR.mkdir(exist_ok=True)


def _is_within_workspace(path: Path) -> bool:
    """Whether ``path`` resolves to a descendant of (or equal to) WORKSPACE_DIR.

    B1 (audit 2026-06-29): compare by PATH COMPONENT, not string prefix. A
    ``str(p).startswith(str(WORKSPACE_DIR))`` check let a sibling directory whose
    name merely shares the workspace prefix (e.g. ``workspace_data_evil/``) pass
    the boundary. ``relative_to`` splits on path separators, so only a true
    descendant matches; a shared name prefix without a separator boundary does not.
    """
    try:
        path.resolve().relative_to(WORKSPACE_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve_case_insensitive(path: Path) -> Path | None:
    """Return ``path`` if it exists, else a UNIQUE case-insensitive sibling.

    M2 (audit 2026-06-29): on a case-sensitive FS (Linux/Docker) an agent that
    wrote ``authorization_required.md`` and reads ``AUTHORIZATION_REQUIRED.md``
    gets a spurious "File not found". When the exact name misses, look for exactly
    one sibling whose name matches case-insensitively and use it. Ambiguous (two
    siblings differing only by case) → return None and do NOT guess.
    """
    try:
        if path.exists():
            return path
        parent = path.parent
        if not parent.is_dir():
            return None
        target = path.name.casefold()
        matches = [c for c in parent.iterdir() if c.name.casefold() == target]
        return matches[0] if len(matches) == 1 else None
    except OSError:
        return None


def _resolve_workspace_by_discovery() -> Path | None:
    """Auto-discover workspace path by scanning WORKSPACE_DIR/tasks/.

    This is the LAST RESORT fallback when no context var is set (e.g., agents spawned
    without task context in Docker). It scans existing task directories and tries
    to match by checking for typical task files (TASK_BRIEF.md, ROE.md).

    Returns the _work/ dir of the most recently modified task, or None if empty.
    """
    tasks_dir = WORKSPACE_DIR / "tasks"
    if not tasks_dir.exists():
        return None

    # Discover all valid task dirs, sorted by modification time (newest first)
    # Task dirs can be 8-char hash (e.g., "90065691") OR descriptive names
    # (e.g., "Mục tiêu: đạt được remote code..."). Accept ANY subdirectory
    # that has _context/ or _work/ inside it.
    candidates: list[tuple[float, Path]] = []
    try:
        for entry in tasks_dir.iterdir():
            if not entry.is_dir():
                continue
            # Skip hidden dirs and known non-task dirs
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            # Validate: must have _context/ or _work/ to be a real task dir
            ctx_dir = entry / "_context"
            work_dir = entry / "_work"
            if not ctx_dir.exists() and not work_dir.exists():
                continue
            if not work_dir.exists():
                work_dir.mkdir(parents=True, exist_ok=True)
            mtime = ctx_dir.stat().st_mtime if ctx_dir.exists() else entry.stat().st_mtime
            candidates.append((mtime, work_dir))
    except (OSError, PermissionError):
        pass

    if candidates:
        # Sort descending by mtime (newest first)
        candidates.sort(key=lambda x: x[0], reverse=True)
        logger.info("workspace_discovery", matches=len(candidates), best=str(candidates[0][1]))
        return candidates[0][1]

    return None


def resolve_task_workspace() -> tuple[Path, bool]:
    """Resolve the task workspace, returning ``(path, not_authoritative)``.

    ``not_authoritative`` is True whenever the path did NOT come from explicit task
    context (Strategy 1). That covers the silent-but-common Docker failure where
    context propagation dies and we fall back to *guessing*: auto-discovery returns
    the most-recent OTHER task's _work (writing into a neighbour's codebase), CWD
    detection, or finally _unscoped. ALL of these are phantom writes — the agent
    believes it wrote to its task workspace but did not — so writers warn on any of
    them (audit A5). Only explicit current_task_id / current_parent_task_id is
    authoritative (False).

    Resolution order:
    1. current_parent_task_id set → parent's _work/   (authoritative)
    2. current_task_id set → own _work/               (authoritative)
    3. Auto-discovery: most-recent task under tasks/   (GUESS → flagged)
    4. CWD is a tasks/.../_work dir                     (GUESS → flagged)
    5. Final fallback → _unscoped/                      (flagged)
    """
    # ── Strategy 1: Context vars (primary, AUTHORITATIVE) ──
    try:
        from app.core.execution_context import current_task_id, current_parent_task_id

        from app.core import task_paths

        parent_id = current_parent_task_id.get("")
        if parent_id:
            return task_paths.work_dir(parent_id), False

        task_id = current_task_id.get("")
        if task_id:
            return task_paths.work_dir(task_id), False
    except Exception:
        pass

    # Everything below is a GUESS without authoritative context → not_authoritative.
    # (Observability for this is recorded by WRITERS — see _record_phantom_write_metric
    # called from handle_write_file — so a benign read doesn't get tagged as a
    # phantom WRITE, and the metric isn't bumped on every path lookup.)
    # ── Strategy 2: Auto-discovery by most recent task (mtime) ──
    logger.warning(
        "get_task_workspace_context_var_empty",
        msg="current_task_id not set — falling back to auto-discovery. "
            "This indicates a bug in context propagation.",
    )
    discovered = _resolve_workspace_by_discovery()
    if discovered:
        return discovered, True  # guessed another task's _work — phantom write risk

    # ── Strategy 3: CWD-based detection ──
    cwd = Path.cwd()
    if cwd.name == "_work" and "tasks" in cwd.parts:
        return cwd, True

    # ── Strategy 4: Fallback to _unscoped ──
    logger.error(
        "get_task_workspace_all_fallbacks_exhausted",
        msg="No task context, no discoverable tasks. Using _unscoped.",
        workspace_dir=str(WORKSPACE_DIR),
    )
    fallback = WORKSPACE_DIR / "_unscoped"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback, True


# Sessions for which we've already recorded a phantom-write metric, so a Docker run
# that loses context doesn't increment the counter on every single write (the signal
# is a STATE — "this session is writing without context" — not a per-write event).
_phantom_write_recorded: set[str] = set()


def _record_phantom_write_metric() -> None:
    """Record ONCE per session that a WRITE happened without authoritative context.

    Best-effort, never raises. Deduped per session_id so the counter reflects how
    many runs hit the phantom-write condition, not how many writes — keeping the
    metric meaningful and the hot write path cheap.
    """
    try:
        from app.core.execution_context import current_session_id
        sid = current_session_id.get("") or "_no_session"
        if sid in _phantom_write_recorded:
            return
        _phantom_write_recorded.add(sid)
        from app.core.background_metrics import get_background_metrics
        get_background_metrics().record_failure(
            "phantom_write_no_task_context",
            "write_file ran without authoritative task context — file may be misplaced",
        )
    except Exception:
        pass


def get_task_workspace() -> Path:
    """Resolve workspace directory for current task (Path only).

    Thin wrapper over ``resolve_task_workspace()`` kept for the many callers that
    only need the path. Writers that must warn when the workspace was resolved
    without authoritative task context should call ``resolve_task_workspace()``.
    """
    return resolve_task_workspace()[0]


def collapse_nested_workspace(ws: Path, ws_name: str = "workspace_data") -> int:
    """Self-heal the nested-workspace duplicate (audit c527e1c2, RC3).

    Agent code run with CWD = the task's ``_work/`` sometimes writes to a relative
    path that REPEATS the workspace prefix it saw in its context — e.g.
    ``savefig("workspace_data/tasks/<id>/_work/calc/x.png")``. Because CWD is
    already ``_work/``, that lands at ``_work/workspace_data/tasks/<id>/_work/...``
    — a nested duplicate of the whole task tree. (write_file paths are normalized
    by ``_smart_resolve_path``, but a savefig/open() inside execute_code is not.)

    This sweep merges any such ``ws/<ws_name>/...`` subtree's files back up into
    ``ws`` (the real _work/), preferring the SHALLOW existing file as authoritative
    (never clobbers it), then removes the nested tree. Returns the number of files
    moved. Best-effort: never raises into the caller.
    """
    moved = 0
    try:
        import shutil

        def _merge_subtree_up(src: Path) -> int:
            """Move every file under ``src`` to the same relative path under ``ws``,
            keeping the SHALLOW (existing) file authoritative. Returns files moved."""
            n = 0
            if not src.is_dir():
                return 0
            for item in src.rglob("*"):
                if not item.is_file():
                    continue
                dest = ws / item.relative_to(src)
                if dest.exists():
                    continue  # shallow file is authoritative — do not overwrite
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(item), str(dest))
                    n += 1
                except OSError:
                    continue
            return n

        # ── Signature B: exact prefix-repeat duplicate ws/<ws.name>/… (audit S2) ──
        # A write path that began with a redundant "_work/" (e.g.
        # write_file("_work/redteam/EXEC.md")) lands at ws/_work/redteam/EXEC.md —
        # a "_work/_work" duplicate that orphans the deliverable. A "_work" inside a
        # "_work" IS the bug signature (never a legit layout), so merge it back up.
        work_name = ws.name
        if work_name and work_name not in (".", "/", "\\"):
            dup = ws / work_name
            if dup.is_dir() and dup.resolve() != ws.resolve():
                moved += _merge_subtree_up(dup)
                shutil.rmtree(dup, ignore_errors=True)

        # ── Signature A (RC3): ws/<ws_name>/tasks/<id>/_work/… duplicate ──
        # GUARD (final-review): an empty/dot/slash ws_name would make nested_root == ws
        # and the rmtree below would delete the REAL _work/. Never proceed unless
        # nested_root is a STRICT subdirectory of ws.
        if ws_name and ws_name not in (".", "/", "\\"):
            nested_root = ws / ws_name
            if nested_root.is_dir() and nested_root.resolve() != ws.resolve():
                # Only collapse when the nested tree carries the bug's SIGNATURE: an
                # inner "_work" dir (the repeated-prefix duplicate is always
                # ws/<ws_name>/tasks/<id>/_work/...). Without an inner _work this is
                # NOT the nested-duplicate bug — it's a legitimately-named
                # "workspace_data" dir the task created (e.g. analyzing this
                # codebase); leave it untouched (do not flatten/delete a legit dir).
                inner_works = [p for p in nested_root.rglob("_work") if p.is_dir()]
                if inner_works:
                    # Deepest first so the most task-specific _work wins the mapping.
                    inner_works.sort(key=lambda p: len(p.parts), reverse=True)
                    for src in inner_works:
                        moved += _merge_subtree_up(src)
                    # Remove the nested duplicate tree (useful files merged up).
                    shutil.rmtree(nested_root, ignore_errors=True)

        if moved:
            logger.warning(
                "nested_workspace_collapsed",
                ws=str(ws), moved=moved,
                note="agent wrote to a path repeating the workspace prefix; merged back",
            )
    except Exception as e:
        logger.debug("collapse_nested_workspace_failed", error=str(e))
    return moved


def _smart_resolve_path(rel_path: str, ws: Path, ws_root: Path) -> Path:
    """Resolve a file path that may be relative to ANYTHING.

    Agents commonly pass paths in various formats:
    - "FINDINGS.md"                                        → relative to task _work/
    - "../_context/TASK_BRIEF.md"                           → relative to task _work/
    - "workspace_data/tasks/xxx/_work/FINDINGS.md"         → absolute-ish (WORKSPACE_DIR prefix)
    - "tasks/xxx/_work/recon/file.md"                      → missing ws_root name prefix
    - "/app/workspace_data/tasks/xxx/_work/FINDINGS.md"    → absolute (Docker)
    - "D:/projects/python/mekongai-nexus/workspace_data/..." → absolute (Windows)

    CRITICAL: Must work for BOTH read (file exists) AND write (file doesn't exist yet).
    Never create nested paths like _work/workspace_data/tasks/.../
    """
    p = Path(rel_path)
    ws_name = ws_root.name  # e.g., "workspace_data"

    # ── Case 1: Already absolute ──
    if p.is_absolute():
        resolved = p.resolve()
        try:
            resolved.relative_to(ws_root.resolve())
            return resolved
        except ValueError:
            return resolved

    # ── Case 2: Starts with WORKSPACE_DIR name (e.g., "workspace_data/...") ──
    # NO .exists() check — this must work for writes too!
    p_parts = p.parts
    if len(p_parts) > 1 and p_parts[0] == ws_name:
        stripped = Path(*p_parts[1:])
        # E-R12 (tri-run audit 2026-07-02): only "workspace_data/tasks/..." is an
        # ABSOLUTE address into the tasks tree — keep it at ws_root. Anything else
        # (e.g. the planner's CONTRACT "workspace_data/recon/", "workspace_data/
        # exploits/", or a bare "workspace_data/report.md") is a DELIVERABLE of the
        # CURRENT task, not a sibling of the tasks/ tree. Joining it to ws_root put
        # it OUTSIDE the task _work/, so the write-handler flattened it to
        # _work/<basename> (subdir lost) — the deepseek run's recon/ was perpetually
        # "empty" because of exactly this. Resolve it INTO the task _work/ instead.
        if p_parts[1] == "tasks":
            return ws_root / stripped
        return ws / stripped

    # ── Case 2b: redundant leading "_work/" prefix (audit S2) ──
    # `ws` IS the task's _work/. An agent that prefixes the path with the work-dir
    # name (e.g. "_work/redteam/x.md", or even "_work/_work/x.md") would otherwise
    # nest UNDER it → "_work/_work/...", orphaning the deliverable from the
    # canonical tree. Strip every leading segment equal to ws.name so the remainder
    # is relative to ws; a bare "_work/" resolves to ws itself. Only the EXACT
    # segment matches — "_workspace/" is a real child dir, left intact.
    work_name = ws.name
    if work_name and p_parts and p_parts[0] == work_name:
        i = 0
        while i < len(p_parts) and p_parts[i] == work_name:
            i += 1
        tail = p_parts[i:]
        return ws / Path(*tail) if tail else ws

    # ── Case 3: Starts with "tasks/" (missing ws_root prefix) ──
    # Agent passed "tasks/xxx/_work/file.md" instead of "workspace_data/tasks/..."
    if len(p_parts) > 1 and p_parts[0] == "tasks":
        return ws_root / p

    # ── Case 4: Relative path with ../ (e.g., "../_context/TASK_BRIEF.md") ──
    # Must come BEFORE marker extraction — ../_context/ is a legit relative ref
    if ".." in p_parts:
        candidate = (ws / rel_path).resolve()
        try:
            candidate.relative_to(ws_root.resolve())
            return candidate
        except ValueError:
            pass

    # ── Case 5: Contains embedded _work/ or _context/ from a redundant path ──
    # e.g., agent passes "some/junk/tasks/B/_work/file.md" or "a756c825/_work/recon/x.md"
    # Only trigger when there are 2+ segments before the marker (not simple "recon/_work/..")
    for marker in ("_work", "_context"):
        if marker in p_parts:
            idx = len(p_parts) - 1 - list(reversed(p_parts)).index(marker)
            # Guard: only strip if there's meaningful prefix before marker
            # (at least 1 segment that looks like a task dir path)
            if idx >= 1 and idx < len(p_parts) - 1:
                tail = Path(*p_parts[idx + 1:])
                return ws / tail

    # ── Case 6: Simple relative path — resolve against task workspace ──
    return ws / rel_path


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


# Project root files that agents are allowed to READ (read-only whitelist).
# These are outside workspace_data/ but contain essential operational info.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_ROOT_READABLE = {
    "developer_profile.json",
    "CLAUDE.md",
    ".env.example",
}


def _try_project_root_read(raw_path: str) -> Path | None:
    """Check if path matches a project-root readable file (outside workspace)."""
    filename = Path(raw_path).name
    if filename in _PROJECT_ROOT_READABLE:
        candidate = _PROJECT_ROOT / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


async def handle_read_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Đọc file từ workspace với path traversal protection, line numbers, và pagination."""
    raw_path = params.get("path", "")
    offset = params.get("offset", 1)
    limit = params.get("limit", 500)
    encoding = params.get("encoding", "utf-8")
    show_line_numbers = params.get("show_line_numbers", True)

    # Sanitize offset/limit
    if not isinstance(offset, int) or offset < 1:
        offset = 1
    if not isinstance(limit, int) or limit < 1:
        limit = 500
    # Cap limit to prevent context explosion
    if limit > 2000:
        limit = 2000
    # Sanitize encoding
    if not isinstance(encoding, str) or not encoding.strip():
        encoding = "utf-8"
    encoding = encoding.strip()
    # Sanitize show_line_numbers
    if not isinstance(show_line_numbers, bool):
        show_line_numbers = True

    ws = get_task_workspace()

    # Check project root whitelist FIRST (e.g., developer_profile.json)
    project_root_file = _try_project_root_read(raw_path)
    if project_root_file:
        path = project_root_file
    else:
        # Use WORKSPACE_DIR for safety check — _smart_resolve_path will handle task scoping
        path_verdict = check_path_safety(raw_path, str(WORKSPACE_DIR))
        if not path_verdict.safe:
            logger.warning("path_traversal_blocked", path=raw_path[:200], reason=path_verdict.reason)
            return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

        path = _smart_resolve_path(raw_path, ws, WORKSPACE_DIR)
        if not path.exists():
            # M2: a unique case-insensitive sibling (agent wrote lowercase, reads
            # the CONTRACT-declared uppercase name, or vice versa) is the file the
            # caller meant — resolve it before falling back / giving up.
            _ci = _resolve_case_insensitive(path)
            if _ci is not None and _ci != path:
                path = _ci
        if not path.exists():
            # Fallback: try project root before giving up
            fallback = _try_project_root_read(Path(raw_path).name)
            if fallback:
                path = fallback
                project_root_file = fallback
            else:
                return {"success": False, "output": f"File not found: {raw_path}", "data": None, "artifacts": []}
        if not project_root_file and not _is_within_workspace(path):
            return {"success": False, "output": "Access denied: outside workspace", "data": None, "artifacts": []}

    stat_result = await asyncio.to_thread(path.stat)
    size = stat_result.st_size

    # Read file with specified encoding, fallback to latin-1 on decode error
    used_encoding = encoding
    try:
        content = await asyncio.to_thread(path.read_text, encoding=encoding, errors="strict")
    except (UnicodeDecodeError, LookupError):
        used_encoding = "latin-1"
        content = await asyncio.to_thread(path.read_text, encoding="latin-1", errors="replace")

    lines = content.split("\n")
    # Remove trailing empty line from split if file ends with newline
    if lines and lines[-1] == "" and content.endswith("\n"):
        lines = lines[:-1]
    total_lines = len(lines)

    if project_root_file:
        relative_path = f"(project root) {path.name}"
    else:
        relative_path = str(path.relative_to(WORKSPACE_DIR))

    def _format_file_size(size_bytes: int) -> str:
        """Format bytes to human-readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    # Handle empty file
    if total_lines == 0:
        return {
            "success": True,
            "output": f"{relative_path} (empty, {_format_file_size(size)}, {used_encoding})",
            "data": {
                "path": relative_path,
                "total_lines": 0,
                "showing_from": 0,
                "showing_to": 0,
                "truncated": False,
                "size": size,
                "encoding": used_encoding,
            },
            "artifacts": [],
        }

    # Handle offset beyond file end
    if offset > total_lines:
        return {
            "success": True,
            "output": f"{relative_path} | {total_lines} lines | offset {offset} beyond end",
            "data": {
                "path": relative_path,
                "total_lines": total_lines,
                "showing_from": offset,
                "showing_to": offset,
                "truncated": False,
                "size": size,
                "encoding": used_encoding,
            },
            "artifacts": [],
        }

    # Apply offset/limit (1-based indexing)
    showing_from = offset
    showing_to = min(offset + limit - 1, total_lines)
    selected_lines = lines[showing_from - 1:showing_to]
    truncated = showing_to < total_lines

    # Format content lines - compact format
    if show_line_numbers:
        formatted_lines = "\n".join(
            f"{showing_from + i}|{line}"
            for i, line in enumerate(selected_lines)
        )
    else:
        formatted_lines = "\n".join(selected_lines)

    # Build compact output
    header = f"{relative_path} [{showing_from}-{showing_to}/{total_lines}]"
    parts = [header, formatted_lines]

    if truncated:
        parts.append(f"(offset={showing_to + 1} for more)")

    output = "\n".join(parts)

    # Track file read in FileContextTracker (fire-and-forget)
    try:
        from app.core.execution_context import current_file_tracker
        tracker = current_file_tracker.get(None)
        if tracker:
            await tracker.track_read(raw_path, total_lines=total_lines, size_bytes=size)
    except Exception:
        pass  # Never block tool execution

    return {
        "success": True,
        "output": output,
        "data": {
            "path": relative_path,
            "total_lines": total_lines,
            "showing_from": showing_from,
            "showing_to": showing_to,
            "truncated": truncated,
            "size": size,
            "encoding": used_encoding,
        },
        "artifacts": [],
    }


def _run_post_write_validation(rel_path: str, content: str) -> None:
    """Run informational validation on written file content.

    Checks JSON syntax for .json files and Python syntax for .py files.
    Logs warnings only — never blocks the write operation.
    """
    if not content:
        logger.warning("write_validation_empty_file", path=rel_path)
        return

    ext = Path(rel_path).suffix.lower()

    if ext == ".json":
        import json
        try:
            json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "write_validation_invalid_json",
                path=rel_path,
                error=str(e)[:200],
            )

    elif ext == ".py":
        try:
            compile(content, rel_path, "exec")
        except SyntaxError as e:
            logger.warning(
                "write_validation_python_syntax_error",
                path=rel_path,
                error=f"Written Python file has syntax error: {e}",
            )


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string for write_file output."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


async def handle_write_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Ghi file vào workspace với path traversal protection, multiple write modes, và atomic write."""
    rel_path = params.get("path", "output.txt")
    content = params.get("content", "")
    mode = params.get("mode", "overwrite")
    insert_line = params.get("insert_line", None)
    encoding = params.get("encoding", "utf-8")
    create_dirs = params.get("create_dirs", True)

    # Validate mode
    valid_modes = ("overwrite", "append", "insert", "prepend")
    if mode not in valid_modes:
        return {"success": False, "output": f"⚠️ Invalid mode '{mode}'. Must be one of: {valid_modes}", "data": None, "artifacts": []}

    # Validate encoding
    if not isinstance(encoding, str) or not encoding.strip():
        encoding = "utf-8"
    encoding = encoding.strip()

    # Validate insert_line when mode=insert
    if mode == "insert" and insert_line is not None:
        if not isinstance(insert_line, int) or insert_line < 1:
            return {"success": False, "output": "⚠️ insert_line must be a positive integer (1-based)", "data": None, "artifacts": []}

    ws, ws_not_authoritative = resolve_task_workspace()

    # Use WORKSPACE_DIR for safety check — _smart_resolve_path will handle task scoping
    path_verdict = check_path_safety(rel_path, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        logger.warning("path_traversal_blocked", path=rel_path[:200], reason=path_verdict.reason)
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    full_path = _smart_resolve_path(rel_path, ws, WORKSPACE_DIR)
    if not _is_within_workspace(full_path):
        return {"success": False, "output": "Access denied", "data": None, "artifacts": []}

    # For write operations: must be within the TASK workspace. If smart_resolve
    # resolved INSIDE WORKSPACE_DIR but OUTSIDE this task's ws (e.g. the workspace
    # root or another task dir), redirect back to ws/<basename>. (A path truly
    # outside WORKSPACE_DIR was already hard-denied by the access-check above — B1.)
    # NOTE: resolve() BOTH sides before comparing — full_path may be RELATIVE (when
    # WORKSPACE_DIR is the relative default "workspace_data") while ws.resolve() is
    # ABSOLUTE. Comparing a relative path against an absolute one ALWAYS raised
    # ValueError, which silently flattened every subdir write to ws/<basename> (audit
    # 1f6decce — cuas_design/* writes lost their subdir, breaking the deliverable contract).
    # When this fallback fires, the file lands at ws/<basename>, NOT the requested
    # path — the agent MUST be told (audit 1f6decce: silently reporting the requested
    # path is the phantom-write dishonesty that burned 6+ turns chasing a "missing"
    # file that was actually elsewhere).
    redirected_from = ""
    try:
        full_path.resolve().relative_to(ws.resolve())
    except ValueError:
        redirected_from = rel_path
        full_path = ws / Path(rel_path).name

    file_existed = full_path.exists()
    old_lines = 0

    # Read existing content for non-overwrite modes or pre-read check
    existing_content = ""
    if file_existed:
        try:
            existing_content = await asyncio.to_thread(full_path.read_text, encoding=encoding)
            old_lines = len(existing_content.split("\n"))
            # Remove trailing empty from split if ends with newline
            if existing_content.endswith("\n"):
                old_lines -= 1
        except (UnicodeDecodeError, LookupError):
            existing_content = await asyncio.to_thread(full_path.read_text, encoding="latin-1")
            old_lines = len(existing_content.split("\n"))
            if existing_content.endswith("\n"):
                old_lines -= 1

    # Determine final content based on mode
    if mode == "overwrite":
        final_content = content
    elif mode == "append":
        final_content = existing_content + content
    elif mode == "prepend":
        final_content = content + existing_content
    elif mode == "insert":
        if not file_existed:
            final_content = content
        else:
            lines = existing_content.split("\n")
            if insert_line is None:
                insert_line = 1
            # If insert_line > total lines, append at end
            if insert_line > len(lines):
                final_content = existing_content + content
            else:
                # Insert at the specified line (1-based, content goes before that line)
                insert_idx = insert_line - 1
                content_lines = content.split("\n")
                lines[insert_idx:insert_idx] = content_lines
                final_content = "\n".join(lines)
    else:
        final_content = content

    # Create parent directories if needed
    if create_dirs:
        full_path.parent.mkdir(parents=True, exist_ok=True)
    elif not full_path.parent.exists():
        return {"success": False, "output": f"⚠️ Parent directory does not exist: {full_path.parent.relative_to(ws)}", "data": None, "artifacts": []}

    # --- Advisory file lock check ---
    lock_warning = ""
    try:
        from app.core.workspace_manager import FileLock
        from app.core.execution_context import current_agent_name
        agent_name = current_agent_name.get("")
        if agent_name:
            holder = FileLock.check_lock(rel_path)
            if holder and holder != agent_name:
                lock_warning = (
                    f"\n\u26a0\ufe0f Warning: File {rel_path} is locked by {holder}. "
                    f"Write may cause conflicts."
                )
                logger.warning(
                    "write_file_lock_conflict",
                    path=rel_path,
                    writer=agent_name,
                    holder=holder,
                )
    except Exception:
        pass  # Lock check is best-effort

    # Atomic write: write to temp file then rename
    def _atomic_write() -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(full_path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, 'w', encoding=encoding) as f:
                f.write(final_content)
            os.replace(tmp_path, str(full_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    await asyncio.to_thread(_atomic_write)

    # --- Zero-byte write detection with retry ---
    expected_size = len(final_content.encode(encoding, errors="replace"))
    if expected_size > 0:
        actual_size_check = (await asyncio.to_thread(full_path.stat)).st_size
        if actual_size_check == 0:
            logger.warning(
                "write_file_zero_bytes_detected",
                path=rel_path,
                expected_size=expected_size,
                attempt=1,
            )
            # Retry write once
            await asyncio.to_thread(_atomic_write)
            actual_size_retry = (await asyncio.to_thread(full_path.stat)).st_size
            if actual_size_retry == 0:
                logger.error(
                    "write_file_zero_bytes_critical",
                    path=rel_path,
                    expected_size=expected_size,
                    message=(
                        f"CRITICAL: write_file produced 0 bytes despite non-empty content "
                        f"(expected {expected_size} bytes). Possible token truncation or tool failure."
                    ),
                )
                return {
                    "success": False,
                    "output": (
                        f"❌ Write produced empty file despite non-empty content "
                        f"(expected {expected_size} bytes). Retry also failed."
                    ),
                    "data": {"path": rel_path, "expected_size": expected_size, "actual_size": 0},
                    "artifacts": [],
                }

    # --- Post-write validation (informational only, never blocks) ---
    _run_post_write_validation(rel_path, final_content)

    # Calculate final stats
    final_size = len(final_content.encode(encoding, errors="replace"))
    final_lines = len(final_content.split("\n"))
    if final_content.endswith("\n"):
        final_lines -= 1
    content_lines_added = len(content.split("\n"))
    if content.endswith("\n"):
        content_lines_added -= 1

    # Store path relative to WORKSPACE_DIR for artifacts API
    artifact_path = str(full_path.relative_to(WORKSPACE_DIR))

    # Pre-read warning check for overwrite mode
    warning = ""
    if mode == "overwrite" and file_existed:
        try:
            from app.core.execution_context import current_file_tracker
            tracker = current_file_tracker.get(None)
            if tracker:
                record = tracker._files.get(rel_path)
                if record is None or record.last_read_at is None:
                    warning = "\n⚠️ Warning: File was overwritten without being read first. Use read_file before write_file to avoid data loss."
        except Exception:
            pass

    # Track file write in FileContextTracker (fire-and-forget)
    try:
        from app.core.execution_context import current_file_tracker
        tracker = current_file_tracker.get(None)
        if tracker:
            if file_existed:
                await tracker.track_edit(rel_path, total_lines=final_lines, size_bytes=final_size)
            else:
                await tracker.track_create(rel_path, size_bytes=final_size)
    except Exception:
        pass  # Never block tool execution

    # Build diff summary output
    status = "created (new file)" if not file_existed else f"modified (was {old_lines} lines)"
    mode_display = mode
    if mode == "insert" and insert_line is not None:
        mode_display = f"insert at line {insert_line}"

    output_parts = [
        f"✏️ File written: {rel_path}",
        f"   Mode: {mode_display} | Size: {_format_size(final_size)} | Lines: {final_lines}",
        f"   Status: {status}",
        "",
        "--- Changes ---",
    ]

    if not file_existed:
        output_parts.append(f"+{content_lines_added} lines added")
    elif mode == "overwrite":
        if old_lines == final_lines:
            output_parts.append(f"~{final_lines} lines replaced")
        else:
            diff_lines = final_lines - old_lines
            sign = "+" if diff_lines > 0 else ""
            output_parts.append(f"{sign}{diff_lines} lines delta (was {old_lines}, now {final_lines})")
    elif mode == "append":
        output_parts.append(f"Lines {old_lines + 1}-{final_lines}: +{content_lines_added} lines appended")
    elif mode == "prepend":
        output_parts.append(f"Lines 1-{content_lines_added}: +{content_lines_added} lines prepended")
    elif mode == "insert":
        effective_insert = min(insert_line or 1, old_lines + 1)
        end_insert = effective_insert + content_lines_added - 1
        output_parts.append(f"Lines {effective_insert}-{end_insert}: +{content_lines_added} lines inserted")

    if warning:
        output_parts.append(warning)

    if lock_warning:
        output_parts.append(lock_warning)

    # The requested path resolved outside the workspace and was redirected to
    # ws/<basename>. Tell the agent HONESTLY — the file is NOT at the path it asked
    # for (audit 1f6decce phantom-write confusion).
    if redirected_from:
        output_parts.append(
            f"\n⚠️ PATH RELOCATED: '{redirected_from}' resolved outside the task "
            f"workspace, so the file was written to '{artifact_path}' instead (its "
            "basename at the workspace root). Use this actual path, not the requested one."
        )

    # A5: the workspace was resolved WITHOUT authoritative task context — the file
    # may have landed in another task's _work/ (auto-discovery), the CWD, or
    # _unscoped/, NOT this task's workspace. That is a phantom write (the agent
    # believes it wrote to its task ws but did not). Surface it in the output (not
    # just logs) so the agent/LLM can react, while still writing the file (no hard
    # failure, no retry-loop).
    if ws_not_authoritative:
        _record_phantom_write_metric()
        output_parts.append(
            f"\n⚠️ WORKSPACE NOT VERIFIED: no task context was set, so this file was "
            f"written to a GUESSED workspace (`{artifact_path}`), not a context-confirmed "
            "task workspace. It may have landed in another task's directory or _unscoped/ "
            "(likely a context-propagation bug). Verify the path before relying on this file."
        )

    # --- Post-write: acquire advisory lock for writing agent (auto-release after 30s) ---
    try:
        from app.core.workspace_manager import FileLock
        from app.core.execution_context import current_agent_name
        agent_name = current_agent_name.get("")
        if agent_name:
            FileLock.acquire_lock(rel_path, agent_name)
    except Exception:
        pass  # Lock acquisition is best-effort

    output = "\n".join(output_parts)

    return {
        "success": True,
        "output": output,
        "data": {
            "path": artifact_path,
            "size": final_size,
            "lines": final_lines,
            "mode": mode,
            "unscoped_fallback": ws_not_authoritative,
        },
        "artifacts": [{"type": "file", "path": artifact_path, "size": final_size}],
    }


async def handle_list_files(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """List files/directories in workspace with recursive, glob, metadata, and tree view support."""
    rel_dir = params.get("path", ".")
    recursive = params.get("recursive", False)
    glob_pattern = params.get("glob_pattern", None)
    include_metadata = params.get("include_metadata", False)
    tree_view = params.get("tree_view", False)
    max_depth = params.get("max_depth", 10)
    max_files = params.get("max_files", 500)

    # Clamp bounds
    if not isinstance(max_depth, int) or max_depth < 1:
        max_depth = 10
    if not isinstance(max_files, int) or max_files < 1:
        max_files = 500

    ws = get_task_workspace()

    path_verdict = check_path_safety(rel_dir, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        logger.warning("path_traversal_blocked", path=rel_dir[:200], reason=path_verdict.reason)
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    target = _smart_resolve_path(rel_dir, ws, WORKSPACE_DIR)
    if not target.exists():
        return {"success": False, "output": f"Directory not found: {rel_dir}", "data": None, "artifacts": []}
    if not target.is_dir():
        return {"success": False, "output": f"Not a directory: {rel_dir}", "data": None, "artifacts": []}

    # Directories to auto-exclude
    _EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

    def _format_size(size_bytes: int) -> str:
        """Format bytes to human-readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _collect_entries(base: Path, current_depth: int) -> list[dict[str, Any]]:
        """Recursively collect file/dir entries up to max_depth."""
        entries: list[dict[str, Any]] = []
        try:
            items = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return entries

        for item in items:
            # Skip excluded directories
            if item.is_dir() and item.name in _EXCLUDED_DIRS:
                continue

            rel = item.relative_to(target)
            entry: dict[str, Any] = {
                "name": item.name,
                "rel_path": str(rel).replace("\\", "/"),
                "type": "dir" if item.is_dir() else "file",
                "depth": current_depth,
            }

            if item.is_file():
                # Apply glob filter
                if glob_pattern and not fnmatch.fnmatch(item.name, glob_pattern):
                    if not fnmatch.fnmatch(str(rel).replace("\\", "/"), glob_pattern):
                        continue
                if include_metadata:
                    try:
                        stat = item.stat()
                        entry["size"] = stat.st_size
                        entry["size_human"] = _format_size(stat.st_size)
                        entry["modified_at"] = datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat()
                    except OSError:
                        entry["size"] = 0
                        entry["size_human"] = "0 B"
                        entry["modified_at"] = ""
                else:
                    try:
                        entry["size"] = item.stat().st_size
                    except OSError:
                        entry["size"] = 0
                entries.append(entry)
            elif item.is_dir():
                if include_metadata:
                    try:
                        stat = item.stat()
                        entry["modified_at"] = datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat()
                    except OSError:
                        entry["modified_at"] = ""
                entries.append(entry)

                # Recurse if needed
                if recursive and current_depth < max_depth:
                    children = _collect_entries(item, current_depth + 1)
                    entries.extend(children)

        return entries

    def _list_sync() -> list[dict[str, Any]]:
        if recursive:
            return _collect_entries(target, 1)
        else:
            return _collect_entries(target, 1)

    all_entries = await asyncio.to_thread(_list_sync)

    # Count totals before truncation
    total_files = sum(1 for e in all_entries if e["type"] == "file")
    total_dirs = sum(1 for e in all_entries if e["type"] == "dir")
    total_count = len(all_entries)

    # Truncate if needed
    truncated = total_count > max_files
    display_entries = all_entries[:max_files] if truncated else all_entries

    # Build output
    header = f"./{rel_dir} ({total_files} files, {total_dirs} dirs)"
    lines: list[str] = [header]

    if tree_view:
        lines.extend(_build_tree_output(display_entries, include_metadata))
    else:
        lines.extend(_build_flat_output(display_entries, include_metadata))

    if truncated:
        lines.append("")
        lines.append(f"(truncated, showing {max_files} of {total_count} total)")

    output = "\n".join(lines)

    return {
        "success": True,
        "output": output,
        "data": {
            "entries": display_entries,
            "total_files": total_files,
            "total_dirs": total_dirs,
            "truncated": truncated,
        },
        "artifacts": [],
    }


def _build_tree_output(entries: list[dict[str, Any]], include_metadata: bool) -> list[str]:
    """Build tree-view output lines with ├── └── │ characters."""
    lines: list[str] = []
    if not entries:
        return ["(empty)"]

    # Group entries by parent path for tree rendering
    # We'll use depth and position to determine connectors
    for i, entry in enumerate(entries):
        depth = entry["depth"]
        is_dir = entry["type"] == "dir"
        name = entry["name"]

        # Determine if this is the last entry at its depth among siblings
        is_last = _is_last_sibling(entries, i)

        # Build indent prefix
        prefix = _build_tree_prefix(entries, i)

        connector = "└── " if is_last else "├── "

        if is_dir:
            display = f"{prefix}{connector}{name}/"
        else:
            meta_str = ""
            if include_metadata:
                size_h = entry.get("size_human", "")
                mod = entry.get("modified_at", "")
                mod_short = mod[:10] if mod else ""
                meta_str = f" ({size_h}, {mod_short})"
            display = f"{prefix}{connector}{name}{meta_str}"

        lines.append(display)

    return lines


def _is_last_sibling(entries: list[dict[str, Any]], index: int) -> bool:
    """Check if entry at index is the last sibling at its depth."""
    current_depth = entries[index]["depth"]
    for j in range(index + 1, len(entries)):
        if entries[j]["depth"] < current_depth:
            return True
        if entries[j]["depth"] == current_depth:
            return False
    return True


def _build_tree_prefix(entries: list[dict[str, Any]], index: int) -> str:
    """Build the indentation prefix for tree view."""
    current_depth = entries[index]["depth"]
    if current_depth <= 1:
        return ""

    prefix_parts: list[str] = []
    for d in range(1, current_depth):
        # Check if there's a continuing branch at this depth
        has_continuation = False
        for j in range(index + 1, len(entries)):
            if entries[j]["depth"] < d:
                break
            if entries[j]["depth"] == d:
                has_continuation = True
                break
        prefix_parts.append("│   " if has_continuation else "    ")

    return "".join(prefix_parts)


def _build_flat_output(entries: list[dict[str, Any]], include_metadata: bool) -> list[str]:
    """Build flat list output."""
    lines: list[str] = []
    if not entries:
        return ["(empty)"]

    for entry in entries:
        is_dir = entry["type"] == "dir"
        rel_path = entry["rel_path"]

        if is_dir:
            lines.append(f"d {rel_path}/")
        else:
            if include_metadata:
                size_h = entry.get("size_human", "")
                mod = entry.get("modified_at", "")[:10] if entry.get("modified_at") else ""
                lines.append(f"f {rel_path} {size_h} {mod}")
            else:
                size = entry.get("size", 0)
                lines.append(f"f {rel_path} ({size}b)")

    return lines


async def handle_delete_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Xóa file/thư mục trong workspace với path traversal protection."""
    raw_path = params.get("path", "")
    ws = get_task_workspace()

    path_verdict = check_path_safety(raw_path, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        logger.warning("path_traversal_blocked", path=raw_path[:200], reason=path_verdict.reason)
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    path = _smart_resolve_path(raw_path, ws, WORKSPACE_DIR)
    if not _is_within_workspace(path):
        return {"success": False, "output": "Access denied", "data": None, "artifacts": []}
    if not path.exists():
        return {"success": False, "output": "Not found", "data": None, "artifacts": []}

    if path.is_dir():
        await asyncio.to_thread(shutil.rmtree, path)
    else:
        await asyncio.to_thread(path.unlink)
    return {"success": True, "output": f"Deleted {raw_path}", "data": None, "artifacts": []}


async def handle_create_directory(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Tạo thư mục trong workspace (task-scoped)."""
    raw_path = params.get("path", "new_dir")
    ws = get_task_workspace()
    path_verdict = check_path_safety(raw_path, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    path = _smart_resolve_path(raw_path, ws, WORKSPACE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    # An empty directory is NOT a deliverable (audit 3c44a93f bug #1): a turn that
    # only creates dirs has produced nothing. Say so plainly so neither the agent
    # nor the orchestrator counts a bare scaffold dir as progress.
    return {
        "success": True,
        "output": (
            f"Created directory: {raw_path}\n"
            f"Note: an empty directory is not a deliverable until it contains a file."
        ),
        "data": None,
        "artifacts": [],
    }


async def handle_move_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Move/rename file or directory within workspace."""
    src_path = params.get("source", "")
    dst_path = params.get("destination", "")
    ws = get_task_workspace()

    if not src_path or not dst_path:
        return {"success": False, "output": "Both source and destination are required", "data": None, "artifacts": []}

    for p_name, p_val in [("source", src_path), ("destination", dst_path)]:
        verdict = check_path_safety(p_val, str(WORKSPACE_DIR))
        if not verdict.safe:
            return {"success": False, "output": f"⚠️ {p_name} path blocked: {verdict.reason}", "data": None, "artifacts": []}

    src = _smart_resolve_path(src_path, ws, WORKSPACE_DIR)
    dst = _smart_resolve_path(dst_path, ws, WORKSPACE_DIR)

    if not src.exists():
        return {"success": False, "output": f"Source not found: {src_path}", "data": None, "artifacts": []}

    dst.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(src), str(dst))

    return {
        "success": True,
        "output": f"Moved {src_path} → {dst_path}",
        "data": {"source": src_path, "destination": dst_path},
        "artifacts": [],
    }


async def handle_copy_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Copy file or directory within workspace."""
    src_path = params.get("source", "")
    dst_path = params.get("destination", "")
    ws = get_task_workspace()

    if not src_path or not dst_path:
        return {"success": False, "output": "Both source and destination are required", "data": None, "artifacts": []}

    for p_name, p_val in [("source", src_path), ("destination", dst_path)]:
        verdict = check_path_safety(p_val, str(WORKSPACE_DIR))
        if not verdict.safe:
            return {"success": False, "output": f"⚠️ {p_name} path blocked: {verdict.reason}", "data": None, "artifacts": []}

    src = _smart_resolve_path(src_path, ws, WORKSPACE_DIR)
    # Fallback to global workspace for source (read)
    if not src.exists() and ws != WORKSPACE_DIR:
        src = WORKSPACE_DIR / src_path
    dst = _smart_resolve_path(dst_path, ws, WORKSPACE_DIR)

    if not src.exists():
        return {"success": False, "output": f"Source not found: {src_path}", "data": None, "artifacts": []}

    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        await asyncio.to_thread(shutil.copytree, str(src), str(dst), dirs_exist_ok=True)
    else:
        await asyncio.to_thread(shutil.copy2, str(src), str(dst))

    size = (await asyncio.to_thread(dst.stat)).st_size if dst.is_file() else 0
    return {
        "success": True,
        "output": f"Copied {src_path} → {dst_path}",
        "data": {"source": src_path, "destination": dst_path, "size": size},
        "artifacts": [],
    }


async def handle_glob_files(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Find files matching a glob pattern within workspace."""
    pattern = params.get("pattern", "")
    rel_dir = params.get("path", ".")
    max_results = params.get("max_results", 100)
    include_hidden = params.get("include_hidden", False)

    if not pattern:
        return {"success": False, "output": "⚠️ pattern is required", "data": None, "artifacts": []}

    if not isinstance(max_results, int) or max_results < 1:
        max_results = 100

    ws = get_task_workspace()

    path_verdict = check_path_safety(rel_dir, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        logger.warning("path_traversal_blocked", path=rel_dir[:200], reason=path_verdict.reason)
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    base = _smart_resolve_path(rel_dir, ws, WORKSPACE_DIR)
    # Fallback to global workspace if not found in task workspace
    if not base.exists() and ws != WORKSPACE_DIR:
        base = WORKSPACE_DIR / rel_dir
    if not base.exists():
        return {"success": False, "output": f"Directory not found: {rel_dir}", "data": None, "artifacts": []}
    if not base.is_dir():
        return {"success": False, "output": f"Not a directory: {rel_dir}", "data": None, "artifacts": []}

    _EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}

    def _glob_sync() -> list[str]:
        results: list[str] = []
        try:
            for match in base.glob(pattern):
                if not match.is_file():
                    continue

                # Check excluded directories
                parts = match.relative_to(base).parts
                if any(part in _EXCLUDED_DIRS for part in parts):
                    continue

                # Hidden file/dir filtering
                if not include_hidden:
                    skip = False
                    for part in parts:
                        if part.startswith(".") and not part.startswith(".env"):
                            skip = True
                            break
                    if skip:
                        continue

                rel = str(match.relative_to(base)).replace("\\", "/")
                results.append(rel)
        except (OSError, ValueError):
            pass
        results.sort()
        return results

    all_matches = await asyncio.to_thread(_glob_sync)
    total_found = len(all_matches)
    truncated = total_found > max_results
    display_matches = all_matches[:max_results] if truncated else all_matches

    # Build output
    if total_found == 0:
        output = f"🔍 Glob: {pattern} (no matches found)"
    elif truncated:
        header = f"🔍 Glob: {pattern} (showing {max_results} of {total_found} matches)"
        body = "\n".join(display_matches)
        output = f"{header}\n\n{body}\n\n(truncated at {max_results} results)"
    else:
        header = f"🔍 Glob: {pattern} (found {total_found} files)"
        body = "\n".join(display_matches)
        output = f"{header}\n\n{body}"

    return {
        "success": True,
        "output": output,
        "data": {
            "pattern": pattern,
            "matches": display_matches,
            "total_found": total_found,
            "truncated": truncated,
        },
        "artifacts": [],
    }


async def handle_get_file_info(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Get detailed file/directory metadata."""
    raw_path = params.get("path", "")
    ws = get_task_workspace()

    path_verdict = check_path_safety(raw_path, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        logger.warning("path_traversal_blocked", path=raw_path[:200], reason=path_verdict.reason)
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    path = _smart_resolve_path(raw_path, ws, WORKSPACE_DIR)
    # Fallback to global workspace if not found in task workspace
    if not path.exists() and ws != WORKSPACE_DIR:
        path = WORKSPACE_DIR / raw_path
    if not _is_within_workspace(path):
        return {"success": False, "output": "Access denied: outside workspace", "data": None, "artifacts": []}

    if not path.exists():
        return {
            "success": True,
            "output": f"ℹ️ File Info: {raw_path}\n\n   Path does not exist.",
            "data": {"path": raw_path, "exists": False},
            "artifacts": [],
        }

    def _human_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _get_info_sync() -> dict[str, Any]:
        info: dict[str, Any] = {}
        info["name"] = path.name
        info["path"] = raw_path
        info["exists"] = True

        # Determine type
        if path.is_symlink():
            info["type"] = "symlink"
        elif path.is_dir():
            info["type"] = "directory"
        else:
            info["type"] = "file"

        try:
            stat = path.stat()
        except OSError:
            info["error"] = "Cannot stat path"
            return info

        # Timestamps
        info["created"] = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
        info["modified"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        # Permissions
        info["readable"] = os.access(str(path), os.R_OK)
        info["writable"] = os.access(str(path), os.W_OK)

        if info["type"] == "directory":
            # Directory-specific info
            file_count = 0
            dir_count = 0
            total_size = 0
            try:
                for item in path.rglob("*"):
                    if item.is_file():
                        file_count += 1
                        try:
                            total_size += item.stat().st_size
                        except OSError:
                            pass
                    elif item.is_dir():
                        dir_count += 1
            except (OSError, PermissionError):
                pass
            info["files"] = file_count
            info["subdirectories"] = dir_count
            info["total_size"] = total_size
            info["total_size_human"] = _human_size(total_size)
        else:
            # File-specific info
            info["size"] = stat.st_size
            info["size_human"] = _human_size(stat.st_size)
            info["extension"] = path.suffix
            info["mime_type"] = mimetypes.guess_type(str(path))[0] or "unknown"

            # Binary detection (check first 8KB for null bytes)
            is_binary = False
            try:
                with open(path, "rb") as f:
                    chunk = f.read(8192)
                    is_binary = b"\x00" in chunk
            except OSError:
                pass
            info["is_binary"] = is_binary

            # Encoding detection and line count (only for non-binary)
            if not is_binary:
                encoding = "utf-8"
                line_count = 0
                try:
                    with open(path, "r", encoding="utf-8", errors="strict") as f:
                        for _ in f:
                            line_count += 1
                    encoding = "utf-8"
                except UnicodeDecodeError:
                    try:
                        with open(path, "r", encoding="ascii", errors="strict") as f:
                            line_count = 0
                            for _ in f:
                                line_count += 1
                        encoding = "ascii"
                    except UnicodeDecodeError:
                        with open(path, "r", encoding="latin-1") as f:
                            line_count = 0
                            for _ in f:
                                line_count += 1
                        encoding = "latin-1"
                except OSError:
                    encoding = "unknown"
                info["encoding"] = encoding
                info["lines"] = line_count
            else:
                info["encoding"] = None
                info["lines"] = None

            # Git status (optional)
            git_status = None
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=str(path.parent),
                )
                if result.returncode == 0 and result.stdout.strip():
                    status_code = result.stdout.strip()[:2]
                    status_map = {
                        "M ": "modified (staged)",
                        " M": "modified (unstaged)",
                        "MM": "modified (staged + unstaged)",
                        "A ": "added (staged)",
                        "??": "untracked",
                        "D ": "deleted (staged)",
                        " D": "deleted (unstaged)",
                        "R ": "renamed",
                        "C ": "copied",
                        "UU": "unmerged",
                    }
                    git_status = status_map.get(status_code, f"status: {status_code.strip()}")
                elif result.returncode == 0:
                    git_status = "clean"
            except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
                pass
            info["git_status"] = git_status

        return info

    info = await asyncio.to_thread(_get_info_sync)

    # Build formatted output
    if info["type"] == "directory":
        output_lines = [
            f"ℹ️ Directory Info: {raw_path}/",
            "",
            f"   Type:       directory",
            f"   Items:      {info.get('files', 0)} files, {info.get('subdirectories', 0)} subdirectories",
            f"   Total Size: {info.get('total_size_human', '0 B')} (recursive)",
            f"   Readable:   {'yes' if info.get('readable') else 'no'}",
            f"   Writable:   {'yes' if info.get('writable') else 'no'}",
            f"   Created:    {info.get('created', 'unknown')}",
            f"   Modified:   {info.get('modified', 'unknown')}",
        ]
    else:
        binary_str = "yes" if info.get("is_binary") else "no"
        lines_str = str(info.get("lines", "N/A")) if info.get("lines") is not None else "N/A (binary)"
        encoding_str = info.get("encoding") or "N/A (binary)"
        git_str = info.get("git_status") or "N/A"

        output_lines = [
            f"ℹ️ File Info: {raw_path}",
            "",
            f"   Type:       {info.get('type', 'file')}",
            f"   Size:       {info.get('size_human', '0 B')} ({info.get('size', 0):,} bytes)",
            f"   Lines:      {lines_str}",
            f"   Extension:  {info.get('extension', '')}",
            f"   MIME:       {info.get('mime_type', 'unknown')}",
            f"   Encoding:   {encoding_str}",
            f"   Binary:     {binary_str}",
            f"   Readable:   {'yes' if info.get('readable') else 'no'}",
            f"   Writable:   {'yes' if info.get('writable') else 'no'}",
            f"   Created:    {info.get('created', 'unknown')}",
            f"   Modified:   {info.get('modified', 'unknown')}",
            f"   Git Status: {git_str}",
        ]

    output = "\n".join(output_lines)

    return {
        "success": True,
        "output": output,
        "data": info,
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

# ══════════════════════════════════════════════════════════════════
# File Discovery & Workspace Info Utilities
# ══════════════════════════════════════════════════════════════════


async def handle_find_workspace_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Search for a file across ALL task workspaces and the global workspace.

    Useful when agents cannot find files via list_files/read_file due to
    path resolution issues (common in Docker environments where CWD differs).

    Searches:
    1. Current task _context/ and _work/
    2. All other task directories under WORKSPACE_DIR/tasks/
    3. WORKSPACE_DIR root (fallback)
    """
    filename = params.get("filename", "")
    if not filename:
        return {"success": False, "output": "filename is required", "data": None, "artifacts": []}

    matches: list[dict[str, Any]] = []

    def _search_dir(directory: Path, label: str) -> None:
        """Recursively search directory for filename match."""
        if not directory.exists():
            return
        try:
            for item in directory.rglob("*"):
                if item.is_file() and item.name == filename:
                    try:
                        rel = str(item.relative_to(WORKSPACE_DIR))
                    except ValueError:
                        rel = str(item)
                    matches.append({
                        "path": str(item),
                        "relative_to_workspace": rel,
                        "size": item.stat().st_size,
                        "label": label,
                    })
        except (OSError, PermissionError):
            pass

    # 1. Search current task workspace
    current_ws = get_task_workspace()
    if current_ws:
        _search_dir(current_ws, "current_task_work")
        _search_dir(current_ws.parent / "_context", "current_task_context")

    # 2. Search all task directories
    tasks_dir = WORKSPACE_DIR / "tasks"
    if tasks_dir.exists():
        try:
            for entry in sorted(tasks_dir.iterdir()):
                if not entry.is_dir() or len(entry.name) != 8:
                    continue
                for subdir in ["_work", "_context"]:
                    _search_dir(entry / subdir, f"task_{entry.name}_{subdir}")
        except (OSError, PermissionError):
            pass

    # 3. Search workspace root
    _search_dir(WORKSPACE_DIR, "workspace_root")

    # 4. Check project root whitelist (developer_profile.json, etc.)
    if not matches:
        root_file = _try_project_root_read(filename)
        if root_file:
            matches.append({
                "path": str(root_file),
                "relative_to_workspace": f"(project root) {filename}",
                "size": root_file.stat().st_size,
                "label": "project_root",
            })

    if not matches:
        return {
            "success": True,
            "output": f"No file named '{filename}' found anywhere in workspace.",
            "data": {"filename": filename, "matches": []},
            "artifacts": [],
        }

    # Deduplicate by path
    seen = set()
    unique = []
    for m in matches:
        if m["path"] not in seen:
            seen.add(m["path"])
            unique.append(m)

    lines = [f"🔍 Found {len(unique)} match(es) for '{filename}':", ""]
    for m in unique:
        size_str = f"{m['size'] / 1024:.1f} KB" if m['size'] >= 1024 else f"{m['size']} B"
        lines.append(f"  📄 {m['relative_to_workspace']}  ({size_str})  [{m['label']}]")

    return {
        "success": True,
        "output": "\n".join(lines),
        "data": {"filename": filename, "matches": unique},
        "artifacts": [],
    }


async def handle_get_workspace_info(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Show current workspace path information.

    Useful for debugging path resolution issues. Shows:
    - Task workspace path (where file tools resolve to)
    - CWD (current working directory of shell)
    - Whether paths match
    - All discovered task directories
    """
    import os as _os

    current_ws = get_task_workspace()
    cwd = Path.cwd()
    ws_dir = WORKSPACE_DIR

    lines = [
        "📂 Workspace Information:",
        "",
        f"  WORKSPACE_DIR env:     {_os.environ.get('WORKSPACE_DIR', '(not set)')}",
        f"  Resolved task _work/:  {current_ws}",
        f"  Shell CWD (cwd):       {cwd}",
        f"  CWD exists in ws:      {'YES' if current_ws and cwd.resolve() == current_ws.resolve() else 'NO'}",
        "",
    ]

    # Task ID from context
    try:
        from app.core.execution_context import current_task_id, current_parent_task_id
        tid = current_task_id.get("(not set)")
        pid = current_parent_task_id.get("(not set)")
        lines.append(f"  Task ID:               {tid}")
        lines.append(f"  Parent Task ID:        {pid}")
    except Exception:
        lines.append("  (execution context not available)")

    lines.append("")
    lines.append("  Discovered task directories:")

    tasks_dir = ws_dir / "tasks"
    if tasks_dir.exists():
        try:
            for entry in sorted(tasks_dir.iterdir()):
                if entry.is_dir() and len(entry.name) == 8:
                    work_files = list((entry / "_work").glob("*")) if (entry / "_work").exists() else []
                    ctx_files = list((entry / "_context").glob("*")) if (entry / "_context").exists() else []
                    lines.append(
                        f"    {entry.name}/  "
                        f"(_work: {len(work_files)} files, _context: {len(ctx_files)} files)"
                    )
        except (OSError, PermissionError):
            lines.append("    (cannot list)")
    else:
        lines.append("    (no tasks directory found)")

    return {
        "success": True,
        "output": "\n".join(lines),
        "data": {
            "task_workspace": str(current_ws),
            "cwd": str(cwd),
            "workspace_dir": str(ws_dir),
        },
        "artifacts": [],
    }


# ══════════════════════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════════════════════

_TOOLSET = "filesystem"

# FIX: GAP-1 - Enhanced tool descriptions for better agent tool selection
registry.register(
    name="read_file",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Read the contents of a file at the specified path. Returns content with line numbers and file info header. "
            "Use this when you need to examine existing code, configuration, or any text file. "
            "ALWAYS use this before editing a file to understand its current state. "
            "Use offset/limit to paginate large files (default: 500 lines from line 1). "
            "Supports custom encoding with graceful fallback to latin-1 on decode errors."
            "\n\n"
            "⚠️ IMPORTANT: This tool resolves paths relative to the TASK WORKSPACE, not your shell's CWD. "
            "If you get 'File not found' for a path that should exist: (1) try find_workspace_file to search "
            "across ALL workspaces, or (2) use get_workspace_info to debug path resolution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "offset": {"type": "integer", "description": "Start line number (1-based, default: 1)", "default": 1},
                "limit": {"type": "integer", "description": "Maximum lines to return (default: 500, max: 2000)", "default": 500},
                "encoding": {
                    "type": "string",
                    "description": "File encoding (default: 'utf-8'). Common: 'utf-8', 'latin-1', 'ascii', 'utf-16', 'shift_jis'",
                    "default": "utf-8",
                },
                "show_line_numbers": {
                    "type": "boolean",
                    "description": "Show line numbers in output (default: true)",
                    "default": True,
                },
            },
            "required": ["path"],
        },
    },
    handler=handle_read_file,
    is_async=True,
    emoji="📖",
)

registry.register(
    name="write_file",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Write content to a file with multiple modes. The primary tool for creating new files "
            "or replacing entire file content.\n\n"
            "MODES:\n"
            "- 'overwrite' (default): Replace entire file content. Best for creating new files.\n"
            "- 'append': Add content to end of file. Best for logs, growing documents.\n"
            "- 'prepend': Add content to beginning of file.\n"
            "- 'insert': Insert content at a specific line number.\n\n"
            "GUIDANCE:\n"
            "- To CREATE a new file: use write_file with mode='overwrite' (directories auto-created)\n"
            "- To ADD content to end: use write_file with mode='append'\n"
            "- To EDIT specific parts of an existing file: use edit_file instead (surgical search/replace)\n"
            "- edit_file supports: single edit, batch edits, occurrence selector, dry_run preview\n\n"
            "Uses atomic writes (temp file + rename) for data safety. "
            "Warns if overwriting a file that hasn't been read first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "content": {"type": "string", "description": "Content to write"},
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append", "insert", "prepend"],
                    "description": "Write mode: 'overwrite' (default) replaces entire file, 'append' adds to end, 'prepend' adds to beginning, 'insert' inserts at specified line",
                    "default": "overwrite",
                },
                "insert_line": {
                    "type": "integer",
                    "description": "Line number to insert content at (1-based, only used when mode='insert')",
                },
                "encoding": {
                    "type": "string",
                    "description": "File encoding (default: 'utf-8')",
                    "default": "utf-8",
                },
                "create_dirs": {
                    "type": "boolean",
                    "description": "Auto-create parent directories if they don't exist (default: true)",
                    "default": True,
                },
            },
            "required": ["path", "content"],
        },
    },
    handler=handle_write_file,
    is_async=True,
    emoji="✏️",
)

registry.register(
    name="list_files",
    toolset=_TOOLSET,
    schema={
        "description": (
            "List files and directories at the specified path. Supports recursive listing, "
            "glob filtering, metadata (size/date), and tree view output. "
            "Use this to understand project structure before reading specific files. "
            "Auto-excludes .git/, node_modules/, __pycache__/, .venv/, venv/. "
            "Prefer this over running shell commands like 'ls' or 'dir'."
            "\n\n"
            "⚠️ IMPORTANT: This tool resolves paths relative to the TASK WORKSPACE, not your shell's CWD. "
            "If you get 'Directory not found' for a path that should exist, the issue is likely a "
            "path resolution mismatch (common in Docker environments). Use get_workspace_info first "
            "to see the actual paths in use, then try find_workspace_file to locate the file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace",
                    "default": ".",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List files recursively (default: false)",
                    "default": False,
                },
                "glob_pattern": {
                    "type": "string",
                    "description": "Filter files by glob pattern (e.g., '*.py', '*.ts')",
                },
                "include_metadata": {
                    "type": "boolean",
                    "description": "Include file size and modified time (default: false)",
                    "default": False,
                },
                "tree_view": {
                    "type": "boolean",
                    "description": "Return as tree structure with indentation (default: false)",
                    "default": False,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum directory depth for recursive listing (default: 10)",
                    "default": 10,
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default: 500)",
                    "default": 500,
                },
            },
        },
    },
    handler=handle_list_files,
    is_async=True,
    emoji="📂",
)

registry.register(
    name="delete_file",
    toolset=_TOOLSET,
    schema={
        "description": "Delete a file or directory from the workspace",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to delete"}},
            "required": ["path"],
        },
    },
    handler=handle_delete_file,
    is_async=True,
    emoji="🗑️",
)

registry.register(
    name="create_directory",
    toolset=_TOOLSET,
    schema={
        "description": "Create a directory in the workspace",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path to create"}},
            "required": ["path"],
        },
    },
    handler=handle_create_directory,
    is_async=True,
    emoji="📁",
)

registry.register(
    name="move_file",
    toolset=_TOOLSET,
    schema={
        "description": "Move or rename a file/directory within the workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source path (relative to workspace)"},
                "destination": {"type": "string", "description": "Destination path (relative to workspace)"},
            },
            "required": ["source", "destination"],
        },
    },
    handler=handle_move_file,
    is_async=True,
    emoji="📦",
)

registry.register(
    name="copy_file",
    toolset=_TOOLSET,
    schema={
        "description": "Copy a file or directory within the workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source path (relative to workspace)"},
                "destination": {"type": "string", "description": "Destination path (relative to workspace)"},
            },
            "required": ["source", "destination"],
        },
    },
    handler=handle_copy_file,
    is_async=True,
    emoji="📋",
)

registry.register(
    name="glob_files",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Find files matching a glob pattern. Fast way to discover files by name pattern "
            "across the workspace. Returns sorted list of matching file paths. "
            "Auto-excludes .git/, node_modules/, __pycache__/, .venv/, venv/, .mypy_cache/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g., '**/*.py', 'src/**/*.ts', '*.json', 'tests/test_*.py')",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to search from (default: workspace root)",
                    "default": ".",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 100)",
                    "default": 100,
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files/directories starting with dot (default: false)",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    },
    handler=handle_glob_files,
    is_async=True,
    emoji="🔍",
)

registry.register(
    name="find_workspace_file",
    toolset=_TOOLSET,
    schema={
        "description": (
            "SEARCH for a specific filename across ALL task workspaces and global workspace. "
            "Use this when read_file or list_files return 'not found' but you believe the file exists. "
            "This is the ULTIMATE file finder — it scans every corner of the filesystem "
            "including Docker containers where the CWD may differ from the task workspace. "
            "Returns ALL matches with their locations. If it returns nothing, the file truly doesn't exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Exact filename to search for (e.g., 'FINDINGS.md', 'TASK_BRIEF.md')",
                },
            },
            "required": ["filename"],
        },
    },
    handler=handle_find_workspace_file,
    is_async=True,
    emoji="🔍",
)

registry.register(
    name="get_workspace_info",
    toolset=_TOOLSET,
    schema={
        "description": (
            "DIAGNOSTIC: Show current workspace path resolution information. "
            "Use this when file tools behave unexpectedly — it shows the actual paths "
            "being used so you can debug path mismatch issues. "
            "Displays: task workspace path, shell CWD, WORKSPACE_DIR env var, "
            "discovered task directories, and task/parent context IDs."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=handle_get_workspace_info,
    is_async=True,
    emoji="📂",
)

registry.register(
    name="get_file_info",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Get detailed metadata about a file or directory without reading its content. "
            "Returns size, type, permissions, timestamps, line count, encoding detection, and git status. "
            "Use this when you need to check file properties before deciding how to process it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory path relative to workspace",
                },
            },
            "required": ["path"],
        },
    },
    handler=handle_get_file_info,
    is_async=True,
    emoji="ℹ️",
)
