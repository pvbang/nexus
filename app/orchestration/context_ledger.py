"""
Nexus - Progressive Context Ledger.

Structured, append-only ledger that records WHAT each agent accomplished per turn.
Unlike ACTIVITY.md (full fidelity log), the ledger stores CONCLUSIONS only —
compact structured entries that agents and orchestrator can query O(1).

Architecture:
- LEDGER.jsonl: 1 line per agent per turn (structured conclusion)
- EPOCHS.jsonl: auto-generated summaries every N turns (hierarchical compression)
- Query API: get entries by role, turn range, topic — no full-file parse needed

This solves the "amnesia" problem: agents can always reconstruct what was done
without relying on flat text budgets that get cut mid-sentence.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
_EPOCH_SIZE = 10  # Compress every 10 turns into one epoch entry

# TWO-TIER priority (X-1, arch review 2026-07-03). Originally one marker set floated
# "handoff" facts to the front. Then W-4 added result-numbers to that SAME set — but
# in a number-heavy task (gasifier) the numbers then crowded out the ❌ error-handoff,
# re-introducing the very C-1/C-2 loss the prioritizer fixed. So split into tiers:
#
#   TIER 1 — ERROR/UNFINISHED handoff: the thing the next agent MUST act on (an error
#   location, unfinished status, a blocker). Agents write these LAST, after their ✅
#   achievements, so a naive [:N] slice dropped them (C-1/C-2: Turn 6 re-searched a
#   line number Turn 5 had named).
_ERROR_HANDOFF_RE = re.compile(
    r"❌|✗|\bCHƯA\b|\bchưa\b|còn nợ|\bpending\b|\bmust[- ]?fix\b|\bmust\b|\bTODO\b|"
    r"\bblocker\b|\bcrash\w*\b|\bKeyError\b|\b[A-Za-z]*Error\b|\bFAIL\w*\b|"
    r"\bdòng\s*\d+|\bline\s*\d+|\bstill\b|\bunresolved\b|\bnot\s+(?:done|run|fixed|saved)\b",
    re.IGNORECASE,
)
#   TIER 2 — RESULT NUMBER: a computed value the next agent needs (CGE=62.93%,
#   closure 4.2%, 20 kW, 115.8 mm). Worth keeping, but never ahead of an error handoff.
_RESULT_NUMBER_RE = re.compile(
    r"[A-Za-z_)\]%]\s*[=:]\s*-?\d|"
    r"\d[\d.,]*\s*(?:%|kW|kWe|kWh|MW|MJ|kJ|GJ|mm|cm|°C|Nm3|kg|USD|VND|MPa|bar|Hz|V|A|"
    r"req|reqs|services?|items?)\b",
    re.IGNORECASE,
)
# Back-compat alias: some callers/tests probe the combined "is this a handoff fact?".
_HANDOFF_MARKER_RE = re.compile(
    _ERROR_HANDOFF_RE.pattern + "|" + _RESULT_NUMBER_RE.pattern, re.IGNORECASE
)


def prioritize_handoff_key_data(key_data: list[str] | None, limit: int) -> list[str]:
    """Return up to ``limit`` key_data items in 2-tier priority order.

    TIER 1 error/unfinished handoff → TIER 2 result numbers → everything else.
    Stable within each tier. This keeps "❌ CHƯA sửa main() dòng 405" ahead of a wall
    of result numbers (X-1), while still surfacing numbers ahead of prose (W-4).
    Pure.
    """
    if not key_data:
        return []
    tier1 = [kd for kd in key_data if _ERROR_HANDOFF_RE.search(kd)]
    tier2 = [kd for kd in key_data
             if kd not in tier1 and _RESULT_NUMBER_RE.search(kd)]
    rest = [kd for kd in key_data if kd not in tier1 and kd not in tier2]
    return (tier1 + tier2 + rest)[:limit]


@dataclass
class LedgerEntry:
    """One agent's conclusion for one turn — the atomic unit of context."""

    turn: int
    agent_role: str
    agent_name: str
    did_what: str  # 1-2 sentence summary of accomplishment
    key_data: list[str] = field(default_factory=list)  # Critical data points found
    tools_used: list[str] = field(default_factory=list)
    success: bool = True
    # Ground-truth "work reached disk this turn?" (E3). Distinct from `success`:
    # a prose-only analysis/wrap-up turn is success=True but produced nothing. The
    # loop gate (E7) counts consecutive turns with NO production to break the
    # "talk, never build" pathology that pinned runs 68c276ac/1894f0ef.
    produced_deliverable: bool = False
    output_chars: int = 0  # Size of full output (for budget estimation)
    blockers: str = ""  # What prevented completion (if any)
    depends_on: list[str] = field(default_factory=list)  # Which prior entries this built on
    truncated_topics: list[str] = field(default_factory=list)  # Topics with truncated data (search hints)


