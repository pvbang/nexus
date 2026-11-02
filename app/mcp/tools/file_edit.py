"""
Nexus - Unified File Edit Tool.

The single canonical tool for surgical text editing in files.
Consolidates functionality from: edit_file, apply_edits, apply_diff,
apply_multiple_diffs, and patch_file into one unified API.

Edit modes (auto-detected from params, in priority order):
    - string  : old_string + new_string — exact surgical replace (DEFAULT)
    - batch   : edits[] — multiple string edits, atomic all-or-nothing
    - regex   : pattern + replacement — regex search/replace with \\1 groups
    - line    : start_line + end_line + expected_text — replace/delete a line span
    - anchor  : (after | before) + new_string — insert text relative to a marker

Features:
    - Whitespace-tolerant matching: if an exact match fails, retries by
      comparing lines with leading/trailing whitespace normalized, so an
      indentation mismatch no longer dooms an otherwise-correct edit.
    - Near-miss suggestions: when nothing matches, the error quotes the most
      similar region of the file so the agent can self-correct.
    - Occurrence selector (0=all, 1=first, 2=second, etc.)
    - Atomic writes via temp file + os.replace()
    - CRLF normalization before matching
    - create_if_missing for new file creation
    - dry_run mode for previewing changes
    - All-or-nothing transactional semantics for batch edits
"""

import asyncio
import difflib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry
from app.mcp.tools.filesystem import WORKSPACE_DIR, get_task_workspace

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalize_line_endings(text: str) -> str:
    """Normalize CRLF → LF for consistent matching."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


async def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically: write to temp file, then os.replace() to target."""

    def _write_sync() -> None:
        dir_path = path.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f".{path.name}_",
            dir=str(dir_path),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    await asyncio.to_thread(_write_sync)


async def _track_file_edit(raw_path: str, content_size: int) -> None:
    """Track file edit in FileContextTracker (fire-and-forget)."""
    try:
        from app.core.execution_context import current_file_tracker

        tracker = current_file_tracker.get(None)
        if tracker:
            await tracker.track_edit(raw_path, size_bytes=content_size)
    except Exception:
        pass  # Never block tool execution


def _validate_path(raw_path: str, ws: Path) -> dict[str, Any] | None:
    """Validate path safety and workspace bounds. Returns error dict or None if OK."""
    if not raw_path:
        return {
            "success": False,
            "output": "⚠️ 'path' parameter is required.",
            "data": None,
            "artifacts": [],
        }

    path_verdict = check_path_safety(raw_path, str(ws))
    if not path_verdict.safe:
        logger.warning("path_traversal_blocked", path=raw_path[:200], reason=path_verdict.reason)
        return {
            "success": False,
            "output": f"⚠️ Path blocked: {path_verdict.reason}",
            "data": None,
            "artifacts": [],
        }

    resolved = (ws / raw_path).resolve()
    if not str(resolved).startswith(str(WORKSPACE_DIR.resolve())):
        return {
            "success": False,
            "output": "Access denied: path resolves outside workspace.",
            "data": None,
            "artifacts": [],
        }

    return None


def _resolve_file(raw_path: str, ws: Path) -> Path:
    """Resolve file path via the shared resolver.

    Uses ``filesystem._smart_resolve_path`` so every path shape an agent may
    pass (relative, ``workspace_data/...`` prefix, ``tasks/...`` prefix,
    absolute, ``../``) resolves the SAME place read_file/write_file do. The old
    ``ws / raw_path`` did not strip the ``workspace_data/`` prefix, so a
    full-prefix path returned a false "File not found" — a root cause of the
    task 3c44a93f 6-turn stall (bug #3). The legacy ``WORKSPACE_DIR / raw_path``
    fallback is kept for the case where smart-resolve lands on a not-yet-created
    path but the file actually exists at the workspace root.
    """
    try:
        from app.mcp.tools.filesystem import _smart_resolve_path
        path = _smart_resolve_path(raw_path, ws, WORKSPACE_DIR)
    except Exception:
        path = ws / raw_path
    if not path.exists() and ws != WORKSPACE_DIR:
        fallback = WORKSPACE_DIR / raw_path
        if fallback.exists():
            return fallback
    return path


