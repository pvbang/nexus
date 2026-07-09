"""
MekongAI Nexus - Task Working Memory (TWMS Core).

Per-task persistent working memory using structured document files.
Inspired by Roo-code pattern: analyze-while-writing to files,
surgical editing, agents read back anytime.

**Task Isolation Guarantee:**
Every task_id gets its own directory:
    workspace_data/tasks/{task_id_short}/_context/
Two concurrent tasks will NEVER collide — all paths are resolved
from the task_id, and asyncio.Lock guards concurrent writes
within the same task.

Document structure per task:
    _context/
    ├── TASK_BRIEF.md      — Goals, constraints, success criteria (pipeline-managed)
    ├── ROE.md             — Rules of Engagement (pipeline-managed)
    ├── TODO.md            — Task decomposition checklist (pipeline-managed)
    ├── ACTIVITY.md        — Turn-by-turn activity log (pipeline-managed)
    ├── FINAL_REPORT.md    — Comprehensive final output (pipeline-managed)
    ├── FINDINGS.md        — Accumulated discoveries (agent-writable)
    ├── DECISIONS.md       — Key decisions with reasoning (agent-writable)
    ├── SCRATCHPAD.md      — Free-form intermediate notes (agent-writable)
    └── [custom].md        — Agents can create additional docs freely
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import structlog

from app.memory.surgical_editor import EditResult, SearchHit, SurgicalEditor

logger = structlog.get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# Base workspace dir (same as filesystem tools)
_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# Well-known context document names (without .md extension)
DOC_TASK_BRIEF = "TASK_BRIEF"
DOC_FINDINGS = "FINDINGS"
DOC_DECISIONS = "DECISIONS"
DOC_SCRATCHPAD = "SCRATCHPAD"

# Pipeline-managed docs (read-only for agents — managed by NexusOrchestrator/ActivityLogger)
PIPELINE_DOCS = ["TASK_BRIEF", "ROE", "TODO", "ACTIVITY", "FINAL_REPORT"]

# Agent-writable docs (agents can create/edit these freely via task_doc tools)
AGENT_DOCS = ["FINDINGS", "DECISIONS", "SCRATCHPAD"]

# Legacy doc (deprecated, restricted)
DOC_PROGRESS = "PROGRESS"  # Keep for backward compat but restricted

ALL_DOC_NAMES = PIPELINE_DOCS + AGENT_DOCS

# Size limits per document (bytes) — generous to allow comprehensive reports
# Set very high to allow detailed content (debate transcripts, evidence catalogs)
MAX_DOC_SIZE = 2_000_000  # 2 MB — effectively unlimited for text docs

# Context building defaults
DEFAULT_CONTEXT_MAX_CHARS = 4000


# ── Registry of per-task locks (prevent concurrent writes within same task) ──
_task_locks: dict[str, asyncio.Lock] = {}
_MAX_TASK_LOCKS = 200  # LRU eviction threshold to prevent memory leak


def _get_task_lock(task_id: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for a task_id.

    Includes LRU eviction: when the lock registry exceeds _MAX_TASK_LOCKS,
    the oldest entries (which are likely completed tasks) are evicted.
    This prevents unbounded memory growth even if cleanup() is never called.
    """
    if task_id not in _task_locks:
        # LRU eviction: remove oldest entries when registry is full
        if len(_task_locks) >= _MAX_TASK_LOCKS:
            # Evict oldest 20% (FIFO — oldest entries are completed tasks)
            evict_count = _MAX_TASK_LOCKS // 5
            keys_to_remove = list(_task_locks.keys())[:evict_count]
            for key in keys_to_remove:
                lock = _task_locks[key]
                # Only evict if lock is NOT currently held AND has no pending waiters
                if not lock.locked() and not getattr(lock, '_waiters', []):
                    del _task_locks[key]
        _task_locks[task_id] = asyncio.Lock()
    return _task_locks[task_id]


def _cleanup_task_lock(task_id: str) -> None:
    """Remove lock when task is done (prevents memory leak)."""
    _task_locks.pop(task_id, None)