@dataclass
class EpochSummary:
    """Compressed summary of N turns — hierarchical compression layer."""

    epoch: int
    turns_range: str  # e.g. "1-10"
    summary: str  # Overall accomplishment across these turns
    key_data: list[str] = field(default_factory=list)  # Accumulated key findings
    roles_active: list[str] = field(default_factory=list)
    progress_trajectory: str = ""  # e.g. "10% → 45%"


class ContextLedger:
    """Progressive Context Ledger — structured context for 100+ turn tasks.

    Append-only. Never truncates. Supports efficient queries.
    Each task gets its own LEDGER.jsonl and EPOCHS.jsonl files.

    Performance: maintains an in-memory cache of parsed entries.
    Cache is append-only (mirrors file semantics) — new entries are appended
    to both file and cache simultaneously, so queries never re-read the file
    unless the cache is empty (cold start).
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._context_dir = task_paths.context_dir(task_id)
        self._ledger_path = self._context_dir / "LEDGER.jsonl"
        self._epochs_path = self._context_dir / "EPOCHS.jsonl"
        # In-memory cache: avoids re-reading JSONL file on every query
        self._entries_cache: list[LedgerEntry] | None = None
        self._epochs_cache: list[EpochSummary] | None = None
        # FIX E3: Indexed lookups for O(1) by turn and by role
        self._index_by_turn: dict[int, list[int]] = {}  # turn → list of cache indices
        self._index_by_role: dict[str, list[int]] = {}  # role → list of cache indices

    def _index_entry(self, entry: LedgerEntry, idx: int) -> None:
        """FIX E3: Update indexes when an entry is added to cache."""
        self._index_by_turn.setdefault(entry.turn, []).append(idx)
        self._index_by_role.setdefault(entry.agent_role, []).append(idx)

    @staticmethod
    def _scrub_entry(entry: LedgerEntry) -> LedgerEntry:
        """Redact secrets from an entry's free-text fields before it is persisted.

        LEDGER.jsonl is durable; `did_what`/`key_data`/`blockers` carry agent
        output that can include dumped credentials (audit CC-6). Scrubbing here —
        at the write boundary — keeps them off disk where post-hoc redaction
        cannot reach. Mutates and returns the same entry (cache stores the
        scrubbed version too, so it never re-surfaces credentials downstream).
        """
        try:
            from app.core.secret_redaction import redact_secrets
            entry.did_what = redact_secrets(entry.did_what or "")
            entry.blockers = redact_secrets(entry.blockers or "")
            if entry.key_data:
                entry.key_data = [redact_secrets(k or "") for k in entry.key_data]
        except Exception:
            pass
        return entry

    def append(self, entry: LedgerEntry) -> None:
        """Append a single ledger entry (1 agent, 1 turn)."""
        entry = self._scrub_entry(entry)
        line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
        with open(self._ledger_path, "a", encoding="utf-8") as f:
            f.write(line)
        if self._entries_cache is not None:
            idx = len(self._entries_cache)
            self._entries_cache.append(entry)
            self._index_entry(entry, idx)

    def append_batch(self, entries: list[LedgerEntry]) -> None:
        """Append multiple entries at once (all agents in a turn)."""
        entries = [self._scrub_entry(e) for e in entries]
        lines = [json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in entries]
        with open(self._ledger_path, "a", encoding="utf-8") as f:
            f.writelines(lines)
        if self._entries_cache is not None:
            base_idx = len(self._entries_cache)
            self._entries_cache.extend(entries)
            for i, e in enumerate(entries):
                self._index_entry(e, base_idx + i)

    @staticmethod
    def _merge_into(draft: LedgerEntry, final: LedgerEntry) -> None:
        """Upgrade ``draft`` in place with the authoritative ``final`` conclusion.

        The end-of-turn write carries the better ``did_what`` (orchestrator notes
        or a summary-aware extraction) and the truncation manifest, while the
        interim draft may hold key_data found earlier in the turn. Take ``final``'s
        free-text/manifest, but UNION key_data + tools_used so no mid-turn fact or
        tool record is lost when the draft is replaced.
        """
        draft.agent_name = final.agent_name or draft.agent_name
        # W-3 (context-arch audit): the final did_what (orchestrator eval note) used
        # to overwrite the draft UNCONDITIONALLY. When the note is terse ("Ran the
        # script.") it erased the agent's own richer conclusion + self-computed
        # numbers extracted from the output. Keep the richer text: overwrite only
        # when the final is not materially shorter; otherwise fold the note in front
        # of the draft so both the orchestrator's framing AND the agent's numbers
        # survive.
        if final.did_what:
            _f, _d = final.did_what.strip(), (draft.did_what or "").strip()
            if not _d or len(_f) >= len(_d) * 0.8:
                draft.did_what = _f
            elif _f and _f.lower() not in _d.lower():
                merged = f"{_f} — {_d}"
                draft.did_what = merged if len(merged) <= 1200 else _d
            # else: draft already contains the note's substance → keep the richer draft
        draft.success = final.success
        # E3: OR the ground-truth production signal — a produce that landed in
        # either the interim or the final write must survive the merge (never
        # downgrade a real production to False).
        draft.produced_deliverable = draft.produced_deliverable or final.produced_deliverable
        draft.blockers = final.blockers or draft.blockers
        draft.output_chars = final.output_chars or draft.output_chars
        if final.depends_on:
            draft.depends_on = final.depends_on
        if final.truncated_topics:
            draft.truncated_topics = final.truncated_topics
        # Union — preserve interim findings, append new ones in order, dedup.
        draft.key_data = list(dict.fromkeys([*draft.key_data, *final.key_data]))
        draft.tools_used = list(dict.fromkeys([*draft.tools_used, *final.tools_used]))

    def upsert_batch(self, entries: list[LedgerEntry]) -> None:
        """Append entries, but UPGRADE any existing draft for the same (turn, role).

        The turn executor writes an interim draft per agent mid-turn; this is the
        end-of-turn authoritative write. The ledger must hold ONE record per
        (turn, agent_role) — appending here would duplicate every agent, and the
        prior code's "skip if a draft exists" silently discarded the better
        conclusion. So: if a draft for (turn, role) exists, merge into it and
        rewrite the file; otherwise append normally.

        ``_discussion_synthesis`` is exempt — multiple synthesis entries per turn
        are legitimate, so those always append.
        """
        all_entries = self._load_cache()
        to_append: list[LedgerEntry] = []
        dirty = False

        for final in entries:
            target_idx = -1
            if final.agent_role != "_discussion_synthesis":
                for idx in self._index_by_turn.get(final.turn, []):
                    if all_entries[idx].agent_role == final.agent_role:
                        target_idx = idx
                        break
            if target_idx >= 0:
                self._merge_into(all_entries[target_idx], self._scrub_entry(final))
                dirty = True
            else:
                to_append.append(final)

        if to_append:
            self.append_batch(to_append)  # append_batch scrubs + indexes + writes
        if dirty:
            self._rewrite_file()

    def _rewrite_file(self) -> None:
        """Rewrite LEDGER.jsonl from the in-memory cache (used after in-place upgrade).

        Append-only is the norm; an in-place upgrade is the one case that needs a
        full rewrite. Cache is the source of truth here — it already reflects the
        merge. Written atomically (temp + replace) so a crash mid-write can't
        truncate the durable ledger.
        """
        entries = self._entries_cache or []
        tmp = self._ledger_path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
            os.replace(tmp, self._ledger_path)
        except Exception as e:
            logger.warning("ledger_rewrite_failed", error=str(e))

    def _load_cache(self) -> list[LedgerEntry]:
        """Load all entries into cache from file (cold start only).

        FIX E3: Also builds turn/role indexes for O(1) lookups.
        """
        if self._entries_cache is not None:
            return self._entries_cache

        self._entries_cache = []
        self._index_by_turn = {}
        self._index_by_role = {}
        if not self._ledger_path.exists():
            return self._entries_cache

        _fields = LedgerEntry.__dataclass_fields__
        try:
            with open(self._ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = LedgerEntry(**{
                            k: v for k, v in data.items() if k in _fields
                        })
                        idx = len(self._entries_cache)
                        self._entries_cache.append(entry)
                        self._index_entry(entry, idx)
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as e:
            logger.warning("ledger_cache_load_failed", error=str(e))

        return self._entries_cache

    def query(
        self,
        *,
        role: str = "",
        since_turn: int = 0,
        until_turn: int = 999999,
        success_only: bool = False,
        limit: int = 50,
    ) -> list[LedgerEntry]:
        """Query ledger entries by criteria. Returns most recent first.

        FIX E3: Uses indexed lookups when filtering by role or specific turn.
        Falls back to linear scan for complex range queries.
        """
        all_entries = self._load_cache()
        results: list[LedgerEntry] = []

        # FIX E3: Use index for role-only queries (most common pattern)
        if role and since_turn == 0 and until_turn == 999999:
            indices = self._index_by_role.get(role, [])
            for idx in reversed(indices):
                entry = all_entries[idx]
                if success_only and not entry.success:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

        # FIX E3: Use index for single-turn queries
        if since_turn > 0 and since_turn == until_turn:
            indices = self._index_by_turn.get(since_turn, [])
            for idx in reversed(indices):
                entry = all_entries[idx]
                if role and entry.agent_role != role:
                    continue
                if success_only and not entry.success:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return results

        # Fallback: linear scan for range queries
        for entry in reversed(all_entries):
            if entry.turn < since_turn:
                if since_turn > 1:
                    break
                continue
            if entry.turn > until_turn:
                continue
            if role and entry.agent_role != role:
                continue
            if success_only and not entry.success:
                continue

            results.append(entry)
            if len(results) >= limit:
                break

        return results

    # Noise patterns that should never appear in key_data output
    _KEY_DATA_NOISE = (
        "TLS", "handshake", "cipher", "CAfile", "CApath",
        "Trying ", "Connected to", "ALPN:", "shutting down",
        "rw-", "rwx", "drw", "total ", "alembic", "pyproject",
        "entrypoint.sh", "__pycache__", ".pyc",
        "webhook.site", "token", "#!/edit/",
    )
    @classmethod
    def _is_key_data_noise(cls, kd: str) -> bool:
        """Whether a key_data item is pure noise (TLS chatter, ls headers, ...).

        A result-number fact is never noise (W-8) — the number is the signal. Only
        when there is NO such number do the substring noise tokens apply. Uses the
        module-level _RESULT_NUMBER_RE (single source; X-5 dedup — there used to be a
        near-duplicate class copy that could drift).
        """
        if not kd:
            return True
        if _RESULT_NUMBER_RE.search(kd):
            return False
        return any(n in kd for n in cls._KEY_DATA_NOISE)

    def query_key_data(
        self,
        *,
        since_turn: int = 0,
        roles: list[str] | None = None,
        limit: int = 100,
    ) -> list[str]:
        """Get all key_data points across ALL entries. Deduped, filtered, most recent first.

        Filters out noise (TLS debug, file permissions, webhook tokens, internal paths)
        that polluted earlier extractions.
        """
        seen: set[str] = set()
        result: list[str] = []
        all_entries = self._load_cache()

        for entry in reversed(all_entries):
            if entry.turn < since_turn:
                continue
            if roles and entry.agent_role not in roles:
                continue
            for kd in entry.key_data:
                if kd in seen:
                    continue
                # Filter noise at query time (fixes historical polluted entries).
                # W-8: result-number facts survive even if they contain a noise word.
                if self._is_key_data_noise(kd):
                    continue
                if len(kd) < 15:
                    continue
                seen.add(kd)
                result.append(kd)
                if len(result) >= limit:
                    return result
        return result

    def get_turn_summary(self, turn: int) -> str:
        """Get human-readable summary for a specific turn."""
        entries = self.query(since_turn=turn, until_turn=turn)
        if not entries:
            return f"(Turn {turn}: no ledger entries)"

        parts: list[str] = []
        for e in entries:
            status = "✓" if e.success else "✗"
            parts.append(f"  {status} [{e.agent_role}] {e.did_what}")
            if e.key_data:
                for kd in e.key_data[:3]:
                    parts.append(f"    → {kd}")
        return f"Turn {turn}:\n" + "\n".join(parts)

    def build_context_for_agent(
        self,
        agent_role: str,
        current_turn: int,
        max_chars: int = 8000,
    ) -> str:
        """Build optimized context for a specific agent.

        Strategy (Recall Before Action):
        - Own prior work: full entries (most relevant, prevents redo)
        - Team agreements: ALL discussions (never expire, critical for coherence)
        - Teammates' work: key_data only (cross-role awareness)
        - Epochs for older history — dynamic window scales with task length
        - Truncation manifest: what was cut, keywords to search

        Returns text ready to inject into agent prompt.
        """
        parts: list[str] = []
        remaining = max_chars

        # Section 1: Own prior work (full detail, recent first)
        # This is the PRIMARY recall mechanism — agent sees everything it did before
        own_entries = self.query(role=agent_role, until_turn=current_turn - 1, limit=15)
        if own_entries:
            section = "## Your Prior Work (DO NOT redo — continue from here)\n"
            section += "**RECALL:** Before acting, check if any item below already covers your planned action.\n"
            _shown = 0
            for e in own_entries:
                line = f"- T{e.turn}: {e.did_what}"
                if e.key_data:
                    line += f" | Data: {'; '.join(e.key_data[:5])}"
                if e.blockers:
                    line += f" | ⚠️ Blocked: {e.blockers}"
                if e.truncated_topics:
                    line += f" | 🔍 Full data: activity_search({', '.join(e.truncated_topics[:3])})"
                line += "\n"
                if len(section) + len(line) > remaining * 0.4:
                    break
                section += line
                _shown += 1
            # R-3 (context-arch audit): a bare break silently hid the rest of your own
            # work when the budget ran out — the agent then couldn't tell it had more
            # history or how to get it. Tell it what was cut + how to recall.
            if _shown < len(own_entries):
                _hidden = len(own_entries) - _shown
                _earliest = own_entries[_shown].turn if _shown < len(own_entries) else 1
                section += (
                    f"[{_hidden} earlier of YOUR turns hidden for space — "
                    f"activity_search(turn_from=1, turn_to={_earliest}) to recall them]\n"
                )
            parts.append(section)
            remaining -= len(section)

        # Section 2: Team Agreements — query ALL turns (never expire)
        # These represent collective decisions that MUST persist across the entire task.
        all_consensus = self.query(
            role="_discussion_synthesis",
            until_turn=current_turn - 1,
            limit=20,
        )
        if all_consensus and remaining > 400:
            section = "## Team Agreements (BINDING — do not contradict)\n"
            for e in all_consensus:
                line = f"- T{e.turn}: {e.did_what}\n"
                if len(section) + len(line) > remaining * 0.2:
                    section += f"[{len(all_consensus) - len(section.split(chr(10))) + 2} older agreements — use activity_search(keyword='Discussion Summary')]\n"
                    break
                section += line
            parts.append(section)
            remaining -= len(section)

        # Section 3: Teammates' recent work (key_data focused)
        all_recent = self.query(
            since_turn=max(1, current_turn - 5),
            until_turn=current_turn - 1,
            limit=30,
        )
        teammate_entries = [
            e for e in all_recent
            if e.agent_role != agent_role and e.agent_role != "_discussion_synthesis"
        ]
        if teammate_entries and remaining > 500:
            section = "## Teammates' Results (build on this, do NOT redo)\n"
            for e in teammate_entries:
                line = f"- T{e.turn} [{e.agent_role}]: {e.did_what}"
                if e.key_data:
                    # C-1/C-2 (context-continuity audit 2026-07-03): filter noise
                    # FIRST, then float action/handoff facts to the front, THEN take
                    # a wider slice. The old ``key_data[:3]`` sliced before filtering
                    # and kept the author's order — so it delivered three "✅ done"
                    # facts and dropped "❌ CHƯA sửa main() dòng 405", the one thing
                    # the next agent needed. blockers carry the same info explicitly.
                    _clean = [
                        kd for kd in e.key_data
                        if not self._is_key_data_noise(kd) and len(kd) >= 15
                    ]
                    clean_kd = prioritize_handoff_key_data(_clean, limit=8)
                    if clean_kd:
                        line += f" → {'; '.join(clean_kd)}"
                if e.blockers:
                    # X-6 (arch review): blockers now carries the ≤600-char
                    # still-missing handoff. Full length here bloated EACH teammate
                    # line, so later teammates got break-dropped (their did_what
                    # lost). Cap the cross-role view to the essence — the owner still
                    # sees it in full under "Your Prior Work".
                    from app.orchestration.text_budget import smart_truncate
                    line += f" | ⚠️ Còn nợ: {smart_truncate(e.blockers, 200)}"
                line += "\n"
                if len(section) + len(line) > remaining * 0.7:
                    break
                section += line
            parts.append(section)
            remaining -= len(section)

        # Section 4: Accumulated key_data across ALL turns (deduped, never lost)
        # This ensures critical findings from any turn remain visible.
        # IMPORTANT: Include source attribution so agents don't claim credit
        # for findings they didn't produce.
        all_key_data = self.query_key_data(
            since_turn=1,
            limit=25,
        )
        # Filter out data already shown in own_entries or teammate_entries
        shown_data: set[str] = set()
        for e in (own_entries or []):
            shown_data.update(e.key_data)
        for e in teammate_entries:
            shown_data.update(e.key_data)
        new_key_data = [kd for kd in all_key_data if kd not in shown_data]
        if new_key_data and remaining > 300:
            section = "## Key Findings From Other Agents (READ-ONLY — do NOT claim as your own work)\n"
            for kd in new_key_data[:15]:
                line = f"- {kd}\n"
                if len(section) + len(line) > remaining * 0.5:
                    break
                section += line
            parts.append(section)
            remaining -= len(section)

        # Section 5: Epoch summaries — dynamic window scales with task length
        if current_turn > _EPOCH_SIZE and remaining > 500:
            epochs = self._read_epochs()
            if epochs:
                epoch_window = max(3, min(len(epochs), current_turn // _EPOCH_SIZE))
                section = "## Earlier Work (epochs)\n"
                for ep in epochs[-epoch_window:]:
                    line = f"- Turns {ep.turns_range}: {ep.summary}"
                    if ep.key_data:
                        line += f" | Key: {'; '.join(ep.key_data[:5])}"
                    line += "\n"
                    if len(section) + len(line) > remaining:
                        break
                    section += line
                if len(epochs) > epoch_window:
                    hidden_count = len(epochs) - epoch_window
                    earliest = epochs[0].turns_range if epochs else "?"
                    section += (
                        f"[{hidden_count} older epochs hidden (turns {earliest}...) "
                        f"— use activity_search(turn_from=1, turn_to={epochs[-epoch_window].epoch * _EPOCH_SIZE}) to retrieve]\n"
                    )
                parts.append(section)

        return "\n".join(parts) if parts else ""

    def build_context_for_orchestrator(
        self,
        current_turn: int,
        max_chars: int = 15000,
    ) -> str:
        """Build comprehensive context for orchestrator evaluation/planning.

        More detailed than agent context — orchestrator needs full picture
        to make correct progress/completion decisions.
        Dynamic epoch window scales with task length.
        """
        parts: list[str] = []
        remaining = max_chars

        # All entries for recent turns (full detail)
        recent_start = max(1, current_turn - 5)
        recent = self.query(since_turn=recent_start, until_turn=current_turn - 1, limit=50)

        if recent:
            section = "## Recent Turn Results (Ledger)\n"
            current_group_turn = -1
            for e in sorted(recent, key=lambda x: x.turn):
                if e.turn != current_group_turn:
                    current_group_turn = e.turn
                    section += f"\n### Turn {e.turn}\n"
                status = "✓" if e.success else "✗"
                line = f"- {status} **{e.agent_name}** ({e.agent_role}): {e.did_what}\n"
                if e.key_data:
                    line += f"  Key data: {'; '.join(e.key_data[:5])}\n"
                if e.blockers:
                    line += f"  ⚠️ Blocker: {e.blockers}\n"
                if len(section) + len(line) > remaining * 0.7:
                    break
                section += line
            parts.append(section)
            remaining -= len(section)

        # Accumulated key data (deduped, ALL turns via scalable query)
        all_key_data = self.query_key_data(limit=50)
        if all_key_data and remaining > 500:
            section = "\n## Accumulated Key Findings\n"
            for kd in all_key_data[:30]:
                line = f"- {kd}\n"
                if len(section) + len(line) > remaining:
                    break
                section += line
            parts.append(section)
            remaining -= len(section)

        # Epoch summaries — dynamic window (show ALL within budget)
        epochs = self._read_epochs()
        if epochs and remaining > 300:
            section = "\n## Epoch History\n"
            # Orchestrator sees ALL epochs that fit in budget (not just last 5)
            epoch_window = max(5, min(len(epochs), current_turn // _EPOCH_SIZE))
            for ep in epochs[-epoch_window:]:
                line = f"- **Turns {ep.turns_range}** ({ep.progress_trajectory}): {ep.summary}"
                if ep.key_data:
                    line += f" | Key: {'; '.join(ep.key_data[:3])}"
                line += "\n"
                if len(section) + len(line) > remaining:
                    break
                section += line
            if len(epochs) > epoch_window:
                hidden = len(epochs) - epoch_window
                section += f"[{hidden} older epochs omitted — total task history spans turns 1-{epochs[-1].epoch * _EPOCH_SIZE}]\n"
            parts.append(section)

        return "\n".join(parts) if parts else ""

    def query_for_tool(self, query: str, max_chars: int = 4000) -> str:
        """Handle agent tool call to recall context from ledger."""
        import re
        query_lower = query.lower().strip()
        results: list[str] = []

        turn_match = re.search(r'turn\s*(\d+)', query_lower)
        role_match = re.search(
            r'(researcher|developer|analyst|writer|pentester|coordinator|critic|judge|core)',
            query_lower,
        )

        if turn_match:
            turn_num = int(turn_match.group(1))
            entries = self.query(since_turn=turn_num, until_turn=turn_num, limit=20)
            if role_match:
                entries = [e for e in entries if role_match.group(1) in e.agent_role.lower()]
            for e in entries:
                line = f"T{e.turn} [{e.agent_role}]: {e.did_what}"
                if e.key_data:
                    line += f"\n  Data: {'; '.join(e.key_data)}"
                if e.blockers:
                    line += f"\n  Blocker: {e.blockers}"
                results.append(line)
        elif "key findings" in query_lower or "key data" in query_lower:
            kd = self.query_key_data(limit=30)
            results = kd
        elif role_match:
            entries = self.query(role=role_match.group(1), limit=15)
            for e in entries:
                line = f"T{e.turn}: {e.did_what}"
                if e.key_data:
                    line += f" → {'; '.join(e.key_data[:3])}"
                results.append(line)
        else:
            all_entries = self._load_cache()
            for entry in reversed(all_entries):
                match_text = f"{entry.did_what} {' '.join(entry.key_data)}".lower()
                if any(word in match_text for word in query_lower.split() if len(word) > 3):
                    line = f"T{entry.turn} [{entry.agent_role}]: {entry.did_what}"
                    if entry.key_data:
                        line += f"\n  → {'; '.join(entry.key_data[:3])}"
                    results.append(line)
                    if len(results) >= 15:
                        break

        if not results:
            return f"No ledger entries found for query: {query}"

        output = "\n".join(results)
        if len(output) > max_chars:
            output = output[:max_chars] + "\n[...truncated]"
        return output

    def maybe_compress_epoch(self, completed_turn: int) -> Optional[EpochSummary]:
        """Check if it's time to compress an epoch. Called after each turn.

        Returns the new epoch if one was created, None otherwise.
        Uses heuristic-based summarization (fast, synchronous).
        For LLM-enhanced quality, call maybe_compress_epoch_with_llm() instead.
        """
        if completed_turn % _EPOCH_SIZE != 0:
            return None
        if completed_turn < _EPOCH_SIZE:
            return None

        epoch_num = completed_turn // _EPOCH_SIZE
        start_turn = (epoch_num - 1) * _EPOCH_SIZE + 1
        end_turn = epoch_num * _EPOCH_SIZE

        # Check if this epoch already exists
        existing = self._read_epochs()
        if any(ep.epoch == epoch_num for ep in existing):
            return None

        entries = self.query(since_turn=start_turn, until_turn=end_turn, limit=200)
        if not entries:
            return None

        summary, all_key_data, roles = self._build_epoch_from_entries(entries)

        epoch = EpochSummary(
            epoch=epoch_num,
            turns_range=f"{start_turn}-{end_turn}",
            summary=summary,
            key_data=all_key_data,
            roles_active=roles,
        )

        self._persist_epoch(epoch, len(entries))
        return epoch

    async def maybe_compress_epoch_with_llm(
        self, completed_turn: int, llm: Any = None
    ) -> Optional[EpochSummary]:
        """GAP-2 FIX: LLM-enhanced epoch compression for higher quality summaries.

        Uses LLM to produce a concise, semantically rich summary instead of
        mechanical concatenation. Falls back to heuristic if LLM unavailable/fails.
        """
        if completed_turn % _EPOCH_SIZE != 0:
            return None
        if completed_turn < _EPOCH_SIZE:
            return None

        epoch_num = completed_turn // _EPOCH_SIZE
        start_turn = (epoch_num - 1) * _EPOCH_SIZE + 1
        end_turn = epoch_num * _EPOCH_SIZE

        existing = self._read_epochs()
        if any(ep.epoch == epoch_num for ep in existing):
            return None

        entries = self.query(since_turn=start_turn, until_turn=end_turn, limit=200)
        if not entries:
            return None

        roles = list(set(e.agent_role for e in entries if e.agent_role != "_discussion_synthesis"))
        all_key_data: list[str] = []
        for e in entries:
            all_key_data.extend(e.key_data)
        all_key_data = list(dict.fromkeys(all_key_data))[:50]

        # Try LLM summarization
        summary = ""
        if llm:
            try:
                from app.orchestration.text_budget import keep_head_tail
                _joined = "\n".join(
                    f"T{e.turn} [{e.agent_role}] {'✓' if e.success else '✗'}: {e.did_what}"
                    for e in entries if e.did_what
                )
                # W-5 (context-arch audit): a bare [:6000] cut the TAIL — i.e. the
                # NEWEST turns of the epoch (e.g. turns 9-10 of a 1-10 epoch), the
                # ones most relevant to what comes next. Keep both ends so the epoch
                # summary sees the epoch's start AND its latest state.
                entry_text = keep_head_tail(_joined, head=3000, tail=3000)
                messages = [
                    {"role": "system", "content": (
                        "Summarize what was accomplished in these turns. "
                        "Focus on: key achievements, important findings/data, "
                        "decisions made, and blockers encountered. "
                        "Be concise (max 600 words). Use structured format with bullet points."
                    )},
                    {"role": "user", "content": f"Turns {start_turn}-{end_turn}:\n{entry_text}"},
                ]
                result = await llm.chat(messages=messages, temperature=0.2, max_tokens=1024)
                summary = (result.get("content", "") or "").strip()
            except Exception as e:
                logger.debug("epoch_llm_summarization_failed", error=str(e)[:100])

        # Fallback to heuristic if LLM failed
        if not summary:
            summary, all_key_data, roles = self._build_epoch_from_entries(entries)

        epoch = EpochSummary(
            epoch=epoch_num,
            turns_range=f"{start_turn}-{end_turn}",
            summary=summary,
            key_data=all_key_data,
            roles_active=roles,
        )

        self._persist_epoch(epoch, len(entries))
        return epoch

    def _build_epoch_from_entries(
        self, entries: list[LedgerEntry]
    ) -> tuple[str, list[str], list[str]]:
        """Heuristic epoch builder — shared between sync and async paths."""
        roles = list(set(e.agent_role for e in entries if e.agent_role != "_discussion_synthesis"))
        all_key_data: list[str] = []
        for e in entries:
            all_key_data.extend(e.key_data)
        all_key_data = list(dict.fromkeys(all_key_data))[:50]

        role_summaries: dict[str, list[str]] = {}
        for e in entries:
            if e.success and e.did_what and e.agent_role != "_discussion_synthesis":
                role_summaries.setdefault(e.agent_role, []).append(e.did_what)

        did_parts = []
        for role, actions in role_summaries.items():
            unique_actions = list(dict.fromkeys(actions))[:8]
            did_parts.append(f"[{role}]: {'; '.join(unique_actions)}")

        agreements = [e.did_what for e in entries if e.agent_role == "_discussion_synthesis" and e.did_what]
        if agreements:
            did_parts.append(f"[agreements]: {'; '.join(agreements[:3])}")

        summary = " | ".join(did_parts)
        if len(summary) > 4000:
            # word-boundary, not a hard mid-word cut of the persisted epoch summary
            # that could drop the last role's contribution (audit 2026-07-01, M1).
            from app.orchestration.text_budget import smart_truncate
            summary = smart_truncate(summary, 4000)

        return summary, all_key_data, roles

    def _persist_epoch(self, epoch: EpochSummary, entries_count: int) -> None:
        """Write epoch to file and cache."""
        # CC-6 defense-in-depth: an LLM-compressed epoch summary is written durably
        # to EPOCHS.jsonl. Inputs are already scrubbed at append time, but scrub the
        # summary/key_data here too so this durable boundary is consistent.
        try:
            from app.core.secret_redaction import redact_secrets
            epoch.summary = redact_secrets(epoch.summary or "")
            if epoch.key_data:
                epoch.key_data = [redact_secrets(k or "") for k in epoch.key_data]
        except Exception:
            pass
        line = json.dumps(asdict(epoch), ensure_ascii=False) + "\n"
        with open(self._epochs_path, "a", encoding="utf-8") as f:
            f.write(line)
        if self._epochs_cache is not None:
            self._epochs_cache.append(epoch)

        logger.info(
            "epoch_compressed",
            epoch=epoch.epoch,
            turns=epoch.turns_range,
            entries_count=entries_count,
        )

    def _read_epochs(self) -> list[EpochSummary]:
        """Read all epoch summaries (cached after first read)."""
        if self._epochs_cache is not None:
            return self._epochs_cache

        self._epochs_cache = []
        if not self._epochs_path.exists():
            return self._epochs_cache

        try:
            with open(self._epochs_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self._epochs_cache.append(EpochSummary(**{
                            k: v for k, v in data.items()
                            if k in EpochSummary.__dataclass_fields__
                        }))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception:
            pass
        return self._epochs_cache

    def get_entry_count(self) -> int:
        """Get total number of ledger entries (from cache if available)."""
        if self._entries_cache is not None:
            return len(self._entries_cache)
        if not self._ledger_path.exists():
            return 0
        try:
            with open(self._ledger_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def get_all_entries(self) -> list[dict[str, Any]]:
        """Get all ledger entries as raw dicts (for deduplication checks).

        Returns list of dicts with keys: turn, agent_role, did_what, key_data, etc.
        Uses in-memory cache — no file I/O after cold start.
        """
        entries = self._load_cache()
        return [asdict(e) for e in entries]
