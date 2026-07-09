"""Budget-aware truncation & context allocation primitives (pure, no I/O).

Single home for "fit agent text / turn context into a char budget" so the engine
never blind-cuts an agent's conclusion. Positional slices (`text[:n]`) across the
orchestration files dropped the END of an agent's output — where completed-work
records and conclusions live — so the next turn lost finished work and redid it
(tri-model audit 2026-07-01). These helpers cut on word boundaries, can keep the
tail or both ends, parse the agent output schema, and allocate by semantic tier,
marking every elision so it is visibly recoverable via activity_search.

DOCTRINE: content destined for an agent's context window or a persistent
``_context/`` file (LEDGER, DECISIONS, TODO, EPOCHS, FINAL_REPORT, next_turn_focus)
MUST be fitted through these helpers — smart_truncate / keep_tail / keep_head_tail
/ allocate_tiered — NEVER a bare ``text[:N]``. A bare slice drops the tail, where
an agent's completed-work record and conclusions live. Safe ``[:N]`` stays allowed
for UI-preview / log / read-side prompt-budget only.

NOTE: distinct from ``app.core.context_budget.ContextBudgetAllocator``, which
allocates /chat ENRICHMENT sections (xskill/memory/kg) by priority+relevance.
This module is the ORCHESTRATION turn-context toolkit. Keep them separate.
"""

from __future__ import annotations

import re
from collections import OrderedDict

_ELLIPSIS = "…"


def smart_truncate(text: str, max_len: int, ellipsis: str = _ELLIPSIS) -> str:
    """Truncate to about ``max_len`` chars WITHOUT splitting a word.

    Trims at the last whitespace boundary at/under ``max_len`` and appends an
    ellipsis. Falls back to a hard cut only for a single unbroken token so length
    is always bounded. Returns the text unchanged when it already fits.
    """
    if not text:
        return text
    text = text.strip()
    if len(text) <= max_len:
        return text
    window = text[:max_len]
    cut = window.rstrip().rfind(" ")
    if cut <= 0:
        return window.rstrip() + ellipsis
    return window[:cut].rstrip() + ellipsis


def keep_tail(text: str, max_len: int, marker: bool = True) -> str:
    """Keep the END of ``text`` (for append-logs / outputs whose latest state is
    last). Starts the kept slice on a whole word and prepends an elision marker."""
    if not text:
        return text
    text = text.rstrip()
    if len(text) <= max_len:
        return text
    tail = text[-max_len:]
    first_space = tail.find(" ")
    if first_space > 0:
        tail = tail[first_space + 1:]
    return (f"{_ELLIPSIS} " if marker else "") + tail.lstrip()


def keep_head_tail(text: str, head: int, tail: int, marker: bool = True) -> str:
    """Keep both ends, drop the middle (for large tool output whose command and
    final result both matter). Marks the elided middle."""
    if not text:
        return text
    text = text.strip()
    if len(text) <= head + tail:
        return text
    _mid = (
        f"\n[… {len(text) - head - tail} chars elided — activity_search for full …]\n"
        if marker else ""
    )
    return text[:head].rstrip() + _mid + text[-tail:].lstrip()


# ── Output-section parsing (semantic preservation) ────────────────────────────
#
# The engine prompts every agent to structure its Output with named sections
# (agent_worker.py: "## ACCOMPLISHED / ## KEY FINDINGS / ## STILL MISSING").
# Preservation is therefore SEMANTIC, not positional: ACCOMPLISHED (done-work) is
# at the HEAD, STILL MISSING (remaining-work steer) at the TAIL — so neither
# "keep head" nor "keep tail" alone is right. We parse by heading and rank by the
# section's ROLE. EN + VI aliases are what actually appear in real ACTIVITY.md.
#
# priority: 0 = inviolable conclusion / done-work, 1 = inviolable remaining-work
# steer, 2 = elastic findings, 3 = elastic other.
_SECTION_ALIASES: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("accomplished", "summary", "conclusion", "final", "result",
      "báo cáo hoàn thành", "báo cáo"), "accomplished", 0),
    (("still missing", "next focus", "next step", "remaining",
      "còn thiếu", "chưa hoàn thành"), "still_missing", 1),
    (("key findings", "key data", "findings", "dữ liệu", "phát hiện"),
     "key_findings", 2),
)
# Vietnamese conclusion headings normalise to the canonical "conclusion" key
# (so extract_did_what can prefer them as done-work, priority 0).
_CANON_OVERRIDES: dict[str, str] = {
    "kết quả": "conclusion", "kết luận": "conclusion",
    "đã hoàn thành": "conclusion", "tóm lại": "conclusion",
}