class TaskWorkingMemory:
    """Per-task persistent working memory manager.

    Each task_id is mapped to a unique directory:
        workspace_data/tasks/{task_id_short}/_context/

    This guarantees:
    - **Isolation**: task A cannot read/write task B's context
    - **Persistence**: context survives LLM context window overflow
    - **Concurrency**: asyncio.Lock per task prevents write corruption
    - **Recovery**: agents can reload context from files at any time

    Usage::

        twm = TaskWorkingMemory("a1b2c3d4-full-uuid")
        await twm.init_task_brief("Analyze GDP data", goals=["Compare Q1-Q4"])
        await twm.add_finding("Researcher", "web_search", "GDP grew 8.1%", 0.8)
        context = await twm.build_agent_context("Analyst", "analysis")
    """

    def __init__(self, task_id: str) -> None:
        if not task_id:
            raise ValueError("task_id is required for TaskWorkingMemory")

        from app.core import task_paths
        self.task_id = task_id
        # Use first 8 chars for directory name (consistent with get_task_workspace)
        self.task_id_short = task_id[:8]

        # Resolve isolated directory via central resolver (nests subtasks).
        self.task_dir = task_paths.task_dir(task_id)
        self.context_dir = task_paths.context_dir(task_id)

        self._lock = _get_task_lock(task_id)
        self._editor = SurgicalEditor()

    # ── Path Resolution (always task-isolated) ───────────────────

    def _doc_path(self, doc_name: str) -> Path:
        """Resolve absolute path for a context document.

        Always returns a path inside this task's _context/ directory.
        Sanitizes doc_name to prevent path traversal attacks.
        Preserves case — predefined docs are UPPERCASE, custom docs keep their case.
        """
        # Sanitize: strip slashes, dots, keep only alphanumeric + underscore
        safe_name = "".join(
            c for c in doc_name if c.isalnum() or c == "_"
        )
        if not safe_name:
            safe_name = "SCRATCHPAD"
        return self.context_dir / f"{safe_name}.md"

    def _all_doc_names(self) -> list[str]:
        """Scan _context/ directory and return all .md doc names (without extension).

        Returns both predefined and custom documents.
        """
        if not self.context_dir.exists():
            return []
        return [
            p.stem for p in sorted(self.context_dir.glob("*.md"))
        ]

    # ── Core Document Operations ─────────────────────────────────

    async def read_doc(
        self,
        doc_name: str,
        section: str | None = None,
    ) -> str:
        """Read a context document or a specific section.

        Args:
            doc_name: Document name (TASK_BRIEF, PROGRESS, FINDINGS, etc.)
            section: Optional markdown section header to extract (e.g. "## Phase: RESEARCH")

        Returns:
            Document content or section content. Empty string if not found.
        """
        path = self._doc_path(doc_name)
        if not path.exists():
            return ""

        from app.memory.surgical_editor import _async_read

        # ACTIVITY special case: include rotated epoch files for full history
        if doc_name.upper() == "ACTIVITY":
            content = ""
            epoch_files = sorted(self.context_dir.glob("ACTIVITY_epoch*.md"))
            for ef in epoch_files:
                content += await _async_read(ef)
            content += await _async_read(path)
        else:
            content = await _async_read(path)

        if section:
            return self._extract_section(content, section)

        return content

    async def read_doc_chunked(
        self,
        doc_name: str,
        offset: int = 0,
        limit: int = 16000,
    ) -> tuple[str, int, bool]:
        """Read document in chunks to avoid context overflow.

        Args:
            doc_name: Document name.
            offset: Character offset to start reading from.
            limit: Maximum characters to return per chunk (default 16000 ~ 4000 tokens).

        Returns:
            Tuple of (content_chunk, total_chars, has_more).
        """
        path = self._doc_path(doc_name)
        if not path.exists():
            return ("", 0, False)

        from app.memory.surgical_editor import _async_read

        # ACTIVITY special case: include rotated epoch files
        if doc_name.upper() == "ACTIVITY":
            content = ""
            epoch_files = sorted(self.context_dir.glob("ACTIVITY_epoch*.md"))
            for ef in epoch_files:
                content += await _async_read(ef)
            content += await _async_read(path)
        else:
            content = await _async_read(path)

        total = len(content)

        if offset >= total:
            return ("", total, False)

        chunk = content[offset:offset + limit]
        has_more = (offset + limit) < total
        return (chunk, total, has_more)

    async def read_doc_toc(self, doc_name: str) -> list[dict[str, Any]]:
        """Read table of contents (markdown headers only) — lightweight.

        Returns list of dicts with: level, title, char_offset, line_num.
        Useful for navigating large documents without loading full content.
        """
        path = self._doc_path(doc_name)
        if not path.exists():
            return []

        from app.memory.surgical_editor import _async_read
        content = await _async_read(path)

        import re
        headers: list[dict[str, Any]] = []
        char_pos = 0
        for line_num, line in enumerate(content.split("\n"), 1):
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if match:
                headers.append({
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                    "char_offset": char_pos,
                    "line_num": line_num,
                })
            char_pos += len(line) + 1  # +1 for newline

        return headers

    async def get_doc_size(self, doc_name: str) -> int:
        """Get document size in characters without reading full content."""
        path = self._doc_path(doc_name)
        if not path.exists():
            return 0
        return path.stat().st_size

    async def write_doc(
        self,
        doc_name: str,
        content: str,
        mode: str = "append",
    ) -> bool:
        """Write content to a context document.

        Args:
            doc_name: Document name.
            content: Content to write.
            mode: "append" (add to end), "replace" (overwrite), or "prepend" (add to start).

        Returns:
            True if written successfully.
        """
        path = self._doc_path(doc_name)

        async with self._lock:
            from app.memory.surgical_editor import _async_read, _async_write

            if mode == "replace":
                new_content = content
            elif mode == "prepend":
                existing = await _async_read(path) if path.exists() else ""
                new_content = content + "\n" + existing
            else:  # append
                existing = await _async_read(path) if path.exists() else ""
                new_content = existing.rstrip() + "\n\n" + content if existing.strip() else content

            # Enforce size limit
            if len(new_content.encode("utf-8")) > MAX_DOC_SIZE:
                logger.warning(
                    "twm_doc_size_limit",
                    task_id=self.task_id_short,
                    doc=doc_name,
                    size=len(new_content),
                    max=MAX_DOC_SIZE,
                )
                # Truncate from the middle, keep head + tail
                half = MAX_DOC_SIZE // 2
                new_content = (
                    new_content[:half]
                    + "\n\n... [TRUNCATED — document exceeded size limit] ...\n\n"
                    + new_content[-half:]
                )

            await _async_write(path, new_content)

        logger.debug(
            "twm_doc_written",
            task_id=self.task_id_short,
            doc=doc_name,
            mode=mode,
            size=len(new_content),
        )
        return True

    async def edit_doc(
        self,
        doc_name: str,
        search: str,
        replace: str,
    ) -> EditResult:
        """Surgical search-replace edit on a context document.

        Finds exact match of search text and replaces with replace text.
        Falls back to whitespace-fuzzy matching if exact match fails.

        Args:
            doc_name: Document name.
            search: Text to find (multi-line OK).
            replace: Replacement text.

        Returns:
            EditResult with success status and context.
        """
        path = self._doc_path(doc_name)
        async with self._lock:
            return await self._editor.edit(path, search, replace)

    async def insert_at_line(
        self,
        doc_name: str,
        after_line: int,
        content: str,
    ) -> EditResult:
        """Insert content after a specific line number (1-based).

        Args:
            doc_name: Document name.
            after_line: Line number after which to insert (0 = beginning).
            content: Text to insert.
        """
        path = self._doc_path(doc_name)
        async with self._lock:
            return await self._editor.insert_at(path, after_line, content)

    async def replace_section(
        self,
        doc_name: str,
        section_header: str,
        new_content: str,
    ) -> EditResult:
        """Replace an entire markdown section.

        Args:
            doc_name: Document name.
            section_header: Exact header text (e.g. "## Phase: RESEARCH").
            new_content: New section content (including the header line).
        """
        path = self._doc_path(doc_name)
        async with self._lock:
            return await self._editor.replace_section(path, section_header, new_content)

    async def search_docs(
        self,
        query: str,
        doc_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[SearchHit]:
        """Full-text search across context documents.

        Args:
            query: Text to search for (case-insensitive).
            doc_names: Specific docs to search (None = all .md files in _context/).
            max_results: Max matches to return.
        """
        file_names = doc_names or self._all_doc_names()
        return await self._editor.search(
            self.context_dir, query, file_names, max_results,
        )

    # ── Structured Operations ────────────────────────────────────

    async def init_task_brief(
        self,
        description: str,
        goals: list[str] | None = None,
        constraints: list[str] | None = None,
        success_criteria: list[str] | None = None,
        analysis_dimensions: list[str] | None = None,
        tool_plan: list[str] | None = None,
    ) -> None:
        """Initialize TASK_BRIEF.md with structured task information.

        Called once at task start. Idempotent: overwrites if already exists.
        """
        now = _now_str()
        goals_md = "\n".join(f"- [ ] {g}" for g in (goals or []))
        constraints_md = "\n".join(f"- {c}" for c in (constraints or []))
        criteria_md = "\n".join(f"- {c}" for c in (success_criteria or []))
        dims_md = "\n".join(f"- {d}" for d in (analysis_dimensions or []))
        plan_md = "\n".join(f"{i+1}. {s}" for i, s in enumerate(tool_plan or []))

        content = f"""# Task Brief

**Task ID:** `{self.task_id_short}`
**Created:** {now}

## Description
{description}

## Goals
{goals_md or "- (auto-determined by agent)"}

## Constraints
{constraints_md or "- (none specified)"}

## Success Criteria
{criteria_md or "- (determined during execution)"}

## Analysis Dimensions
{dims_md or "- (auto-analyzed)"}

## Tool Plan
{plan_md or "(agent decides tools dynamically)"}
"""
        await self.write_doc(DOC_TASK_BRIEF, content, mode="replace")

        # NOTE: Other docs (PROGRESS, FINDINGS, DECISIONS, SCRATCHPAD) are NOT
        # auto-created. They are created on-demand when agents write to them
        # via task_doc_write. This avoids cluttering the workspace with empty
        # template files.

        logger.info(
            "twm_task_brief_initialized",
            task_id=self.task_id_short,
            goals=len(goals or []),
        )

    async def log_progress(
        self,
        phase: str,
        summary: str,
        details: dict[str, Any] | None = None,
        status: str = "completed",
    ) -> None:
        """Log progress entry to PROGRESS.md.

        Args:
            phase: Phase name (RESEARCH, ANALYSIS, SYNTHESIS, etc.)
            summary: Human-readable summary of what happened.
            details: Optional structured data (metrics, counts).
            status: Phase status (in_progress, completed, failed).
        """
        now = _now_str()
        details_md = ""
        if details:
            details_lines = [f"- {k}: {v}" for k, v in details.items()]
            details_md = "\n**Details:**\n" + "\n".join(details_lines)

        entry = f"""
---

## Phase: {phase.upper()} [{status}]
**Timestamp:** {now}
**Summary:** {summary}
{details_md}
"""
        await self.write_doc(DOC_PROGRESS, entry.strip(), mode="append")

    async def add_finding(
        self,
        agent_name: str,
        source: str,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        round_num: int = 0,
    ) -> None:
        """Add a discovery/finding to FINDINGS.md.

        Args:
            agent_name: Which agent found this.
            source: Source tool or method (web_search, analysis, etc.).
            content: The actual finding content.
            importance: 0.0-1.0, higher = more important.
            tags: Optional classification tags.
            round_num: Which execution round.
        """
        now = _now_str()
        tags_str = f" | tags: {', '.join(tags)}" if tags else ""

        entry = f"""
## {agent_name} via {source} (Round {round_num}) [importance: {importance:.1f}]{tags_str}
_Found at: {now}_

{content.strip()}
"""
        await self.write_doc(DOC_FINDINGS, entry.strip(), mode="append")

    async def record_decision(
        self,
        decision: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        agent_name: str = "",
        round_num: int = 0,
    ) -> None:
        """Record a key decision to DECISIONS.md.

        Args:
            decision: What was decided.
            reasoning: Why this was chosen.
            alternatives: What else was considered (and rejected).
            agent_name: Which agent/phase made this decision.
            round_num: Execution round number.
        """
        now = _now_str()
        alts_md = ""
        if alternatives:
            alts_lines = [f"- {a}" for a in alternatives]
            alts_md = "\n**Alternatives considered:**\n" + "\n".join(alts_lines)

        entry = f"""
---

## Decision (Round {round_num}{f' — {agent_name}' if agent_name else ''})
_Recorded at: {now}_

**Decision:** {decision}
**Reasoning:** {reasoning}
{alts_md}
"""
        await self.write_doc(DOC_DECISIONS, entry.strip(), mode="append")

    async def add_scratchpad_note(
        self,
        note: str,
        label: str = "",
    ) -> None:
        """Add a free-form note to SCRATCHPAD.md."""
        now = _now_str()
        prefix = f"**[{label}]** " if label else ""
        entry = f"\n- [{now}] {prefix}{note}"
        await self.write_doc(DOC_SCRATCHPAD, entry.strip(), mode="append")

    # ── Context Building for Agent Prompts ────────────────────────

    async def build_agent_context(
        self,
        agent_name: str,
        phase: str,
        max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
    ) -> str:
        """Build context string for injection into agent's system prompt.

        Progressive disclosure strategy:
        1. TASK_BRIEF summary (goals only) — always included
        2. Current phase focus — always included
        3. Recent findings (filtered by importance) — up to budget
        4. Key decisions — most recent 3
        5. PROGRESS summary of completed phases — compressed

        Args:
            agent_name: Name of requesting agent.
            phase: Current execution phase.
            max_chars: Maximum context size in characters.

        Returns:
            Formatted context string ready for prompt injection.
        """
        parts: list[str] = []
        chars_used = 0

        def _add(text: str, max_alloc: int) -> None:
            nonlocal chars_used
            if chars_used >= max_chars:
                return
            remaining = max_chars - chars_used
            trimmed = text[:min(max_alloc, remaining)]
            parts.append(trimmed)
            chars_used += len(trimmed)

        # 1. Task brief summary (goals)
        brief = await self.read_doc(DOC_TASK_BRIEF, section="## Goals")
        if brief:
            _add(f"## Task Goals\n{brief.strip()}\n", 400)

        # 2. Current phase
        _add(f"\n## Current Phase: {phase.upper()}\n", 100)

        # 3. Recent findings (top by importance)
        findings = await self.read_doc(DOC_FINDINGS)
        if findings:
            # Parse and sort by importance
            finding_sections = self._parse_findings(findings)
            # Sort descending by importance
            finding_sections.sort(key=lambda f: f.get("importance", 0.5), reverse=True)
            # Take top findings within budget
            findings_text = "\n## Key Findings So Far\n"
            for finding in finding_sections[:8]:
                entry = f"- **{finding.get('agent', '?')}** ({finding.get('source', '?')}): {finding.get('content', '')[:200]}\n"
                findings_text += entry
            _add(findings_text, 2000)

        # 4. Key decisions (most recent 3)
        decisions = await self.read_doc(DOC_DECISIONS)
        if decisions:
            # Get last 3 decision sections
            dec_sections = [s for s in decisions.split("---") if "Decision" in s]
            if dec_sections:
                recent = dec_sections[-3:]
                dec_text = "\n## Recent Decisions\n"
                for d in recent:
                    dec_text += d.strip()[:300] + "\n"
                _add(dec_text, 800)

        # 5. Progress summary
        progress = await self.read_doc(DOC_PROGRESS)
        if progress:
            # Extract phase summaries (compact)
            prog_lines = []
            for line in progress.split("\n"):
                if line.startswith("## Phase:") or line.startswith("**Summary:**"):
                    prog_lines.append(line.strip())
            if prog_lines:
                _add("\n## Progress\n" + "\n".join(prog_lines) + "\n", 600)

        # 6. WORKSPACE-AWARENESS: List all files in current task workspace
        # so agents know exactly what's available to read/edit/reference.
        try:
            file_sizes = await self.get_file_sizes()
            if file_sizes:
                file_listing = "\n## Your Task Files (workspace)\n"
                file_listing += f"Task ID: `{self.task_id_short}` | Path: `workspace_data/tasks/{self.task_id_short}/_context/`\n"
                for fname, fsize in sorted(file_sizes.items()):
                    size_str = f"{fsize:,}" if fsize < 10000 else f"{fsize/1024:.1f}K"
                    file_listing += f"- **{fname}.md** ({size_str} bytes)\n"
                file_listing += "\nUse `task_doc_read/write/edit/list` to interact with these files.\n"
                _add(file_listing, 800)
        except Exception:
            pass  # File listing is best-effort

        if not parts:
            return ""

        header = f"<task_context task_id=\"{self.task_id_short}\">\n"
        footer = "\n</task_context>"
        return header + "\n".join(parts) + footer

    async def get_context_snapshot(self) -> str:
        """Generate compact recovery summary from all task docs.

        Used when LLM context window overflows — provides a compact
        document-backed summary that replaces conversation history.

        Returns:
            Compact context string (max ~3000 chars).
        """
        parts: list[str] = []

        # Goals (200 chars)
        brief = await self.read_doc(DOC_TASK_BRIEF, section="## Description")
        if brief:
            parts.append(f"**Task:** {brief.strip()[:200]}")

        # Last progress entry (300 chars)
        progress = await self.read_doc(DOC_PROGRESS)
        if progress:
            sections = progress.split("---")
            if sections:
                parts.append(f"**Last progress:** {sections[-1].strip()[:300]}")

        # Top 5 findings (1500 chars)
        findings = await self.read_doc(DOC_FINDINGS)
        if findings:
            finding_sections = self._parse_findings(findings)
            finding_sections.sort(key=lambda f: f.get("importance", 0.5), reverse=True)
            findings_text = "**Top findings:**\n"
            for f in finding_sections[:5]:
                findings_text += f"- {f.get('content', '')[:250]}\n"
            parts.append(findings_text[:1500])

        # Last 2 decisions (500 chars)
        decisions = await self.read_doc(DOC_DECISIONS)
        if decisions:
            dec_sections = [s for s in decisions.split("---") if "Decision" in s]
            if dec_sections:
                parts.append(f"**Recent decisions:** {' | '.join(s.strip()[:200] for s in dec_sections[-2:])}")

        return "\n\n".join(parts)[:3000]

    async def get_all_findings(
        self,
        min_importance: float = 0.0,
        agent_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all findings as structured dicts.

        Args:
            min_importance: Minimum importance threshold.
            agent_filter: Only findings from this agent.

        Returns:
            List of finding dicts with agent, source, content, importance.
        """
        content = await self.read_doc(DOC_FINDINGS)
        if not content:
            return []

        findings = self._parse_findings(content)

        # Filter
        if min_importance > 0:
            findings = [f for f in findings if f.get("importance", 0) >= min_importance]
        if agent_filter:
            findings = [f for f in findings if agent_filter.lower() in f.get("agent", "").lower()]

        return findings

    # ── File Management ──────────────────────────────────────────

    async def get_file_sizes(self) -> dict[str, int]:
        """Get byte sizes of all context documents (predefined + custom)."""
        sizes: dict[str, int] = {}
        for name in self._all_doc_names():
            path = self._doc_path(name)
            if path.exists():
                sizes[name] = path.stat().st_size
        return sizes

    async def list_docs(self) -> list[dict[str, Any]]:
        """List all .md files in _context/ directory with metadata.

        Returns both predefined and custom documents.
        """
        docs: list[dict[str, Any]] = []
        for name in self._all_doc_names():
            path = self._doc_path(name)
            if path.exists():
                stat = path.stat()
                docs.append({
                    "name": name,
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc,
                    ).isoformat(),
                })
        return docs

    async def delete_doc(self, doc_name: str) -> bool:
        """Delete a context document.

        Predefined docs (TASK_BRIEF, PROGRESS, etc.) can also be deleted,
        but they may be recreated by auto-context hooks.

        Args:
            doc_name: Document name (without .md extension).

        Returns:
            True if the file was deleted, False if it didn't exist.
        """
        path = self._doc_path(doc_name)
        if not path.exists():
            return False

        async with self._lock:
            path.unlink()

        logger.info(
            "twm_doc_deleted",
            task_id=self.task_id_short,
            doc=doc_name,
        )
        return True

    def cleanup(self) -> None:
        """Release resources (lock) when task is done."""
        _cleanup_task_lock(self.task_id)

    # ── Internal Helpers ─────────────────────────────────────────

    @staticmethod
    def _extract_section(content: str, section_header: str) -> str:
        """Extract markdown section content (from header to next same-level header)."""
        lines = content.split("\n")
        header_stripped = section_header.strip()

        # Determine header level
        header_level = 0
        for ch in header_stripped:
            if ch == "#":
                header_level += 1
            else:
                break

        # Find section start
        section_start = -1
        for i, line in enumerate(lines):
            if line.strip() == header_stripped:
                section_start = i + 1  # Skip the header line itself
                break

        if section_start < 0:
            return ""

        # Find section end
        section_end = len(lines)
        for i in range(section_start, len(lines)):
            line_stripped = lines[i].strip()
            if line_stripped.startswith("#"):
                line_level = sum(1 for c in line_stripped if c == "#")
                if line_level <= header_level:
                    section_end = i
                    break

        return "\n".join(lines[section_start:section_end]).strip()

    @staticmethod
    def _parse_findings(content: str) -> list[dict[str, Any]]:
        """Parse FINDINGS.md content into structured dicts.

        Expected format per finding:
        ## AgentName via source (Round N) [importance: 0.8]
        Content here...
        """
        findings: list[dict[str, Any]] = []
        sections = content.split("\n## ")

        for section in sections:
            if not section.strip():
                continue

            lines = section.strip().split("\n")
            header = lines[0]

            # Parse header: "AgentName via source (Round N) [importance: 0.8]"
            agent = ""
            source = ""
            importance = 0.5
            round_num = 0

            if " via " in header:
                parts = header.split(" via ", 1)
                agent = parts[0].strip()
                rest = parts[1] if len(parts) > 1 else ""

                # Extract source
                if " (" in rest:
                    source = rest.split(" (")[0].strip()

                # Extract importance
                if "[importance:" in rest:
                    try:
                        imp_str = rest.split("[importance:")[1].split("]")[0].strip()
                        importance = float(imp_str)
                    except (IndexError, ValueError):
                        pass

                # Extract round
                if "(Round " in rest:
                    try:
                        round_str = rest.split("(Round ")[1].split(")")[0].strip()
                        round_num = int(round_str)
                    except (IndexError, ValueError):
                        pass

            # Content = everything after header (skip timestamp line)
            body_lines = []
            for line in lines[1:]:
                if line.startswith("_Found at:"):
                    continue
                body_lines.append(line)

            findings.append({
                "agent": agent,
                "source": source,
                "importance": importance,
                "round": round_num,
                "content": "\n".join(body_lines).strip(),
            })

        return findings


# ── Module-level Helpers ─────────────────────────────────────────────────

def _now_str() -> str:
    """Current timestamp string in UTC+7 (Vietnam timezone)."""
    tz_vn = timezone(timedelta(hours=7))
    return datetime.now(tz_vn).strftime("%Y-%m-%d %H:%M:%S UTC+7")


def get_task_working_memory(task_id: str | None = None) -> TaskWorkingMemory | None:
    """Factory: get TaskWorkingMemory for current task.

    If task_id not provided, reads from execution context (current_task_id).
    Returns None if no task context is available.
    """
    if not task_id:
        try:
            from app.core.execution_context import current_task_id
            task_id = current_task_id.get("")
        except Exception:
            pass

    if not task_id:
        return None

    return TaskWorkingMemory(task_id)
