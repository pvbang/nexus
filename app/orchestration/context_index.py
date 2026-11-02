"""
Nexus - Context Index.

Human-readable index auto-maintained alongside LEDGER.jsonl.
Provides a quick overview that agents can scan in O(1) to know
what happened without parsing the full activity log.

CONTEXT_INDEX.md is the "table of contents" for the task:
- What was found per turn (1 line each)
- Which agent did what
- Where to find full details

This file is regenerated (not appended) after each turn
because it's an index, not a log. Always reflects current state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from app.orchestration.context_ledger import ContextLedger, LedgerEntry
from app.orchestration.ledger_extract import smart_truncate

logger = structlog.get_logger(__name__)


def _cell(text: str) -> str:
    """Make text safe for a markdown table cell.

    A raw newline ends the row and an unescaped ``|`` starts a new column, both
    of which corrupt the table (and the prompt the agent reads). Collapse
    whitespace and escape pipes.
    """
    return " ".join(text.split()).replace("|", "\\|")

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))


class ContextIndex:
    """Maintains CONTEXT_INDEX.md — human-readable overview of task progress.

    Regenerated after each turn to reflect current accumulated state.
    Agents read this to quickly understand what exists before acting.
    """

    def __init__(self, task_id: str, ledger: ContextLedger) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._ledger = ledger
        self._context_dir = task_paths.context_dir(task_id)
        self._index_path = self._context_dir / "CONTEXT_INDEX.md"

    def rebuild(
        self,
        current_turn: int,
        progress_pct: int = 0,
        user_message: str = "",
        active_blockers: list[str] | None = None,
    ) -> None:
        """Rebuild CONTEXT_INDEX.md from ledger state.

        Called after each turn evaluation. Fast — just reads ledger
        and writes a compact markdown table.
        """
        entries = self._ledger.query(limit=500)  # All entries, recent first

        # Group by turn
        by_turn: dict[int, list[LedgerEntry]] = {}
        for e in entries:
            by_turn.setdefault(e.turn, []).append(e)

        # Build markdown
        parts: list[str] = []
        parts.append("# Context Index\n")
        parts.append(f"**Task**: {smart_truncate(user_message, 100)}\n")
        parts.append(f"**Progress**: {progress_pct}% | **Turns completed**: {current_turn}\n")

        if active_blockers:
            parts.append(f"**Active blockers**: {'; '.join(active_blockers[:5])}\n")

        parts.append("")

        # Key findings summary (top 15 deduped)
        all_key_data = self._ledger.query_key_data(limit=15)
        if all_key_data:
            parts.append("## Key Findings Accumulated\n")
            for kd in all_key_data:
                parts.append(f"- {kd}")
            parts.append("")

        # Turn-by-turn table
        parts.append("## Turn History\n")
        parts.append("| Turn | Agent | Result | Key Data |")
        parts.append("|------|-------|--------|----------|")

        for turn_num in sorted(by_turn.keys()):
            for e in by_turn[turn_num]:
                status = "✓" if e.success else "✗"
                key_str = "; ".join(e.key_data[:3]) if e.key_data else "-"
                key_str = _cell(smart_truncate(" ".join(key_str.split()), 90))
                # Markdown table cells can't contain raw newlines OR unescaped pipes
                # — both break the column layout. _cell() collapses/escapes; truncate
                # on a word boundary so meaning survives. C-8 (continuity audit): 50
                # chars made Turn History useless as memory — 120 fits one clause.
                did_short = _cell(smart_truncate(" ".join(e.did_what.split()), 120))
                parts.append(
                    f"| {turn_num} | {e.agent_role} | {status} {did_short} | {key_str} |"
                )

        parts.append("")

        # Epoch summaries
        epochs = self._ledger._read_epochs()
        if epochs:
            parts.append("## Epoch Summaries\n")
            for ep in epochs:
                parts.append(f"- **Turns {ep.turns_range}**: {smart_truncate(ep.summary, 200)}")
            parts.append("")

        parts.append("\n---\n*Auto-generated from LEDGER.jsonl — do not edit manually*\n")

        # Write (overwrite — this is an index, not a log)
        content = "\n".join(parts)
        try:
            self._context_dir.mkdir(parents=True, exist_ok=True)
            self._index_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning("context_index_write_failed", error=str(e))

    def read_for_injection(self, max_chars: int = 4000) -> str:
        """Read the index content for injection into agent prompts.

        Returns compact version suitable for context window.
        """
        if not self._index_path.exists():
            return ""
        try:
            content = self._index_path.read_text(encoding="utf-8")
            if len(content) <= max_chars:
                return content
            # Truncate intelligently — keep header + key findings + recent turns
            lines = content.split("\n")
            result_lines: list[str] = []
            char_count = 0
            for line in lines:
                if char_count + len(line) + 1 > max_chars:
                    result_lines.append("[...index truncated — use activity_search for full details]")
                    break
                result_lines.append(line)
                char_count += len(line) + 1
            return "\n".join(result_lines)
        except Exception:
            return ""