SECTION_PRIORITY: tuple[tuple[str, int], ...] = (
    ("accomplished", 0), ("conclusion", 0),
    ("still_missing", 1), ("key_findings", 2), ("other", 3),
)

_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,4}\s*(?P<title>[^\n#][^\n]*?)\s*$")


def classify_section(title: str) -> tuple[str, int]:
    """Normalise a raw heading to (canonical_key, priority).

    Unknown headings return ("other", 3). Vietnamese conclusion headings map to
    the canonical "conclusion" key at priority 0 via _CANON_OVERRIDES.
    """
    t = (title or "").strip().lower().rstrip(":")
    if t in _CANON_OVERRIDES:
        return _CANON_OVERRIDES[t], 0
    for aliases, canon, prio in _SECTION_ALIASES:
        for a in aliases:
            if t.startswith(a):
                return canon, prio
    return "other", 3


def parse_output_sections(text: str) -> "OrderedDict[str, str]":
    """Split an agent Output into named sections keyed by canonical section key.

    Preamble before the first heading is stored under "". Unknown headings map to
    their canonical key ("other") via classify_section. Fail-open: no heading at
    all → the whole text under "" (never worse than returning it unchanged).
    Bodies of sections sharing a canonical key are concatenated in order.
    """
    out: "OrderedDict[str, str]" = OrderedDict()
    if not text:
        out[""] = ""
        return out
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        out[""] = text.strip()
        return out
    preamble = text[: matches[0].start()].strip()
    if preamble:
        out[""] = preamble
    for i, m in enumerate(matches):
        canon, _prio = classify_section(m.group("title"))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if canon in out:
            out[canon] += "\n" + body
        else:
            out[canon] = body
    return out


# ── Tiered allocation (conclusions whole, details elastic) ────────────────────

def allocate_tiered(fragments, budget: int) -> str:
    """Fit ``(priority, text, recovery_hint)`` fragments into ``budget`` chars.

    Conclusion-tier fragments (priority <= 1: done-work and remaining-work steer)
    are kept WHOLE first — never dropped or cut. Whatever budget remains is spent
    on lower tiers, smart_truncated, most-recent-first (input order = oldest →
    newest, so the remainder goes to the tail of the list). Anything elided
    contributes its recovery_hint to a single trailing pointer line, so nothing is
    silently lost — the caller supplies an ``activity_search(...)`` string.
    """
    kept: list[str] = []
    elided_hints: list[str] = []

    conclusions = [f for f in fragments if f[0] <= 1]
    details = [f for f in fragments if f[0] > 1]

    used = 0
    for _prio, text, _hint in conclusions:
        text = (text or "").strip()
        if not text:
            continue
        kept.append(text)
        used += len(text) + 1

    remaining = max(0, budget - used)
    # Spend the remainder on details newest-first (end of list = newest).
    for _prio, text, hint in reversed(details):
        text = (text or "").strip()
        if not text:
            continue
        if remaining <= 40:  # not enough to say anything meaningful
            if hint:
                elided_hints.append(hint)
            continue
        piece = smart_truncate(text, min(len(text), remaining))
        kept.append(piece)
        remaining -= len(piece) + 1
        if len(piece) < len(text) and hint:
            elided_hints.append(hint)

    if elided_hints:
        kept.append(f"[elided detail — recover via: {'; '.join(elided_hints[:5])}]")
    return "\n".join(kept)