def _find_line_number(content: str, position: int) -> int:
    """Find the 1-based line number for a character position."""
    return content[:position].count("\n") + 1


def _find_fuzzy_spans(content: str, old_string: str) -> list[tuple[int, int]]:
    """Find spans matching old_string ignoring per-line leading/trailing whitespace.

    Returns a list of (start, end) char spans in `content` whose lines match
    old_string's lines after each line is stripped. Used as a fallback ONLY
    when exact matching finds nothing, so indentation drift between what the
    agent typed and what's on disk doesn't doom an otherwise-correct edit.
    """
    old_lines = old_string.split("\n")
    content_lines = content.split("\n")
    n = len(old_lines)
    if n == 0 or n > len(content_lines):
        return []

    stripped_old = [ln.strip() for ln in old_lines]

    # Precompute char offset of the start of each content line.
    line_starts: list[int] = []
    pos = 0
    for ln in content_lines:
        line_starts.append(pos)
        pos += len(ln) + 1  # +1 for the '\n'

    spans: list[tuple[int, int]] = []
    for i in range(len(content_lines) - n + 1):
        window = content_lines[i:i + n]
        if [ln.strip() for ln in window] == stripped_old:
            start = line_starts[i]
            end = line_starts[i] + sum(len(ln) + 1 for ln in window) - 1
            spans.append((start, end))
    return spans


def _suggest_near_miss(content: str, old_string: str) -> str:
    """Build a hint quoting the file region most similar to old_string.

    Helps the agent self-correct after a failed match instead of blindly
    retrying. Returns an empty string when nothing is reasonably close.
    """
    target = old_string.strip()
    if not target:
        return ""

    content_lines = content.split("\n")
    first_target_line = target.split("\n", 1)[0]

    best_ratio = 0.0
    best_idx = -1
    matcher = difflib.SequenceMatcher(a=first_target_line)
    for idx, line in enumerate(content_lines):
        matcher.set_seq2(line.strip())
        ratio = matcher.quick_ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = idx

    if best_idx < 0 or best_ratio < 0.5:
        return ""

    span = old_string.count("\n") + 1
    start = best_idx
    end = min(len(content_lines), best_idx + span)
    excerpt = "\n".join(
        f"{start + 1 + j}| {content_lines[start + j]}"
        for j in range(end - start)
    )
    return (
        f" Closest region (line {best_idx + 1}, ~{int(best_ratio * 100)}% similar):\n{excerpt}"
    )


def _apply_single_edit(
    content: str, old_string: str, new_string: str, occurrence: int
) -> tuple[str | None, str | None, int]:
    """Apply a single string edit to content.

    Tries an exact match first; if that finds nothing, falls back to a
    whitespace-tolerant line match. Returns (new_content, error, replacements);
    new_content is None on error.
    """
    match_count = content.count(old_string)

    if match_count == 0:
        # Fallback: whitespace-tolerant match before giving up.
        fuzzy_spans = _find_fuzzy_spans(content, old_string)
        if fuzzy_spans:
            if occurrence == 0:
                # Replace every fuzzy span (right-to-left to keep offsets valid).
                new_content = content
                for start, end in reversed(fuzzy_spans):
                    new_content = new_content[:start] + new_string + new_content[end:]
                return new_content, None, len(fuzzy_spans)
            if occurrence == 1 and len(fuzzy_spans) > 1:
                return None, (
                    f"Found {len(fuzzy_spans)} whitespace-tolerant matches "
                    f"(must be unique when occurrence=1). Set occurrence=0 for all, "
                    f"or add more context to make old_string unique."
                ), 0
            target = occurrence if occurrence >= 1 else 1
            if target > len(fuzzy_spans):
                return None, (
                    f"Occurrence {occurrence} requested but only "
                    f"{len(fuzzy_spans)} (whitespace-tolerant) found."
                ), 0
            start, end = fuzzy_spans[target - 1]
            return content[:start] + new_string + content[end:], None, 1

        snippet = old_string[:100].replace("\n", "\\n")
        hint = _suggest_near_miss(content, old_string)
        return None, (
            f"No match found. "
            f"Searched for: \"{snippet}{'...' if len(old_string) > 100 else ''}\"."
            f"{hint}"
        ), 0

    if occurrence == 0:
        # Replace all
        new_content = content.replace(old_string, new_string)
        return new_content, None, match_count
    elif occurrence == 1 and match_count > 1:
        # Default occurrence=1 with multiple matches: require uniqueness
        # unless explicitly set (handled by caller for batch mode)
        return None, (
            f"Found {match_count} matches (must be unique when occurrence=1). "
            f"Set occurrence=0 for all, or provide more context to make old_string unique."
        ), 0
    elif occurrence > match_count:
        return None, (
            f"Occurrence {occurrence} requested but only {match_count} found."
        ), 0
    else:
        # Replace the N-th occurrence
        pos = -1
        for _ in range(occurrence):
            pos = content.find(old_string, pos + 1)
        new_content = content[:pos] + new_string + content[pos + len(old_string):]
        return new_content, None, 1


