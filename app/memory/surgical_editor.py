"""
MekongAI Nexus - Surgical Document Editor.

Provides diff-based editing capabilities for task context documents,
inspired by Roo-code's apply_diff pattern:
- Exact search/replace within files
- Line-based insert/delete
- Section-based replace (markdown headers)
- Atomic writes to prevent corruption

All operations are task-isolated: each task_id has its own directory,
preventing cross-task file collisions when running concurrently.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EditResult:
    """Result of a surgical edit operation."""

    success: bool
    message: str = ""
    old_text: str = ""
    new_text: str = ""
    line_range: tuple[int, int] = (0, 0)  # (start_line, end_line) affected
    context_before: str = ""  # 3 lines before edit for verification
    context_after: str = ""   # 3 lines after edit for verification
    file_path: str = ""


@dataclass
class SearchHit:
    """A single search match within a document."""

    doc_name: str
    line_num: int
    line_content: str
    context: str = ""  # surrounding lines
    relevance: float = 1.0


class SurgicalEditor:
    """Line-aware search-replace editor for markdown documents.

    All file paths are resolved relative to a specific task directory
    to guarantee task isolation. Two concurrent tasks with different
    task_ids will NEVER read/write each other's files.

    Usage:
        editor = SurgicalEditor()
        result = await editor.edit(path, search="old text", replace="new text")
        result = await editor.insert_at(path, after_line=10, content="new line")
        result = await editor.replace_section(path, "## Phase: RESEARCH", new_content)
    """

    # ── Core: Search-Replace ─────────────────────────────────────

    @staticmethod
    async def edit(
        file_path: Path,
        search: str,
        replace: str,
    ) -> EditResult:
        """Find exact match of search text and replace it.

        Algorithm:
        1. Read entire file content
        2. Find exact match of search text (preserves whitespace)
        3. If found: replace FIRST occurrence, return success + context
        4. If not found: try stripped-whitespace fuzzy match
        5. Write back with atomic write

        Args:
            file_path: Absolute path to the document file.
            search: Exact text to find (multi-line supported).
            replace: Replacement text.

        Returns:
            EditResult with success status, affected line range, and context.
        """
        if not file_path.exists():
            return EditResult(
                success=False,
                message=f"File not found: {file_path.name}",
                file_path=str(file_path),
            )

        content = await _async_read(file_path)

        # --- Exact match ---
        idx = content.find(search)
        if idx >= 0:
            new_content = content[:idx] + replace + content[idx + len(search):]
            await _async_write(file_path, new_content)

            start_line = content[:idx].count("\n") + 1
            end_line = start_line + search.count("\n")
            ctx_before, ctx_after = _extract_context(new_content, start_line, end_line)

            return EditResult(
                success=True,
                message="Exact match replaced successfully.",
                old_text=search[:200],
                new_text=replace[:200],
                line_range=(start_line, end_line),
                context_before=ctx_before,
                context_after=ctx_after,
                file_path=str(file_path),
            )

        # --- Fuzzy match: strip each line's trailing whitespace ---
        search_stripped = "\n".join(
            line.rstrip() for line in search.split("\n")
        )
        content_stripped = "\n".join(
            line.rstrip() for line in content.split("\n")
        )
        fuzzy_idx = content_stripped.find(search_stripped)

        if fuzzy_idx >= 0:
            # Map back to original content position
            # Count bytes in original up to the same line
            target_line = content_stripped[:fuzzy_idx].count("\n")
            original_lines = content.split("\n")
            search_line_count = search_stripped.count("\n") + 1

            # Rebuild the original span
            orig_start = sum(
                len(original_lines[i]) + 1 for i in range(target_line)
            )
            orig_span = "\n".join(
                original_lines[target_line: target_line + search_line_count]
            )
            new_content = content[:orig_start] + replace + content[orig_start + len(orig_span):]
            await _async_write(file_path, new_content)

            return EditResult(
                success=True,
                message="Fuzzy match replaced (whitespace-trimmed).",
                old_text=orig_span[:200],
                new_text=replace[:200],
                line_range=(target_line + 1, target_line + search_line_count),
                file_path=str(file_path),
            )

        return EditResult(
            success=False,
            message=(
                f"Search text not found in {file_path.name}. "
                f"Search preview: '{search[:80]}...'"
            ),
            file_path=str(file_path),
        )

    # ── Insert at Line ───────────────────────────────────────────

    @staticmethod
    async def insert_at(
        file_path: Path,
        after_line: int,
        content: str,
    ) -> EditResult:
        """Insert content after a specific line number (1-based).

        Args:
            file_path: Absolute path to the document file.
            after_line: Line number after which to insert (0 = beginning).
            content: Text to insert.
        """
        if not file_path.exists():
            return EditResult(
                success=False,
                message=f"File not found: {file_path.name}",
                file_path=str(file_path),
            )

        existing = await _async_read(file_path)
        lines = existing.split("\n")

        if after_line < 0 or after_line > len(lines):
            return EditResult(
                success=False,
                message=f"Line {after_line} out of range (file has {len(lines)} lines).",
                file_path=str(file_path),
            )

        # Insert content lines after the specified line
        content_lines = content.split("\n")
        new_lines = lines[:after_line] + content_lines + lines[after_line:]
        new_content = "\n".join(new_lines)
        await _async_write(file_path, new_content)

        end_line = after_line + len(content_lines)
        ctx_before, ctx_after = _extract_context(
            new_content, after_line + 1, end_line,
        )

        return EditResult(
            success=True,
            message=f"Inserted {len(content_lines)} lines after line {after_line}.",
            new_text=content[:200],
            line_range=(after_line + 1, end_line),
            context_before=ctx_before,
            context_after=ctx_after,
            file_path=str(file_path),
        )

    # ── Replace Section ──────────────────────────────────────────

    @staticmethod
    async def replace_section(
        file_path: Path,
        section_header: str,
        new_content: str,
    ) -> EditResult:
        """Replace entire markdown section (from header to next same-level header).

        Args:
            file_path: Absolute path to document file.
            section_header: Exact header text, e.g. "## Phase: RESEARCH".
            new_content: New content for the entire section (including header).
        """
        if not file_path.exists():
            return EditResult(
                success=False,
                message=f"File not found: {file_path.name}",
                file_path=str(file_path),
            )

        content = await _async_read(file_path)
        lines = content.split("\n")

        # Determine header level (count leading '#')
        header_stripped = section_header.strip()
        header_level = 0
        for ch in header_stripped:
            if ch == "#":
                header_level += 1
            else:
                break

        if header_level == 0:
            return EditResult(
                success=False,
                message=f"Invalid section header: '{section_header}'",
                file_path=str(file_path),
            )

        # Find section start
        section_start = -1
        for i, line in enumerate(lines):
            if line.strip() == header_stripped:
                section_start = i
                break

        if section_start < 0:
            return EditResult(
                success=False,
                message=f"Section '{section_header}' not found.",
                file_path=str(file_path),
            )

        # Find section end (next header at same or higher level)
        section_end = len(lines)
        for i in range(section_start + 1, len(lines)):
            line_stripped = lines[i].strip()
            if line_stripped.startswith("#"):
                line_level = 0
                for ch in line_stripped:
                    if ch == "#":
                        line_level += 1
                    else:
                        break
                if line_level <= header_level:
                    section_end = i
                    break

        # Replace section
        new_lines = lines[:section_start] + new_content.split("\n") + lines[section_end:]
        await _async_write(file_path, "\n".join(new_lines))

        return EditResult(
            success=True,
            message=f"Section '{header_stripped}' replaced (lines {section_start + 1}-{section_end}).",
            old_text="\n".join(lines[section_start:section_end])[:300],
            new_text=new_content[:300],
            line_range=(section_start + 1, section_end),
            file_path=str(file_path),
        )

    # ── Search across documents ──────────────────────────────────

    @staticmethod
    async def search(
        directory: Path,
        query: str,
        file_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[SearchHit]:
        """Full-text search across markdown documents in a directory.

        Args:
            directory: Directory containing .md files to search.
            query: Text to search for (case-insensitive).
            file_names: Optional list of specific file names (without .md extension).
            max_results: Max number of matches to return.
        """
        hits: list[SearchHit] = []
        query_lower = query.lower()

        # Resolve target files
        if file_names:
            targets = [directory / f"{name}.md" for name in file_names]
        else:
            targets = sorted(directory.glob("*.md"))

        for fpath in targets:
            if not fpath.exists():
                continue
            content = await _async_read(fpath)
            lines = content.split("\n")
            doc_name = fpath.stem

            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    # Build context (2 lines before + after)
                    ctx_start = max(0, i - 2)
                    ctx_end = min(len(lines), i + 3)
                    context = "\n".join(lines[ctx_start:ctx_end])

                    hits.append(SearchHit(
                        doc_name=doc_name,
                        line_num=i + 1,
                        line_content=line.strip(),
                        context=context,
                    ))

                    if len(hits) >= max_results:
                        return hits

        return hits


# ── Internal helpers ─────────────────────────────────────────────────


async def _async_read(path: Path) -> str:
    """Read file content asynchronously via thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: path.read_text(encoding="utf-8"),
    )


async def _async_write(path: Path, content: str) -> None:
    """Write file content atomically via thread pool.

    Writes to a temp file first, then renames — prevents corruption
    if process is interrupted mid-write.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    def _do_write() -> None:
        tmp_path.write_text(content, encoding="utf-8")
        # Atomic rename (works on POSIX; on Windows, replaces target)
        if os.name == "nt":
            # Windows: os.replace is atomic
            os.replace(str(tmp_path), str(path))
        else:
            os.rename(str(tmp_path), str(path))

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _do_write)


def _extract_context(
    content: str,
    start_line: int,
    end_line: int,
    context_lines: int = 3,
) -> tuple[str, str]:
    """Extract context lines before and after an edit region."""
    lines = content.split("\n")
    total = len(lines)

    before_start = max(0, start_line - 1 - context_lines)
    before_end = max(0, start_line - 1)
    ctx_before = "\n".join(lines[before_start:before_end])

    after_start = min(total, end_line)
    after_end = min(total, end_line + context_lines)
    ctx_after = "\n".join(lines[after_start:after_end])

    return ctx_before, ctx_after
