"""
MekongAI Nexus - Search & Patch Tools.

Provides search_files (grep-like) and patch (apply unified diff) tools.
These are essential for agents that need to find and modify code.

Inspired by: hermes-agent file_tools (search_files, patch).

Usage::

    # Auto-registered tools:
    #   search_files — search for text/regex patterns in workspace files
    #   patch_file   — apply a patch/edit to a file (search & replace)
    #   diff_files   — compare two files and show differences
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
WORKSPACE_DIR.mkdir(exist_ok=True)

# Max files to search, max matches to return
_MAX_FILES = 500
_MAX_MATCHES = 50
_MAX_FILE_SIZE = 1_000_000  # 1MB
_BINARY_CHECK_SIZE = 8192

# Directories automatically excluded from search (VCS/build artifacts)
DEFAULT_EXCLUDED_DIRS: set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".tox",
    ".eggs", "*.egg-info", ".next", ".nuxt",
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _is_binary_file(filepath: Path) -> bool:
    """Check if a file is binary by scanning first bytes for null characters."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(_BINARY_CHECK_SIZE)
        return b"\x00" in chunk
    except OSError:
        return True


def _should_exclude_dir(dirname: str, include_hidden: bool) -> bool:
    """Check if a directory should be excluded from search."""
    if not include_hidden and dirname.startswith("."):
        # Still allow traversal into search_dir itself, but skip hidden subdirs
        return True
    for pattern in DEFAULT_EXCLUDED_DIRS:
        if fnmatch.fnmatch(dirname, pattern):
            return True
    return False