def _apply_regex_edit(
    content: str, pattern: str, replacement: str, occurrence: int
) -> tuple[str | None, str | None, int]:
    """Apply a regex search/replace. occurrence: 0=all, N=Nth match (1-based)."""
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error as e:
        return None, f"Invalid regex pattern: {e}", 0

    matches = list(rx.finditer(content))
    if not matches:
        return None, f"Regex pattern matched nothing: /{pattern[:100]}/", 0

    if occurrence == 0:
        new_content, count = rx.subn(replacement, content)
        return new_content, None, count

    target = occurrence if occurrence >= 1 else 1
    if target > len(matches):
        return None, (
            f"Occurrence {occurrence} requested but regex matched "
            f"only {len(matches)} time(s)."
        ), 0
    m = matches[target - 1]
    try:
        expanded = m.expand(replacement)
    except re.error as e:
        return None, f"Invalid replacement template: {e}", 0
    return content[:m.start()] + expanded + content[m.end():], None, 1


def _apply_line_edit(
    content: str,
    start_line: int,
    end_line: int,
    new_string: str,
    expected_text: str | None,
) -> tuple[str | None, str | None, int]:
    """Replace (or delete) the inclusive line span [start_line, end_line].

    Requires expected_text to match the current content of that span (after
    stripping) as a guard against stale line numbers. new_string="" deletes
    the span entirely.
    """
    lines = content.split("\n")
    total = len(lines)

    if start_line < 1 or end_line < start_line:
        return None, (
            f"Invalid line range: start_line={start_line}, end_line={end_line} "
            f"(must satisfy 1 <= start_line <= end_line)."
        ), 0
    if start_line > total:
        return None, f"start_line {start_line} is beyond end of file ({total} lines).", 0

    end = min(end_line, total)
    current_span = "\n".join(lines[start_line - 1:end])

    if expected_text is None:
        return None, (
            "line mode requires 'expected_text' (the current content of "
            "lines start_line..end_line) to guard against stale line numbers."
        ), 0

    if current_span.strip() != _normalize_line_endings(expected_text).strip():
        actual_preview = "\n".join(
            f"{start_line + i}| {ln}" for i, ln in enumerate(lines[start_line - 1:end])
        )
        return None, (
            f"expected_text does not match lines {start_line}-{end_line}. "
            f"The file may have changed — re-read it. Current content:\n{actual_preview}"
        ), 0

    if new_string == "":
        # Delete the span.
        new_lines = lines[:start_line - 1] + lines[end:]
    else:
        new_lines = lines[:start_line - 1] + new_string.split("\n") + lines[end:]
    return "\n".join(new_lines), None, 1


def _apply_anchor_edit(
    content: str,
    after: str | None,
    before: str | None,
    new_string: str,
    occurrence: int,
) -> tuple[str | None, str | None, int]:
    """Insert new_string immediately after `after` or before `before`.

    The anchor text itself is preserved. Exactly one of after/before is used
    (after takes priority). occurrence selects which anchor when repeated.
    """
    anchor = after if after is not None else before
    if not anchor:
        return None, "anchor mode requires non-empty 'after' or 'before'.", 0

    match_count = content.count(anchor)
    if match_count == 0:
        hint = _suggest_near_miss(content, anchor)
        return None, f"Anchor text not found: \"{anchor[:80]}\".{hint}", 0
    if occurrence == 1 and match_count > 1:
        return None, (
            f"Anchor found {match_count} times (must be unique when occurrence=1). "
            f"Add more context or set occurrence to target a specific one."
        ), 0

    target = occurrence if occurrence >= 1 else 1
    if target > match_count:
        return None, (
            f"Occurrence {occurrence} requested but anchor found only {match_count} time(s)."
        ), 0

    pos = -1
    for _ in range(target):
        pos = content.find(anchor, pos + 1)

    if after is not None:
        insert_at = pos + len(anchor)
    else:
        insert_at = pos
    return content[:insert_at] + new_string + content[insert_at:], None, 1


