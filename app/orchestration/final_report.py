"""
Nexus - Final Report Generator.

IMPROVEMENT: No limits - read everything, synthesize everything, verify everything.

Reads TASK_BRIEF.md, ROE.md, and ACTIVITY.md in their ENTIRETY,
synthesizes them into a comprehensive FINAL_REPORT.md.
Handles long files by processing in segments to avoid context overflow.
Uses multi-pass verification to ensure nothing is missed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
# IMPROVEMENT: Increased chunk size for better context per LLM call
_CHUNK_SIZE = 30000  # Characters per chunk — larger chunks = better synthesis

# CC-8: the report is assembled by appending one section per ACTIVITY chunk. With
# no cap it ballooned to 200-534 KB with blocks pasted 13-18×. Bound the assembled
# size and drop near-duplicate sections (content-based, not heading-only).
_MAX_REPORT_CHARS = 50_000
_MAX_DUP_SECTION = 2  # a near-identical section survives at most this many times


class FinalReportGenerator:
    """Generates FINAL_REPORT.md by reading and synthesizing source files.

    IMPROVEMENT: No artificial limits on file reading or section count.
    Read everything, synthesize everything, verify everything.

    Strategy:
    1. Read TASK_BRIEF.md (full content)
    2. Read ROE.md (full content)
    3. Read ACTIVITY.md in full — no size cap
    4. For each chunk, extract key information and append to report
    5. After ALL chunks processed, multi-pass verification:
       a. Read back the report
       b. Compare against sources
       c. Add missing information
       d. Verify again
    """

    def __init__(
        self,
        task_id: str,
        llm: Any,
        event_callback: Optional[Callable] = None,
    ) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._llm = llm
        self._event_callback = event_callback
        self._context_dir = task_paths.context_dir(task_id)
        self._report_path = self._context_dir / "FINAL_REPORT.md"

    async def generate(self) -> str:
        """Generate comprehensive FINAL_REPORT.md.

        IMPROVEMENT: No limits — reads ALL content, processes ALL chunks,
        performs multi-pass verification for completeness.

        Returns the final report content.
        """
        await self._emit("report_building", {
            "section": "Generating Final Report — reading source files and synthesizing...",
            "status": "active",
        })

        # IMPROVEMENT: Read ALL source files without any size cap
        task_brief = self._read_file("TASK_BRIEF.md")
        roe = self._read_file("ROE.md")
        todo = self._read_file("TODO.md")
        contract = self._read_file("CONTRACT.md")

        # ARTIFACT-FIRST: for BUILD tasks the deliverable is the artifact, not prose.
        # Produce a concise hand-over note (what was built, where it is, how to run it)
        # instead of a heavy multi-pass narrative report.
        if self._is_build_mode(contract):
            report = await self._build_handover_report(task_brief, contract, todo)
            self._write_report(report)
            await self._emit("report_building", {
                "section": f"✅ Hand-over note complete — BUILD task, {len(report)} chars (artifact-first)",
                "status": "done",
            })
            logger.info("final_report_handover", task_id=self._task_id[:8], report_len=len(report))
            return report
        # Read ALL activity: epoch archives + current file (preserves full history)
        activity = ""
        epoch_files = sorted(self._context_dir.glob("ACTIVITY_epoch*.md"))
        for epoch_file in epoch_files:
            try:
                activity += epoch_file.read_text(encoding="utf-8")
            except Exception:
                pass
        activity += self._read_file("ACTIVITY.md")

        if not activity:
            # No activity — minimal report
            report = self._build_minimal_report(task_brief)
            self._write_report(report)
            return report

        # Build report incrementally
        report_parts: list[str] = []

        # Part 1: Executive Summary + Task Overview (from TASK_BRIEF + ROE + TODO)
        summary_section = await self._synthesize_header(task_brief, roe, todo)
        report_parts.append(summary_section)

        # Part 2: Process ACTIVITY.md in chunks — NO cap on number of chunks
        activity_chunks = self._split_into_chunks(activity, _CHUNK_SIZE)
        total_chunks = len(activity_chunks)

        # Track what has already been covered to prevent duplication
        covered_topics: list[str] = []

        # IMPROVEMENT: Process ALL chunks — no _MAX_REPORT_SECTIONS limit
        for i, chunk in enumerate(activity_chunks):
            await self._emit("report_building", {
                "section": f"Processing activity log — chunk {i + 1}/{total_chunks}",
                "status": "active",
            })

            section = await self._synthesize_chunk(
                chunk, i + 1, total_chunks, covered_topics
            )
            if section:
                report_parts.append(section)
                # Extract headings from synthesized section to track coverage
                covered_topics.extend(self._extract_headings(section))

        # CC-8: cap growth + drop near-duplicate sections BEFORE appending the
        # artifact inventory (the inventory must always survive). A final report is
        # a synthesis, not an unbounded per-chunk accumulator.
        report_parts = self._cap_report_parts(report_parts)

        # Part 2.5: Append artifact inventory (list of _work/ files produced)
        artifact_section = self._build_artifact_inventory()
        if artifact_section:
            report_parts.append(artifact_section)

        # Assemble full report
        full_report = "\n\n".join(report_parts)

        # Part 3: Multi-pass verification
        # IMPROVEMENT: Multiple verification passes to ensure completeness
        full_report = await self._multi_pass_verify(
            full_report, task_brief, activity, total_chunks
        )

        # SECURITY: redact secrets from the final report before persisting.
        # Tool outputs are already scanned/redacted, but agent-written summaries
        # can still accidentally include credentials.
        try:
            from app.core.tool_guard import redact_tool_output
            full_report = redact_tool_output(full_report)
        except Exception:
            pass

        # CC-2: ground headline figures against the structured artifacts. A number
        # with a unit that no script produced is annotated [UNSOURCED] (not removed).
        full_report = self._ground_numbers(full_report)

        # Write to file
        self._write_report(full_report)

        await self._emit("report_building", {
            "section": f"✅ Final Report complete — {len(full_report)} chars, {len(report_parts)} sections",
            "status": "done",
        })

        logger.info(
            "final_report_generated",
            task_id=self._task_id[:8],
            report_len=len(full_report),
            chunks_processed=total_chunks,
        )

        return full_report

    @staticmethod
    def _is_build_mode(contract: str) -> bool:
        """Detect a BUILD-mode deliverable contract.

        BUILD tasks deliver a runnable/openable artifact; the report is just a
        hand-over note. Reads the 'Work Mode:' line written by build_contract().
        """
        if not contract:
            return False
        for line in contract.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("## work mode:"):
                return stripped.split(":", 1)[1].strip().upper() == "BUILD"
        return False

    async def _build_handover_report(
        self, task_brief: str, contract: str, todo: str = ""
    ) -> str:
        """Build a concise hand-over note for a BUILD task.

        Structure: what was built + where it lives + how to run/open it, followed by
        the contract and the real artifact inventory. The artifacts ARE the deliverable;
        this note just points the user at them — no multi-pass prose synthesis.
        """
        artifact_section = self._build_artifact_inventory()

        messages = [
            {"role": "system", "content": (
                "You are writing a short HAND-OVER NOTE for a BUILD task. The real "
                "deliverable is the artifact(s) listed below — NOT this note. Write briefly:\n"
                "1. A title (# Hand-over)\n"
                "2. **What was built** (2-4 sentences)\n"
                "3. **How to run / open it** (exact commands or URL, from the activity)\n"
                "4. **Known limitations** (only if real)\n\n"
                "Be concise and concrete. Do NOT pad with generic prose. Use markdown."
            )},
            {"role": "user", "content": (
                f"## Task Brief\n{task_brief}\n\n"
                f"## Deliverable Contract\n{contract}\n\n"
                f"## Progress (TODO)\n{todo}\n\n"
                f"## Artifacts produced\n{artifact_section or '(none detected)'}\n"
            )},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.2, max_tokens=2048)
            note = result.get("content", "") or "# Hand-over\n\n(Hand-over note generation failed)"
        except Exception as e:
            logger.warning("handover_note_failed", error=str(e))
            note = f"# Hand-over\n\n## Task\n{task_brief}\n"

        # Deterministically surface any live preview/demo URL at the very top, so
        # the user gets a working link even if the LLM didn't copy it into prose
        # (audit gap B). Source: the full activity log (tool outputs land there).
        banner = ""
        try:
            from app.orchestration.preview_url import build_preview_banner
            activity = ""
            for ep in sorted(self._context_dir.glob("ACTIVITY_epoch*.md")):
                activity += self._read_file(ep.name)
            activity += self._read_file("ACTIVITY.md")
            banner = build_preview_banner(activity)
        except Exception:
            banner = ""

        parts = [banner, note] if banner else [note]
        if contract:
            parts.append(f"## Deliverable Contract\n{contract}")
        if artifact_section:
            parts.append(artifact_section)

        full = "\n\n".join(parts)
        try:
            from app.core.tool_guard import redact_tool_output
            full = redact_tool_output(full)
        except Exception:
            pass
        full = self._ground_numbers(full)
        return full

    def _ground_numbers(self, report_text: str) -> str:
        """CC-2: annotate report figures that no structured artifact produced with
        [UNSOURCED]. Fail-open: any error (or no structured artifact) returns the
        report unchanged — grounding must never corrupt or drop the report."""
        try:
            from app.core import task_paths
            from app.orchestration.numeric_integrity import (
                build_numeric_facts,
                ground_report_numbers,
            )
            facts = build_numeric_facts(task_paths.work_dir(self._task_id))
            return ground_report_numbers(report_text, facts)
        except Exception as e:
            logger.debug("ground_numbers_failed", error=str(e), task_id=self._task_id[:8])
            return report_text

    async def _synthesize_header(self, task_brief: str, roe: str, todo: str = "") -> str:
        """Create report header from TASK_BRIEF, ROE, and TODO.

        IMPROVEMENT: Feed full task_brief, ROE, and TODO content (not truncated).
        """
        todo_section = f"\n\n## TODO.md (Progress Tracking)\n{todo}" if todo else ""

        messages = [
            {"role": "system", "content": (
                "You are creating the header section of a final task report. "
                "Based on the TASK_BRIEF, ROE, and TODO below, write:\n"
                "1. A title (# Final Report)\n"
                "2. An Executive Summary (2-3 sentences)\n"
                "3. A Task Overview section\n"
                "4. A Methodology section (team composition, approach)\n"
                "5. A Progress Summary section (from TODO — what was completed, final progress %)\n\n"
                "Be concise but complete. Use markdown formatting."
            )},
            {"role": "user", "content": (
                f"## TASK_BRIEF.md\n{task_brief}\n\n"
                f"## ROE.md\n{roe}"
                f"{todo_section}\n"
            )},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.2, max_tokens=4096)
            return result.get("content", "# Final Report\n\n(Header generation failed)")
        except Exception as e:
            logger.warning("report_header_failed", error=str(e))
            return f"# Final Report\n\n## Task Overview\n{task_brief}\n"

    async def _synthesize_chunk(
        self,
        chunk: str,
        chunk_num: int,
        total: int,
        covered_topics: list[str] | None = None,
    ) -> str:
        """Extract key information from one chunk of ACTIVITY.md.

        Args:
            chunk: The activity log text chunk to synthesize.
            chunk_num: Current chunk number (1-based).
            total: Total number of chunks.
            covered_topics: List of section headings already in the report,
                used to instruct the LLM to skip duplicate information.
        """
        # Build dedup context so the LLM knows what's already covered
        dedup_instruction = ""
        if covered_topics:
            topics_list = "\n".join(f"- {t}" for t in covered_topics[-30:])
            dedup_instruction = (
                "\n\nIMPORTANT — The report already contains sections covering:\n"
                f"{topics_list}\n\n"
                "Do NOT repeat information already covered above. "
                "If this chunk contains the same data (e.g. same repository stats, "
                "same table, same API responses), SKIP it entirely and return ONLY "
                "genuinely NEW information not yet in the report. "
                "If there is nothing new, respond with exactly: [NO_NEW_CONTENT]"
            )

        messages = [
            {"role": "system", "content": (
                "You are extracting key findings from a section of an activity log. "
                f"This is chunk {chunk_num}/{total}.\n\n"
                "Extract ONLY NEW important information:\n"
                "- Key findings and data discovered\n"
                "- Important tool results (with actual data, numbers, URLs)\n"
                "- Decisions made and their reasoning\n"
                "- Artifacts created\n"
                "- Code snippets or configurations produced\n"
                "- Errors encountered and how they were resolved\n\n"
                "Evidence rule:\n"
                "- Do NOT label something as CONFIRMED unless the chunk includes concrete evidence (tool output, HTTP request/response, logs).\n"
                "- If it's only an agent claim or inference, label it POTENTIAL and state what evidence is missing.\n\n"
                "Format as a report section with ## heading. "
                "Include ALL specific details — numbers, URLs, code snippets, full data. "
                "Do NOT summarize or compress important data. "
                "Do NOT include meta-commentary about the process."
                f"{dedup_instruction}"
            )},
            {"role": "user", "content": f"Activity log chunk:\n{chunk}"},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.2, max_tokens=8192)
            content = result.get("content", "")
            # Skip if LLM indicates no new content
            if "[NO_NEW_CONTENT]" in content:
                return ""
            return content if len(content) > 30 else ""
        except Exception as e:
            logger.debug("report_chunk_synth_failed", chunk=chunk_num, error=str(e))
            return ""

    @staticmethod
    def _cap_report_parts(parts: list[str]) -> list[str]:
        """Bound report growth (CC-8): drop near-duplicate sections and cap total size.

        - Content-based dedup: sections whose normalized body has already appeared
          ``_MAX_DUP_SECTION`` times are skipped (the heading-only dedup missed
          blocks pasted under slightly different headings).
        - Hard size cap: stop appending once the assembled report would exceed
          ``_MAX_REPORT_CHARS`` (a final report is a synthesis, not an accumulator).
        Pure + side-effect-free so it is unit-testable without an LLM.
        """
        import re as _re

        def _norm(s: str) -> str:
            # Strip leading heading + collapse whitespace so "same body, new
            # heading" and reflowed copies compare equal.
            body = _re.sub(r"^\s*#{1,6}\s.*$", "", s, count=1, flags=_re.MULTILINE)
            return _re.sub(r"\s+", " ", body).strip().lower()

        seen: dict[str, int] = {}
        kept: list[str] = []
        total = 0
        for part in parts:
            if not part:
                continue
            key = _norm(part)
            if key:
                count = seen.get(key, 0)
                if count >= _MAX_DUP_SECTION:
                    continue  # near-duplicate seen enough times — drop
                seen[key] = count + 1
            added = len(part) + 2  # +2 for the "\n\n" join
            if total + added > _MAX_REPORT_CHARS and kept:
                break  # size cap reached — stop accumulating
            kept.append(part)
            total += added
        return kept

    @staticmethod
    def _extract_headings(section: str) -> list[str]:
        """Extract markdown headings AND key entities from a synthesized section.

        Used to build a running list of topics already covered,
        preventing duplication in subsequent chunks.
        """
        headings: list[str] = []
        for line in section.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("### "):
                heading_text = stripped.lstrip("#").strip()
                if heading_text:
                    headings.append(heading_text)
                    # Also add a normalized key (lowercase, no parens) for fuzzy matching
                    normalized = heading_text.split("(")[0].strip().lower()
                    if normalized and normalized != heading_text.lower():
                        headings.append(normalized)
        return headings

    async def _multi_pass_verify(
        self,
        report: str,
        task_brief: str,
        activity: str,
        total_chunks: int = 1,
    ) -> str:
        """Multi-pass verification: read back → check → supplement → verify again.

        Pass 1: Check against task_brief requirements
        Pass 2: Check against activity highlights for missed data (head+mid+tail)

        The two passes exist to catch information dropped when a report is synthesized
        across MANY activity chunks. When the activity fit in a single chunk (so nothing
        was split) AND the resulting report is small, the header synthesis already saw
        everything in one pass — verification would just re-bill two LLM calls for no gain.
        Skip it in that case; always run it for multi-chunk / large reports where drop-out
        is real.
        """
        _VERIFY_MIN_REPORT_CHARS = 12000
        if total_chunks <= 1 and len(report) < _VERIFY_MIN_REPORT_CHARS:
            logger.debug(
                "final_report_verify_skipped",
                task_id=self._task_id[:8],
                report_len=len(report),
                total_chunks=total_chunks,
            )
            return report

        # Pass 1: Verify against task brief requirements
        report = await self._verify_against_brief(report, task_brief)

        # Pass 2: Verify against activity for missed critical data
        # Sample head + middle + tail to cover full activity span
        activity_sample = self._sample_activity(activity)
        report = await self._verify_against_activity(report, activity_sample)

        return report

    def _sample_activity(self, activity: str) -> str:
        """Sample head + middle + tail of activity for verification.

        Ensures verification covers the full span of execution, not just
        the final results.
        """
        if len(activity) <= _CHUNK_SIZE:
            return activity

        segment_size = _CHUNK_SIZE // 3
        head = activity[:segment_size]
        mid_start = (len(activity) - segment_size) // 2
        mid = activity[mid_start:mid_start + segment_size]
        tail = activity[-segment_size:]

        return (
            f"[HEAD — Start of execution]\n{head}\n\n"
            f"[MID — Middle of execution]\n{mid}\n\n"
            f"[TAIL — Final results]\n{tail}"
        )

    async def _verify_against_brief(self, report: str, task_brief: str) -> str:
        """Pass 1: Verify report covers all requirements from task brief."""
        messages = [
            {"role": "system", "content": (
                "You are verifying a final report for completeness against the original task brief. "
                "Compare the report against the task brief requirements. "
                "If anything important is MISSING from the report, add it. "
                "If the report is complete, return it unchanged.\n\n"
                "RULES:\n"
                "- Only ADD content, never remove existing content\n"
                "- Focus on missing conclusions, data points, or recommendations\n"
                "- Add '## Additional Findings' or '## Conclusions' section if needed\n"
                "- Return the COMPLETE report (original + any additions)\n"
                "- Be thorough — check every requirement in the brief"
            )},
            {"role": "user", "content": (
                f"## Task Brief (what was requested)\n{task_brief}\n\n"
                f"## Current Report\n{report}\n\n"
                "Is the report complete? If not, add missing sections."
            )},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.1, max_tokens=16384)
            verified = result.get("content", "")
            # Only use verified version if it's at least as long (added content, not truncated)
            if verified and len(verified) >= len(report) * 0.9:
                return verified
        except Exception as e:
            logger.debug("report_verify_brief_skipped", error=str(e))

        return report

    async def _verify_against_activity(self, report: str, activity_tail: str) -> str:
        """Pass 2: Verify report includes critical data from activity log."""
        messages = [
            {"role": "system", "content": (
                "You are doing a FINAL verification pass on a report. "
                "Check if any critical DATA POINTS from the activity log are missing from the report. "
                "Focus on:\n"
                "- Specific numbers, statistics, or measurements\n"
                "- URLs or references discovered\n"
                "- Code snippets or configurations created\n"
                "- Error resolutions\n"
                "- Final conclusions from agents\n\n"
                "If you find missing critical data, append it to the report. "
                "If nothing is missing, return the report unchanged.\n"
                "Return the COMPLETE report."
            )},
            {"role": "user", "content": (
                f"## Activity Log (tail section — final results)\n{activity_tail}\n\n"
                f"## Current Report\n{report}\n\n"
                "Any critical data missing? Add if so."
            )},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.1, max_tokens=16384)
            verified = result.get("content", "")
            if verified and len(verified) >= len(report) * 0.9:
                return verified
        except Exception as e:
            logger.debug("report_verify_activity_skipped", error=str(e))

        return report

    def _build_artifact_inventory(self) -> str:
        """Build a markdown section listing all files produced in _work/ directory.

        This helps users know exactly what artifacts were created and where to find them.
        """
        from app.core import task_paths
        work_dir = task_paths.work_dir(self._task_id)

        if not work_dir.exists():
            return ""

        files: list[tuple[str, int]] = []
        try:
            for item in sorted(work_dir.rglob("*")):
                if not item.is_file():
                    continue
                # Skip __pycache__ and hidden files
                rel = item.relative_to(work_dir)
                if "__pycache__" in str(rel) or str(rel).startswith("."):
                    continue
                files.append((str(rel), item.stat().st_size))
        except Exception:
            return ""

        if not files:
            return ""

        lines = ["## Deliverable Artifacts\n"]
        lines.append(f"Location: `{work_dir}`\n")
        lines.append("| File | Size |")
        lines.append("|------|------|")
        for name, size in files:
            if size >= 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"
            lines.append(f"| `{name}` | {size_str} |")

        lines.append(f"\n**Total: {len(files)} files**")
        return "\n".join(lines)

    def _read_file(self, filename: str) -> str:
        """Read a file from _context/ directory.

        IMPROVEMENT: No max_chars cap — read the ENTIRE file regardless of size.
        The file system is the source of truth and we must read all of it.
        """
        path = self._context_dir / filename
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            return content
        except Exception as e:
            logger.warning("report_read_failed", file=filename, error=str(e))
            return ""

    def _write_report(self, content: str) -> None:
        """Write FINAL_REPORT.md."""
        self._context_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._report_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.error("report_write_failed", error=str(e))

    def _split_into_chunks(self, text: str, chunk_size: int) -> list[str]:
        """Split text into chunks, preferring split at section boundaries."""
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        remaining = text

        while remaining:
            if len(remaining) <= chunk_size:
                chunks.append(remaining)
                break

            # Try to split at a section boundary (## or ---)
            candidate = remaining[:chunk_size]
            split_point = chunk_size

            # Look for last section marker within the chunk
            for marker in ["\n## ", "\n---\n", "\n### "]:
                last_marker = candidate.rfind(marker)
                if last_marker > chunk_size // 2:  # Only if in second half
                    split_point = last_marker
                    break

            chunks.append(remaining[:split_point])
            remaining = remaining[split_point:]

        return chunks

    def _build_minimal_report(self, task_brief: str) -> str:
        """Build minimal report when no activity exists."""
        return (
            "# Final Report\n\n"
            "## Status\n"
            "Task completed with minimal activity.\n\n"
            f"## Task Brief\n{task_brief}\n"
        )

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event via callback."""
        if self._event_callback is None:
            return
        try:
            await self._event_callback({"event_type": event_type, **data})
        except Exception:
            pass