def _matches_exclude_patterns(
    rel_path: str, exclude_patterns: list[str]
) -> bool:
    """Check if a relative path matches any user-supplied exclude glob."""
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Also match just the filename portion
        if fnmatch.fnmatch(os.path.basename(rel_path), pattern):
            return True
    return False


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_search_files(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Search for text or regex patterns across workspace files.

    Like grep -rn but with glob filtering, context lines, multiple output
    modes, pagination, and multiline support.
    """
    query: str = params.get("query", "")
    path: str = params.get("path", ".")
    file_pattern: str = params.get("file_pattern", "*")
    is_regex: bool = params.get("regex", False)
    context_lines: int = params.get("context_lines", 0)
    max_results: int = params.get("max_results", _MAX_MATCHES)
    output_mode: str = params.get("output_mode", "content")
    case_sensitive: bool = params.get("case_sensitive", False)
    multiline: bool = params.get("multiline", False)
    offset: int = params.get("offset", 0)
    exclude_patterns: list[str] = params.get("exclude_patterns", [])
    include_hidden: bool = params.get("include_hidden", False)

    if not query:
        return {"success": False, "output": "No search query provided", "data": None, "artifacts": []}

    if output_mode not in ("content", "files_only", "count"):
        return {"success": False, "output": f"Invalid output_mode: {output_mode}", "data": None, "artifacts": []}

    # Security check
    path_verdict = check_path_safety(path, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    # Resolve through the shared resolver so every path shape an agent may pass
    # (relative, ``workspace_data/...`` prefix, ``tasks/...`` prefix, absolute)
    # lands the SAME place read_file/write_file/list_files do — and is scoped to
    # THIS task's _work/ by default. Hand-rolled ``WORKSPACE_DIR / path`` here
    # was the cause of false "Directory not found" in task 3c44a93f bug #3.
    from app.mcp.tools.filesystem import _smart_resolve_path, get_task_workspace
    ws = get_task_workspace()
    search_dir = _smart_resolve_path(path, ws, WORKSPACE_DIR)
    if not search_dir.exists():
        return {"success": False, "output": f"Directory not found: {path}", "data": None, "artifacts": []}

    # Compile pattern
    flags = 0
    if not case_sensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.DOTALL | re.MULTILINE

    try:
        if is_regex:
            pattern = re.compile(query, flags)
        else:
            pattern = re.compile(re.escape(query), flags)
    except re.error as e:
        return {"success": False, "output": f"Invalid regex: {e}", "data": None, "artifacts": []}

    # Walk and search
    all_matches: list[dict[str, Any]] = []
    files_searched = 0
    files_with_matches: dict[str, int] = {}  # rel_path -> match count
    total_match_count = 0

    for root, dirs, files in os.walk(search_dir):
        # Filter directories in-place to prune traversal
        dirs[:] = [
            d for d in dirs
            if not _should_exclude_dir(d, include_hidden)
        ]

        for filename in files:
            if files_searched >= _MAX_FILES:
                break

            # Skip hidden files unless requested
            if not include_hidden and filename.startswith("."):
                continue

            if not fnmatch.fnmatch(filename, file_pattern):
                continue

            filepath = Path(root) / filename
            rel_path = str(filepath.relative_to(WORKSPACE_DIR))

            # User exclude patterns
            if exclude_patterns and _matches_exclude_patterns(rel_path, exclude_patterns):
                continue

            # Skip too-large files
            try:
                file_size = filepath.stat().st_size
            except OSError:
                continue
            if file_size > _MAX_FILE_SIZE:
                continue

            # Skip binary files
            if _is_binary_file(filepath):
                continue

            files_searched += 1
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            if multiline:
                # Multiline: match across line boundaries
                file_matches = list(pattern.finditer(content))
                if not file_matches:
                    continue
                file_match_count = len(file_matches)
                total_match_count += file_match_count
                files_with_matches[rel_path] = file_match_count

                lines = content.split("\n")
                for m in file_matches:
                    start_line = content[:m.start()].count("\n") + 1
                    end_line = content[:m.end()].count("\n") + 1
                    matched_text = m.group()
                    # Truncate very long matches
                    if len(matched_text) > 1000:
                        matched_text = matched_text[:1000] + "..."

                    match_entry: dict[str, Any] = {
                        "file": rel_path,
                        "line_number": start_line,
                        "end_line_number": end_line,
                        "line": matched_text.rstrip()[:500],
                    }
                    if context_lines > 0:
                        ctx_start = max(0, start_line - 1 - context_lines)
                        ctx_end = min(len(lines), end_line + context_lines)
                        match_entry["context"] = [
                            f"{j + 1}: {lines[j].rstrip()[:500]}"
                            for j in range(ctx_start, ctx_end)
                        ]
                    all_matches.append(match_entry)
            else:
                # Line-by-line search
                lines = content.split("\n")
                file_match_count = 0

                for i, line in enumerate(lines):
                    if pattern.search(line):
                        file_match_count += 1
                        total_match_count += 1
                        match_entry = {
                            "file": rel_path,
                            "line_number": i + 1,
                            "line": line.rstrip()[:500],
                        }
                        if context_lines > 0:
                            start = max(0, i - context_lines)
                            end = min(len(lines), i + context_lines + 1)
                            match_entry["context"] = [
                                f"{j + 1}: {lines[j].rstrip()[:500]}"
                                for j in range(start, end)
                            ]
                        all_matches.append(match_entry)

                if file_match_count > 0:
                    files_with_matches[rel_path] = file_match_count

        if files_searched >= _MAX_FILES:
            break

    # Apply pagination (offset + max_results)
    total_before_pagination = len(all_matches)
    paginated_matches = all_matches[offset:offset + max_results]

    # Summary header
    num_files_matched = len(files_with_matches)
    header = f'Search: "{query}" ({total_match_count} matches in {num_files_matched} files)'

    # Pagination hint
    pagination_hint = ""
    if offset + max_results < total_before_pagination:
        next_offset = offset + max_results
        pagination_hint = (
            f"\n(Showing results {offset + 1}-{offset + len(paginated_matches)} "
            f"of {total_before_pagination}. Use offset={next_offset} to see more)"
        )
    elif offset > 0:
        pagination_hint = (
            f"\n(Showing results {offset + 1}-{offset + len(paginated_matches)} "
            f"of {total_before_pagination})"
        )

    # Format output based on mode
    if output_mode == "count":
        output = header + pagination_hint

    elif output_mode == "files_only":
        output_lines = [header, ""]
        # Sort by match count descending
        for fpath, count in sorted(
            files_with_matches.items(), key=lambda x: x[1], reverse=True
        ):
            suffix = "match" if count == 1 else "matches"
            output_lines.append(f"{fpath} ({count} {suffix})")
        output = "\n".join(output_lines) + pagination_hint

    else:  # output_mode == "content"
        output_lines = [header, ""]
        for m in paginated_matches:
            if "context" in m:
                output_lines.append(f"{m['file']}:{m['line_number']}")
                for ctx in m["context"]:
                    output_lines.append(f"  {ctx}")
            else:
                output_lines.append(f"{m['file']}:{m['line_number']}:{m['line']}")
        output = "\n".join(output_lines) + pagination_hint

    return {
        "success": True,
        "output": output,
        "data": {
            "matches": paginated_matches,
            "total_matches": total_match_count,
            "total_before_pagination": total_before_pagination,
            "files_searched": files_searched,
            "files_matched": num_files_matched,
            "offset": offset,
            "has_more": (offset + max_results) < total_before_pagination,
        },
        "artifacts": [],
    }


async def handle_patch_file(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Apply a search-and-replace patch to a file.

    Find `search` text in the file and replace with `replace` text.
    This is safer than rewriting the whole file and works like
    hermes-agent's patch tool.
    """
    logger.warning("deprecated_tool_called", tool="patch_file", replacement="edit_file")
    file_path = params.get("path", "")
    search_text = params.get("search", "")
    replace_text = params.get("replace", "")

    if not file_path:
        return {"success": False, "output": "No file path provided", "data": None, "artifacts": []}
    if not search_text:
        return {"success": False, "output": "No search text provided", "data": None, "artifacts": []}

    # Security check
    path_verdict = check_path_safety(file_path, str(WORKSPACE_DIR))
    if not path_verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {path_verdict.reason}", "data": None, "artifacts": []}

    target = WORKSPACE_DIR / file_path
    if not target.exists():
        return {"success": False, "output": f"File not found: {file_path}", "data": None, "artifacts": []}
    if not str(target.resolve()).startswith(str(WORKSPACE_DIR.resolve())):
        return {"success": False, "output": "Access denied: outside workspace", "data": None, "artifacts": []}

    try:
        content = target.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "output": f"Cannot read file: {e}", "data": None, "artifacts": []}

    # Find and replace
    count = content.count(search_text)
    if count == 0:
        return {
            "success": False,
            "output": f"Search text not found in {file_path}.\nSearched for:\n{search_text[:500]}",
            "data": {"file": file_path, "matches": 0},
            "artifacts": [],
        }

    new_content = content.replace(search_text, replace_text)
    target.write_text(new_content, encoding="utf-8")

    return {
        "success": True,
        "output": f"Patched {file_path}: {count} replacement(s) made",
        "data": {
            "file": file_path,
            "replacements": count,
            "search_length": len(search_text),
            "replace_length": len(replace_text),
        },
        "artifacts": [{"type": "file", "path": file_path}],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "filesystem"

registry.register(
    name="search_files",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Search for text or regex patterns across files in the workspace. "
            "Like grep -rn with output modes, pagination, multiline, and "
            "case-sensitivity options."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (relative to workspace, default: '.')",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g., '*.py', '*.js'). Default: '*'",
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat query as regex (default: false, plain text search)",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Number of context lines around each match (default: 0)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to return (default: 50)",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_only", "count"],
                    "description": (
                        "Output format: 'content' (default) shows matched lines with context, "
                        "'files_only' shows only file paths containing matches, "
                        "'count' shows match count per file"
                    ),
                    "default": "content",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive search (default: false)",
                    "default": False,
                },
                "multiline": {
                    "type": "boolean",
                    "description": "Enable multiline regex matching with re.DOTALL (default: false)",
                    "default": False,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip first N results for pagination (default: 0)",
                    "default": 0,
                },
                "exclude_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glob patterns to exclude (e.g., ['*.min.js', 'dist/*'])",
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files/directories (default: false)",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_search_files,
    is_async=True,
    emoji="🔎",
)