def _build_edit_summary(
    content: str, old_string: str, new_string: str, edit_idx: int | None = None
) -> str:
    """Build a brief summary line for one edit."""
    pos = content.find(old_string) if old_string in content else -1
    line_num = _find_line_number(content, pos) if pos >= 0 else "?"

    old_lines = old_string.splitlines()
    new_lines = new_string.splitlines()

    prefix = f"Edit {edit_idx}: " if edit_idx is not None else ""

    if not new_string:
        # Deletion
        old_preview = old_string[:60].replace("\n", "\\n")
        return f"{prefix}Line {line_num} — deleted \"{old_preview}\""
    elif len(old_lines) == 1 and len(new_lines) == 1:
        old_preview = old_string[:40].replace("\n", "\\n")
        new_preview = new_string[:40].replace("\n", "\\n")
        return f"{prefix}Line {line_num} — \"{old_preview}\" → \"{new_preview}\""
    else:
        return f"{prefix}Line {line_num} — replaced {len(old_lines)} line(s) with {len(new_lines)} line(s)"


# ------------------------------------------------------------------
# Main Handler
# ------------------------------------------------------------------


async def handle_edit_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Unified edit tool — supports single edit OR batch edits.

    Mode detection:
        1. If "edits" array provided → batch mode
        2. If "old_string"/"new_string" provided → single edit mode
        3. Neither → error

    Features:
        - Atomic write (temp file + os.replace)
        - Occurrence selector (0=all, N=Nth)
        - Batch edits with sequential application
        - create_if_missing — create file if not found
        - dry_run — preview changes without writing
        - CRLF normalization
    """
    raw_path = params.get("path", "")
    edits_param = params.get("edits")
    old_string_param = params.get("old_string")
    new_string_param = params.get("new_string")
    occurrence_param = params.get("occurrence", 1)
    create_if_missing = params.get("create_if_missing", False)
    dry_run = params.get("dry_run", False)

    ws = get_task_workspace()

    # --- Validate path ---
    path_err = _validate_path(raw_path, ws)
    if path_err:
        return path_err

    path = _resolve_file(raw_path, ws)

    # --- Determine edit mode (priority: batch > regex > line > anchor > string) ---
    if edits_param is not None:
        # Batch mode — each item is a string edit (old_string/new_string).
        if not isinstance(edits_param, list) or not edits_param:
            return {
                "success": False,
                "output": (
                    "⚠️ 'edits' must be a non-empty array of "
                    "{\"old_string\": \"...\", \"new_string\": \"...\"} objects."
                ),
                "data": None,
                "artifacts": [],
            }
        edits = [{**e, "_mode": e.get("_mode", "string")} if isinstance(e, dict) else e
                 for e in edits_param]
    elif params.get("pattern") is not None:
        # Regex mode
        edits = [{
            "_mode": "regex",
            "pattern": params.get("pattern", ""),
            "replacement": params.get("replacement", ""),
            "occurrence": occurrence_param,
        }]
    elif params.get("start_line") is not None:
        # Line-span mode (requires expected_text guard)
        edits = [{
            "_mode": "line",
            "start_line": params.get("start_line"),
            "end_line": params.get("end_line", params.get("start_line")),
            "new_string": new_string_param if new_string_param is not None else "",
            "expected_text": params.get("expected_text"),
        }]
    elif params.get("after") is not None or params.get("before") is not None:
        # Anchor-insert mode
        edits = [{
            "_mode": "anchor",
            "after": params.get("after"),
            "before": params.get("before"),
            "new_string": new_string_param if new_string_param is not None else "",
            "occurrence": occurrence_param,
        }]
    elif old_string_param is not None:
        # Single string edit — normalize to batch of 1
        edits = [{
            "_mode": "string",
            "old_string": old_string_param,
            "new_string": new_string_param if new_string_param is not None else "",
            "occurrence": occurrence_param,
        }]
    else:
        return {
            "success": False,
            "output": (
                "⚠️ No edit specified. Provide one of: "
                "'old_string'+'new_string' (string edit), 'edits' (batch), "
                "'pattern'+'replacement' (regex), "
                "'start_line'+'end_line'+'expected_text' (line span), or "
                "'after'/'before'+'new_string' (anchor insert)."
            ),
            "data": None,
            "artifacts": [],
        }

    # --- Handle file not found ---
    if not path.exists():
        if create_if_missing:
            # Create file with new_string content from first edit
            first_new = _normalize_line_endings(edits[0].get("new_string", ""))
            if dry_run:
                return {
                    "success": True,
                    "output": (
                        f"✂️ [DRY RUN] Would create '{raw_path}' "
                        f"({len(first_new)} chars)"
                    ),
                    "data": {"path": raw_path, "would_create": True, "dry_run": True},
                    "artifacts": [],
                }
            await _atomic_write(path, first_new)
            await _track_file_edit(raw_path, len(first_new))
            logger.info("edit_file_created", path=raw_path, size=len(first_new))
            return {
                "success": True,
                "output": f"✂️ Created '{raw_path}' ({len(first_new)} chars)",
                "data": {"path": raw_path, "created": True, "new_size": len(first_new)},
                "artifacts": [{"type": "file", "path": raw_path, "size": len(first_new)}],
            }
        return {
            "success": False,
            "output": (
                f"File not found: '{raw_path}'. "
                "Use create_if_missing=true to create it, or use write_file."
            ),
            "data": None,
            "artifacts": [],
        }

    # --- Read file ---
    content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
    content = _normalize_line_endings(content)

    # --- Apply edits sequentially (all-or-nothing) ---
    working_content = content
    edit_summaries: list[str] = []
    total_replacements = 0

    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return {
                "success": False,
                "output": (
                    f"Edit #{idx + 1} is not a valid object. "
                    f"Expected {{\"old_string\": ..., \"new_string\": ...}}."
                ),
                "data": {"failed_edit_index": idx},
                "artifacts": [],
            }

        mode = edit.get("_mode", "string")
        occ = edit.get("occurrence", 1)

        # --- Dispatch by mode ---
        if mode == "regex":
            pattern = edit.get("pattern", "")
            replacement = _normalize_line_endings(edit.get("replacement", ""))
            if not pattern:
                return {
                    "success": False,
                    "output": f"Edit #{idx + 1}: 'pattern' cannot be empty in regex mode.",
                    "data": {"failed_edit_index": idx},
                    "artifacts": [],
                }
            summary = f"regex /{pattern[:40]}/ → \"{replacement[:40]}\""
            new_content, error, replacements = _apply_regex_edit(
                working_content, pattern, replacement, occ
            )
        elif mode == "line":
            start_line = edit.get("start_line")
            end_line = edit.get("end_line", start_line)
            new_str = _normalize_line_endings(edit.get("new_string", ""))
            expected = edit.get("expected_text")
            if not isinstance(start_line, int) or not isinstance(end_line, int):
                return {
                    "success": False,
                    "output": f"Edit #{idx + 1}: 'start_line'/'end_line' must be integers.",
                    "data": {"failed_edit_index": idx},
                    "artifacts": [],
                }
            action = "deleted" if new_str == "" else "replaced"
            summary = f"Lines {start_line}-{end_line} — {action}"
            new_content, error, replacements = _apply_line_edit(
                working_content, start_line, end_line, new_str, expected
            )
        elif mode == "anchor":
            after = edit.get("after")
            before = edit.get("before")
            new_str = _normalize_line_endings(edit.get("new_string", ""))
            if not new_str:
                return {
                    "success": False,
                    "output": f"Edit #{idx + 1}: 'new_string' cannot be empty in anchor mode.",
                    "data": {"failed_edit_index": idx},
                    "artifacts": [],
                }
            anchor_preview = (after if after is not None else before or "")[:40]
            rel = "after" if after is not None else "before"
            summary = f"insert {rel} \"{anchor_preview}\""
            new_content, error, replacements = _apply_anchor_edit(
                working_content, after, before, new_str, occ
            )
        else:
            # string mode (default)
            old_str = _normalize_line_endings(edit.get("old_string", ""))
            new_str = _normalize_line_endings(edit.get("new_string", ""))

            if not old_str:
                return {
                    "success": False,
                    "output": f"Edit #{idx + 1}: 'old_string' cannot be empty.",
                    "data": {"failed_edit_index": idx},
                    "artifacts": [],
                }

            if old_str == new_str:
                return {
                    "success": False,
                    "output": f"Edit #{idx + 1}: old_string and new_string are identical (no-op).",
                    "data": {"failed_edit_index": idx},
                    "artifacts": [],
                }

            # Build summary BEFORE applying (uses working_content to find position)
            summary = _build_edit_summary(
                working_content, old_str, new_str,
                edit_idx=(idx + 1) if len(edits) > 1 else None,
            )
            new_content, error, replacements = _apply_single_edit(
                working_content, old_str, new_str, occ
            )

        if error:
            rollback_note = (
                " No changes written — all edits rolled back."
                if len(edits) > 1 else ""
            )
            return {
                "success": False,
                "output": (
                    f"Edit #{idx + 1} failed: {error}{rollback_note}"
                ),
                "data": {"failed_edit_index": idx},
                "artifacts": [],
            }

        working_content = new_content
        total_replacements += replacements
        edit_summaries.append(summary)

    # --- Check if anything changed ---
    if working_content == content:
        return {
            "success": False,
            "output": "No changes resulted from the edits (content unchanged).",
            "data": None,
            "artifacts": [],
        }

    # --- Dry run: return preview without writing ---
    if dry_run:
        old_lines = content.splitlines()
        new_lines = working_content.splitlines()
        lines_changed = abs(len(new_lines) - len(old_lines))
        if lines_changed == 0:
            lines_changed = sum(1 for a, b in zip(old_lines, new_lines) if a != b)

        summary_text = "\n   ".join(edit_summaries)
        return {
            "success": True,
            "output": (
                f"✂️ [DRY RUN] Edit: {raw_path}\n"
                f"   Would apply {len(edits)} edit(s), ~{lines_changed} line(s) changed\n"
                f"   {summary_text}"
            ),
            "data": {
                "path": raw_path,
                "dry_run": True,
                "edits_count": len(edits),
                "lines_changed": lines_changed,
                "summaries": edit_summaries,
            },
            "artifacts": [],
        }

    # --- Write atomically ---
    await _atomic_write(path, working_content)

    # --- Compute stats ---
    old_lines = content.splitlines()
    new_lines = working_content.splitlines()
    lines_changed = abs(len(new_lines) - len(old_lines))
    if lines_changed == 0:
        lines_changed = sum(1 for a, b in zip(old_lines, new_lines) if a != b)

    artifact_path = raw_path
    try:
        artifact_path = str(path.relative_to(WORKSPACE_DIR))
    except ValueError:
        pass

    # Track file edit
    await _track_file_edit(raw_path, len(working_content))

    logger.info(
        "file_edited",
        path=raw_path,
        edits_count=len(edits),
        replacements=total_replacements,
        lines_changed=lines_changed,
    )

    # --- Build output ---
    summary_text = "\n   ".join(edit_summaries)
    return {
        "success": True,
        "output": (
            f"✂️ Edit: {raw_path}\n"
            f"   Applied {len(edits)} edit(s) successfully\n"
            f"   {summary_text}"
        ),
        "data": {
            "path": artifact_path,
            "edits_applied": len(edits),
            "replacements": total_replacements,
            "lines_changed": lines_changed,
            "new_size": len(working_content),
        },
        "artifacts": [{"type": "file", "path": artifact_path, "size": len(working_content)}],
    }


# ------------------------------------------------------------------
# Deprecated wrapper: apply_edits → delegates to handle_edit_file
# ------------------------------------------------------------------


async def handle_apply_edits_deprecated(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """[DEPRECATED] Thin wrapper — delegates to handle_edit_file."""
    logger.warning("deprecated_tool_called", tool="apply_edits", replacement="edit_file")
    # Params are already in the correct format (path + edits array)
    return await handle_edit_file(params, **kwargs)


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "filesystem"

registry.register(
    name="edit_file",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Make surgical edits to a file. The PRIMARY tool for changing existing "
            "files — always prefer this over rewriting a whole file with write_file.\n\n"
            "FIVE EDIT MODES (pick ONE per call; auto-detected from which params you send):\n"
            "1. STRING (default, use for most edits): old_string + new_string. Replaces an "
            "exact snippet. If the exact text isn't found, a whitespace-tolerant line match "
            "is tried automatically, so minor indentation differences won't break the edit.\n"
            "2. BATCH: edits[] — several string edits applied atomically (all succeed or none).\n"
            "3. REGEX: pattern + replacement — regex search/replace; use \\1 \\2 for capture "
            "groups. occurrence=0 replaces all, N targets the Nth match.\n"
            "4. LINE SPAN: start_line + end_line + expected_text — replace or delete whole lines "
            "N..M. expected_text MUST equal the current content of those lines (a guard against "
            "stale line numbers); new_string=\"\" deletes the span.\n"
            "5. ANCHOR INSERT: after (or before) + new_string — insert new_string right after/"
            "before a marker WITHOUT removing the marker. Great for adding imports/methods.\n\n"
            "SHARED OPTIONS: occurrence (0=all,1=first default,N=Nth), create_if_missing "
            "(create the file if absent), dry_run (preview without writing).\n\n"
            "WORKFLOW: read_file first to see exact content, then edit. On a 'No match' error "
            "the tool quotes the closest region — use it to fix old_string and retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "edits": {
                    "type": "array",
                    "description": (
                        "List of edits to apply. Each edit replaces old_string with new_string. "
                        "Applied sequentially (later edits see results of earlier ones)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {
                                "type": "string",
                                "description": "Exact text to find (must be unique unless occurrence is specified)",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Replacement text (empty string to delete)",
                            },
                            "occurrence": {
                                "type": "integer",
                                "description": "Which occurrence to replace: 0=all, 1=first (default), 2=second, etc.",
                                "default": 1,
                            },
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
                "old_string": {
                    "type": "string",
                    "description": "For single edit: exact text to find (must be unique unless occurrence is set)",
                },
                "new_string": {
                    "type": "string",
                    "description": "For single edit: replacement text (empty string to delete)",
                },
                "occurrence": {
                    "type": "integer",
                    "description": "Which occurrence to act on (string/regex/anchor modes): 0=all, 1=first (default), 2=second, etc.",
                    "default": 1,
                },
                "pattern": {
                    "type": "string",
                    "description": "REGEX mode: regular expression to search for (Python re syntax, MULTILINE enabled).",
                },
                "replacement": {
                    "type": "string",
                    "description": "REGEX mode: replacement text; supports \\1, \\2 backreferences to capture groups.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "LINE mode: first line of the span to replace/delete (1-based, inclusive).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "LINE mode: last line of the span (1-based, inclusive). Defaults to start_line.",
                },
                "expected_text": {
                    "type": "string",
                    "description": "LINE mode (REQUIRED): current content of lines start_line..end_line. Tool refuses the edit if it doesn't match, guarding against stale line numbers.",
                },
                "after": {
                    "type": "string",
                    "description": "ANCHOR mode: insert new_string immediately AFTER this marker text (marker is kept).",
                },
                "before": {
                    "type": "string",
                    "description": "ANCHOR mode: insert new_string immediately BEFORE this marker text (marker is kept).",
                },
                "create_if_missing": {
                    "type": "boolean",
                    "description": "Create file with new_string content if file doesn't exist",
                    "default": False,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview changes without applying (default: false)",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
    handler=handle_edit_file,
    is_async=True,
    emoji="✂️",
)

registry.register(
    name="apply_edits",
    toolset=_TOOLSET,
    schema={
        "description": (
            "[DEPRECATED - Use edit_file with 'edits' array instead] "
            "Apply multiple search/replace edits to a single file atomically. "
            "All edits must succeed or none are applied (transactional)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "edits": {
                    "type": "array",
                    "description": "List of search/replace operations to apply in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {
                                "type": "string",
                                "description": "Exact text to find (must be unique at time of application)",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Replacement text",
                            },
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
            },
            "required": ["path", "edits"],
        },
    },
    handler=handle_apply_edits_deprecated,
    is_async=True,
    emoji="🔀",
)
