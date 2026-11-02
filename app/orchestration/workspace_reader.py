"""
Nexus - Workspace Reader.

Shared module that gives orchestration-layer agents (NexusOrchestrator,
EvolutionAgent, MetacognitiveAgent) full visibility into both _context/
and _work/ directories.

These agents don't have tool access — they're pure LLM calls. This module
reads the filesystem programmatically and builds a digest they can reason over.

Design principle: Read EVERYTHING relevant, summarize intelligently,
budget-aware. The orchestrator should never be "blind" to what exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# Heuristics for identifying "important" documents
_DESIGN_KEYWORDS = frozenset({
    "architect", "design", "spec", "plan", "schema", "structure",
    "research", "decision", "readme", "curriculum", "mapping",
    "report", "analysis", "strategy", "requirements", "brief",
})

_SKIP_PATTERNS = frozenset({"__pycache__", ".pyc", ".bin", ".png", ".jpg", ".gif"})


def _is_text_file(path: Path) -> bool:
    """Check if file is likely text (not binary)."""
    suffix = path.suffix.lower()
    if suffix in (".bin", ".pyc", ".png", ".jpg", ".gif", ".pdf", ".zip", ".exe"):
        return False
    if suffix in (".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
                  ".py", ".js", ".ts", ".html", ".css", ".sql", ".sh",
                  ".prisma", ".env", ".cfg", ".conf", ".xml", ".csv"):
        return True
    # Unknown extension: try reading first bytes
    try:
        with open(path, "rb") as f:
            chunk = f.read(512)
            return b"\x00" not in chunk  # Binary files contain null bytes
    except Exception:
        return False


def _is_design_document(path: Path) -> bool:
    """Heuristic: is this file a design/planning/architecture document?"""
    if path.suffix.lower() not in (".md", ".txt", ".rst"):
        return False
    name_lower = path.stem.lower().replace("-", " ").replace("_", " ")
    return any(kw in name_lower for kw in _DESIGN_KEYWORDS)


def _should_skip(rel_path: str) -> bool:
    """Check if file should be skipped in scan."""
    return any(pat in rel_path for pat in _SKIP_PATTERNS)


class WorkspaceReader:
    """Reads task workspace and builds context digest for orchestration agents.

    Usage:
        reader = WorkspaceReader(task_id)
        digest = reader.build_digest(max_chars=6000)  # Full digest
        digest = reader.build_digest(max_chars=2000)  # Compact for assignment injection
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._context_dir = task_paths.context_dir(task_id)
        self._work_dir = task_paths.work_dir(task_id)

    def build_digest(self, max_chars: int = 6000) -> str:
        """Build comprehensive workspace digest reading both _context/ and _work/.

        Sections (budget-allocated):
        1. Context documents summary (TASK_BRIEF constraints, TODO state, CONTEXT_INDEX)
        2. Work file inventory (what was actually produced)
        3. Design document content (headers of important docs)
        4. Drift detection prompt

        Args:
            max_chars: Total character budget for the digest.
        """
        parts: list[str] = []
        budget = max_chars

        # ── Section 1: _context/ documents (task "brain") ──
        context_summary = self._read_context_docs(budget=budget // 3)
        if context_summary:
            parts.append(context_summary)
            budget -= len(context_summary)

        # ── Section 2: _work/ file inventory ──
        inventory, work_files = self._scan_work_inventory()
        if inventory:
            parts.append(inventory)
            budget -= len(inventory)

        # ── Section 3: Design document content (from _work/) ──
        if budget > 500:
            design_content = self._read_design_docs(work_files, budget=budget - 200)
            if design_content:
                parts.append(design_content)
                budget -= len(design_content)

        # ── Section 4: Drift check prompt ──
        drift_check = (
            "\n### ⚓ Alignment Check\n"
            "Compare workspace deliverables against Task Constraints and Execution Plan above.\n"
            "Flag DRIFT if: tech stack changed, planned features missing, scope reduced silently."
        )
        parts.append(drift_check)

        return "\n\n".join(parts)[:max_chars]

    def _read_context_docs(self, budget: int = 2000) -> str:
        """Read key sections from _context/ documents.

        Reads: TASK_BRIEF (constraints), TODO (invariants + current progress),
        CONTEXT_INDEX (accumulated findings).
        """
        if not self._context_dir.exists():
            return ""

        parts: list[str] = ["## Task Context (_context/)"]
        remaining = budget - 30

        # 1. TASK_BRIEF: extract constraints/intent
        brief_path = self._context_dir / "TASK_BRIEF.md"
        if brief_path.exists() and remaining > 200:
            section = self._extract_section(
                brief_path,
                markers=["## Constraints", "**Constraints**", "- Constraints:",
                         "## Intent", "## Requirements", "## Tech Stack"],
                max_chars=min(1000, remaining // 2),
            )
            if section:
                parts.append(f"**TASK_BRIEF constraints:**\n{section}")
                remaining -= len(parts[-1])

        # 2. TODO: invariants + latest progress
        todo_path = self._context_dir / "TODO.md"
        if todo_path.exists() and remaining > 200:
            # Get invariants
            invariants = self._extract_section(
                todo_path,
                markers=["## Invariants"],
                max_chars=min(600, remaining // 3),
            )
            if invariants:
                parts.append(f"**TODO invariants:**\n{invariants}")
                remaining -= len(parts[-1])

            # Get latest progress (last ## Turn entry)
            latest_progress = self._read_latest_progress(todo_path, max_chars=min(400, remaining // 3))
            if latest_progress:
                parts.append(f"**Latest progress:**\n{latest_progress}")
                remaining -= len(parts[-1])

        # 3. CONTEXT_INDEX: key findings
        index_path = self._context_dir / "CONTEXT_INDEX.md"
        if index_path.exists() and remaining > 200:
            section = self._extract_section(
                index_path,
                markers=["## Key Findings", "**Key Findings**", "## Active Blockers"],
                max_chars=min(500, remaining),
            )
            if section:
                parts.append(f"**Key findings/blockers:**\n{section}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def build_work_inventory(self, max_chars: int = 4000) -> str:
        """Public: the _work/ file inventory the EXECUTING agent needs (A-1/R-1).

        The orchestrator gets the full workspace tree, but the agent that does the
        work was left blind to which deliverables already exist — so it re-did
        produced work or skipped files it thought were missing. This exposes the
        inventory (name + size, empty-scaffold dirs flagged) for the agent-context
        builder to inject. Budget-bounded; never raises.
        """
        try:
            inventory, _ = self._scan_work_inventory()
        except Exception:
            return ""
        return (inventory or "")[:max_chars]

    def _scan_work_inventory(self) -> tuple[str, list[tuple[str, int, Path]]]:
        """Scan _work/ and return (formatted inventory, file list).

        Returns tuple: (inventory_text, [(rel_path, size, full_path), ...])
        """
        if not self._work_dir.exists():
            return ("## Workspace (_work/): EMPTY — no deliverables produced yet.", [])

        files: list[tuple[str, int, Path]] = []
        dirs: list[Path] = []
        try:
            for item in sorted(self._work_dir.rglob("*")):
                if item.is_dir():
                    dirs.append(item)
                    continue
                if not item.is_file():
                    continue
                rel = str(item.relative_to(self._work_dir))
                if _should_skip(rel):
                    continue
                files.append((rel, item.stat().st_size, item))
        except Exception:
            return ("## Workspace (_work/): (scan error)", [])

        # Empty directories are NOT deliverables (task 3c44a93f bug #1): an agent
        # that only ran create_directory produced nothing. Surface them so the
        # orchestrator is not blind to them (it then can't assert they "remain on
        # disk" as if they were output), but label them clearly as non-deliverable.
        # "Empty" = no file ANYWHERE in the subtree, so a parent like ``redteam/``
        # (which leads to ``phase_1/recon.md``) is NOT flagged — only true dead-end
        # scaffold dirs like ``phase_2/`` are.
        empty_dirs = [
            str(d.relative_to(self._work_dir))
            for d in dirs
            if not _should_skip(str(d.relative_to(self._work_dir)))
            and not any(p.is_file() for p in d.rglob("*"))
        ]

        if not files:
            base = "## Workspace (_work/): EMPTY — no deliverables produced yet."
            if empty_dirs:
                base += (
                    "\n### Empty directories (NON-DELIVERABLE — created but hold no files):\n"
                    + "\n".join(f"  - `{d}/`" for d in empty_dirs[:25])
                )
            return (base, [])

        total_size = sum(s for _, s, _ in files)
        lines = [f"## Workspace (_work/): {len(files)} files, {total_size / 1024:.1f} KB"]

        for rel, size, path in files[:25]:
            size_str = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
            marker = " 📐" if _is_design_document(path) else ""
            lines.append(f"  - `{rel}` ({size_str}){marker}")

        if len(files) > 25:
            lines.append(f"  - ... +{len(files) - 25} more files")

        if empty_dirs:
            lines.append(
                "### Empty directories (NON-DELIVERABLE — created but hold no files):"
            )
            for d in empty_dirs[:25]:
                lines.append(f"  - `{d}/`")

        return ("\n".join(lines), files)

    def _read_design_docs(
        self,
        work_files: list[tuple[str, int, Path]],
        budget: int = 2000,
    ) -> str:
        """Read content from design/planning documents in _work/.

        Identifies important docs by keyword heuristics, reads headers.
        """
        design_docs: list[tuple[str, Path]] = []

        for rel, size, path in work_files:
            if size < 200:
                continue
            # Include if: design keyword match OR large .md file (likely important)
            if _is_design_document(path):
                design_docs.append((rel, path))
            elif path.suffix.lower() == ".md" and size > 8000:
                design_docs.append((rel, path))

        if not design_docs:
            return ""

        # Sort by size descending (larger docs likely more important)
        design_docs.sort(key=lambda x: x[1].stat().st_size, reverse=True)

        parts = ["### Design Documents (content summaries)"]
        remaining = budget - 50

        for rel, path in design_docs[:6]:
            if remaining <= 150:
                break
            try:
                if not _is_text_file(path):
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                # Read first N chars (header/overview — where decisions are stated)
                chars_to_read = min(500, remaining - 50)
                header = content[:chars_to_read].strip()
                # Trim at last complete line
                last_nl = header.rfind("\n")
                if last_nl > 150:
                    header = header[:last_nl]
                entry = f"\n**`{rel}`** ({path.stat().st_size / 1024:.1f}KB):\n{header}"
                parts.append(entry)
                remaining -= len(entry)
            except Exception:
                continue

        return "\n".join(parts) if len(parts) > 1 else ""

    def _extract_section(
        self,
        file_path: Path,
        markers: list[str],
        max_chars: int = 800,
    ) -> str:
        """Extract a section from a file by looking for marker headings.

        Tries each marker in order, returns first match.
        Section ends at next ## heading or max_chars.
        """
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return ""

        for marker in markers:
            idx = content.find(marker)
            if idx >= 0:
                section = content[idx:idx + max_chars]
                # Trim at next ## heading (section boundary)
                next_heading = section.find("\n## ", 10)
                if next_heading > 0:
                    section = section[:next_heading]
                return section.strip()

        return ""

    def _read_latest_progress(self, todo_path: Path, max_chars: int = 400) -> str:
        """Read the most recent progress entry from TODO.md.

        The writer (``nexus_orchestrator.update_todo``) emits progress as a
        line-anchored ``## Current Status — Turn N | Progress: X%`` block
        (rewrite model); the legacy append-log used ``## Turn N — ...``. We
        match EITHER, but only **line-anchored** (``^## ``).

        Task 3c44a93f bug #2: a plain ``rfind("## Turn ")`` matched the
        substring inside the Execution Plan's ``### Turn 6: PHASE 6`` heading
        (3 hashes) and mislabelled that FUTURE plan as "latest progress". A
        plan-only TODO (no progress block yet) must therefore return "" — never
        a plan heading. Line-anchoring on ``^## `` excludes ``### `` headings.
        """
        try:
            content = todo_path.read_text(encoding="utf-8")
        except Exception:
            return ""

        # Real progress markers, line-anchored. ``## Current Status — Turn N``
        # is the current rewrite-model marker; ``## Turn N`` the legacy
        # append-log one. Both require start-of-line + exactly two hashes, so a
        # ``### Turn N`` plan heading can never match.
        progress_re = re.compile(r"(?m)^## (?:Current Status — Turn|Turn) \d+\b")
        matches = list(progress_re.finditer(content))
        if not matches:
            return ""

        start = matches[-1].start()
        section = content[start:start + max_chars]
        # Trim at the next line-anchored ``## `` heading (not ``### `` subsections).
        next_heading = re.search(r"(?m)^## ", section[1:])
        if next_heading:
            section = section[: next_heading.start() + 1]

        return section.strip()

    def get_constraints_compact(self, max_chars: int = 800) -> str:
        """Get just the constraints/invariants for compact injection into assignments.

        Used when injecting into agent task_description (must be short).
        """
        parts: list[str] = []
        remaining = max_chars

        # TASK_BRIEF constraints
        brief_path = self._context_dir / "TASK_BRIEF.md"
        if brief_path.exists():
            section = self._extract_section(
                brief_path,
                markers=["## Constraints", "**Constraints**", "- Constraints:",
                         "## Tech Stack", "## Requirements"],
                max_chars=min(400, remaining),
            )
            if section:
                parts.append(section)
                remaining -= len(section)

        # TODO invariants
        todo_path = self._context_dir / "TODO.md"
        if todo_path.exists() and remaining > 100:
            section = self._extract_section(
                todo_path,
                markers=["## Invariants"],
                max_chars=min(300, remaining),
            )
            if section:
                parts.append(section)

        return "\n".join(parts)[:max_chars] if parts else ""


def _artifact_present_on_disk(work_dir: Path, location: str) -> bool:
    """Whether a CONTRACT artifact ``location`` exists on disk as a deliverable.

    A file counts if it exists and is non-empty. A directory counts ONLY if it
    holds at least one file — an empty scaffold dir is not a deliverable (task
    3c44a93f bug #1 synergy).

    The planner authors ``location`` FREELY, so it rarely matches the literal
    on-disk path (audit S0, cross-session 2026-06-30). We resolve it relative to
    the task's _work/ by stripping whichever workspace prefix it carries:
      - ``_work/redteam/recon.md``                    → ``redteam/recon.md``
      - ``workspace_data/tasks/<id>/_work/recon/``     → ``recon/`` (via _work split)
      - ``workspace_data/recon/``                      → ``recon/`` (deepseek run)
    If the resolved path still does not exist AND the location names a FILE, fall
    back to a basename match anywhere under _work/ — covers the planner declaring
    ``workspace_data/pln_redteam/phase1_recon.md`` while the agent wrote it flat at
    ``_work/phase1_recon.md`` (Opus 4.6 run). Without this, present=0 pinned the
    artifact-delivery cap at 10% even though real files existed.
    """
    loc = (location or "").strip().strip("`").replace("\\", "/")
    if not loc:
        return False
    # E-R4 (tri-run audit 2026-07-02): build_contract writes some locations as a
    # DIR + a parenthetical file-list, e.g.
    #   "_work/design_basis/ (design_basis.py, results.json, power_chain.csv)".
    # The trailing " (...)" defeated BOTH branches below (killed the dir-hint "/"
    # and turned the basename into "(design_basis.py, ...)"), pinning present=0 →
    # the artifact-delivery cap at 10% for a whole run. Strip a trailing
    # parenthetical so the remainder is the bare path the agent actually wrote to.
    _paren = re.match(r"^(.*?)\s*\([^()]*\)\s*$", loc)
    if _paren and _paren.group(1).strip():
        loc = _paren.group(1).strip()
    is_dir_hint = loc.endswith("/")
    # Strip whichever workspace prefix the planner used so the remainder is
    # relative to work_dir. Prefer the deepest ``_work/`` boundary; otherwise a
    # leading conventional ``workspace_data/`` segment.
    if "_work/" in loc:
        loc = loc.rsplit("_work/", 1)[1]
    elif loc.startswith("workspace_data/"):
        loc = loc.split("/", 1)[1]
    loc = loc.lstrip("/")
    candidate = work_dir / loc if loc else work_dir
    try:
        if candidate.is_file():
            return candidate.stat().st_size > 0
        if candidate.is_dir():
            return any(p.is_file() for p in candidate.rglob("*"))
        # Direct path missing. For a FILE location, the planner's directory may
        # not match where the agent actually wrote it — match by basename.
        if loc and not is_dir_hint:
            name = Path(loc).name
            if name:
                for match in work_dir.rglob(name):
                    if match.is_file() and match.stat().st_size > 0:
                        return True
        # DIR-hint location. Two very different shapes arrive here, and they must
        # be treated differently (R2-A / G2-4, tri-run round-2 2026-07-03):
        #
        #  (1) a made-up 1-SEGMENT GROUP name the planner invented (run 68c276ac:
        #      ``biomass_gasifier_design/``) while the agent flattened files into
        #      ``_work/`` root or nested a like-named dir elsewhere. The group is a
        #      LOGICAL label, not a real path, so we search by name and — only for
        #      this case — accept "``_work/`` holds ANY real deliverable" as present.
        #
        #  (2) a SPECIFIC MULTI-SEGMENT sub-path the CONTRACT declared explicitly
        #      (gasifier: ``_work/gasifier/02_cad/dxf/`` → ``gasifier/02_cad/dxf``).
        #      Here the path is authoritative: it exists or it does not. The global
        #      "any file under _work/" fallback must NOT apply — otherwise a task
        #      that produced ONE file makes EVERY declared sub-path read present
        #      (8/8 while 5 dirs were empty), which reads the delivery cap at 100%
        #      and disables the missing-artifact completion veto. That is exactly
        #      how the gasifier run "completed" at 13% with 5/8 deliverables absent.
        #
        # Distinguish structurally by segment count of the stripped ``loc``: a bare
        # group name is a single segment; a declared sub-path has 2+.
        elif loc and is_dir_hint:
            _stripped = loc.rstrip("/")
            _is_group_name = len(Path(_stripped).parts) <= 1
            name = Path(_stripped).name
            # A same-named dir holding a file counts for BOTH shapes (the agent may
            # have nested the declared leaf dir elsewhere).
            if name:
                for d in work_dir.rglob(name):
                    if d.is_dir() and any(p.is_file() for p in d.rglob("*")):
                        return True
            # The permissive "any deliverable under _work/" fallback is ONLY sound
            # for a 1-segment logical group — never for a specific declared sub-path.
            if _is_group_name and any(
                p.is_file() and p.stat().st_size > 0 for p in work_dir.rglob("*")
            ):
                return True
    except OSError:
        return False
    return False


def count_contract_artifacts(task_id: str) -> tuple[int, int]:
    """Count (declared, present-on-disk) CONTRACT artifacts for a task.

    Parses ``CONTRACT.md`` for ``- **Location**: ...`` lines (the artifact
    locations ``build_contract`` writes) and checks each against the task's
    _work/ on disk via ``_artifact_present_on_disk``. Ground-truth basis for the
    artifact-delivery progress cap (task 3c44a93f bug #4c). Returns ``(0, 0)``
    when no CONTRACT or no declared locations exist (→ no cap applies).
    """
    from app.core import task_paths

    contract_path = task_paths.context_file(task_id, "CONTRACT.md")
    if not contract_path.exists():
        return (0, 0)
    try:
        content = contract_path.read_text(encoding="utf-8")
    except Exception:
        return (0, 0)

    locations = re.findall(r"(?m)^- \*\*Location\*\*:\s*(.+?)\s*$", content)
    if not locations:
        return (0, 0)

    work_dir = task_paths.work_dir(task_id)
    present = sum(1 for loc in locations if _artifact_present_on_disk(work_dir, loc))
    return (len(locations), present)