registry.register(
    name="patch_file",
    toolset=_TOOLSET,
    schema={
        "description": (
            "[DEPRECATED - Use edit_file instead] "
            "Apply a search-and-replace edit to a file. Find exact text "
            "in the file and replace it with new text. Safer than rewriting "
            "the entire file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "search": {
                    "type": "string",
                    "description": "Exact text to find in the file",
                },
                "replace": {
                    "type": "string",
                    "description": "Text to replace the search text with",
                },
            },
            "required": ["path", "search", "replace"],
        },
    },
    handler=handle_patch_file,
    is_async=True,
    emoji="🩹",
)


# ------------------------------------------------------------------
# Diff handler
# ------------------------------------------------------------------


async def handle_diff_files(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Compare two files and show unified diff."""
    file_a = params.get("file_a", "")
    file_b = params.get("file_b", "")
    context_lines = params.get("context_lines", 3)

    if not file_a or not file_b:
        return {"success": False, "output": "Both file_a and file_b are required", "data": None, "artifacts": []}

    for name, path_val in [("file_a", file_a), ("file_b", file_b)]:
        verdict = check_path_safety(path_val, str(WORKSPACE_DIR))
        if not verdict.safe:
            return {"success": False, "output": f"⚠️ {name} path blocked: {verdict.reason}", "data": None, "artifacts": []}

    path_a = WORKSPACE_DIR / file_a
    path_b = WORKSPACE_DIR / file_b

    if not path_a.exists():
        return {"success": False, "output": f"File not found: {file_a}", "data": None, "artifacts": []}
    if not path_b.exists():
        return {"success": False, "output": f"File not found: {file_b}", "data": None, "artifacts": []}

    try:
        lines_a = path_a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = path_b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        return {"success": False, "output": f"Cannot read files: {e}", "data": None, "artifacts": []}

    diff_lines = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=file_a, tofile=file_b,
        n=context_lines,
    ))

    if not diff_lines:
        return {
            "success": True,
            "output": f"✅ Files are identical: {file_a} == {file_b}",
            "data": {"identical": True, "file_a": file_a, "file_b": file_b},
            "artifacts": [],
        }

    diff_text = "".join(diff_lines)
    # Truncate if too long
    if len(diff_text) > 15_000:
        diff_text = diff_text[:15_000] + "\n... (diff truncated)"

    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    return {
        "success": True,
        "output": f"📝 Diff: {file_a} vs {file_b} (+{added}/-{removed} lines)\n\n```diff\n{diff_text}\n```",
        "data": {
            "identical": False,
            "file_a": file_a,
            "file_b": file_b,
            "added_lines": added,
            "removed_lines": removed,
        },
        "artifacts": [],
    }


registry.register(
    name="diff_files",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Compare two files and show their differences in unified diff format. "
            "Useful for code review and change tracking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_a": {"type": "string", "description": "First file path (relative to workspace)"},
                "file_b": {"type": "string", "description": "Second file path (relative to workspace)"},
                "context_lines": {"type": "integer", "description": "Lines of context around changes (default: 3)"},
            },
            "required": ["file_a", "file_b"],
        },
    },
    handler=handle_diff_files,
    is_async=True,
    emoji="📝",
)
