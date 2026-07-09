# Section-Aware Tiered Context Allocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `text[:N]` truncation from silently dropping agent conclusions / completed-work across turns, by centralising budget-aware truncation and making preservation semantic (by named output section) rather than positional.

**Architecture:** A new pure module `app/core/context_budget.py` holds every "fit text into a budget" primitive (word-boundary truncate, keep-tail, keep-head-tail, output-section parsing, tiered allocation). The ~6 dangerous truncation sites and the `extract_did_what` schema gap are fixed to use it. Already-correct paths (extract_conclusion, compaction, DECISIONS/FINAL_REPORT capping, ACTIVITY/EPOCHS recovery) are untouched.

**Tech Stack:** Python 3.12, pytest (asyncio_mode=auto), stdlib `re` only (no new deps).

## Global Constraints

- Pure module: `app/core/context_budget.py` has NO I/O, NO LLM, NO `self` — stdlib only.
- `smart_truncate` MOVES to the new module; `ledger_extract.py` re-exports it so the 4 existing importers (`context_index.py:26`, `nexus_orchestrator.py:150,2784,2809`) keep working unchanged.
- Fail-open everywhere: any helper returns its input unchanged when no known structure is present — never worse than a bare slice.
- Never blind-cut agent-authored content: elision must be word-boundary (`smart_truncate`) or keep-tail / keep-head-tail with a visible marker.
- Do NOT touch the safe `[:N]` sites (UI preview, logs, read-side prompt budget) or the confirmed-correct paths listed in the spec §1.2.
- Full test suite (currently 1247 passed, 5 skipped) must stay green.

---

### Task 1: `context_budget` core primitives (smart_truncate move + keep_tail + keep_head_tail)

**Files:**
- Create: `app/core/context_budget.py`
- Modify: `app/orchestration/ledger_extract.py:19-40` (remove the local `smart_truncate` body, re-export from the new module)
- Test: `tests/test_context_budget.py`

**Interfaces:**
- Produces:
  - `smart_truncate(text: str, max_len: int, ellipsis: str = "…") -> str`
  - `keep_tail(text: str, max_len: int, marker: bool = True) -> str`
  - `keep_head_tail(text: str, head: int, tail: int, marker: bool = True) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_budget.py
from app.core.context_budget import smart_truncate, keep_tail, keep_head_tail


def test_smart_truncate_word_boundary():
    assert smart_truncate("the quick brown fox jumps", 12) == "the quick…"

def test_smart_truncate_unchanged_when_fits():
    assert smart_truncate("short", 100) == "short"

def test_smart_truncate_hard_cut_when_no_space():
    # one long unbroken token → still bounded
    out = smart_truncate("a" * 50, 10)
    assert len(out) <= 11 and out.endswith("…")

def test_keep_tail_keeps_end():
    text = "START " + "x " * 200 + "THE-END"
    out = keep_tail(text, 40)
    assert "THE-END" in out
    assert "START" not in out
    assert out.startswith("…")

def test_keep_tail_unchanged_when_fits():
    assert keep_tail("short tail", 100) == "short tail"

def test_keep_head_tail_drops_middle_keeps_both_ends():
    text = "HEAD " + "m" * 500 + " TAIL"
    out = keep_head_tail(text, head=10, tail=10)
    assert "HEAD" in out and "TAIL" in out
    assert "[… " in out  # elision marker
    assert "mmmmmmmmmm" not in out  # middle gone (10+ m's)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_context_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.context_budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/context_budget.py
"""Budget-aware truncation & context allocation primitives (pure, no I/O).

Single home for "fit text/items into a token budget" so the engine never
blind-cuts an agent's conclusion. Positional cuts (`text[:n]`) across the
context files dropped the END of an agent's output — where completed-work
records and conclusions live — so the next turn lost finished work and redid it
(tri-model audit 2026-07-01). These helpers cut on word boundaries, can keep the
tail or both ends, and mark every elision so it is visibly recoverable.
"""

from __future__ import annotations

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
    _mid = f"\n[… {len(text) - head - tail} chars elided — activity_search for full …]\n" if marker else ""
    return text[:head].rstrip() + _mid + text[-tail:].lstrip()
```

- [ ] **Step 4: Update `ledger_extract.py` to re-export (keep back-compat)**

Replace the `smart_truncate` function body at `app/orchestration/ledger_extract.py:19` with a re-export. The existing docstring lines above `def smart_truncate` can stay as a module comment or be removed; the import line is what matters:

```python
# near the top imports of ledger_extract.py, after `import re`:
from app.core.context_budget import smart_truncate  # re-exported (single source of truth)
```

Then DELETE the local `def smart_truncate(...)` definition (lines ~19-40). Every existing `from app.orchestration.ledger_extract import smart_truncate` keeps working because the name is now imported into the module namespace.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_context_budget.py tests/test_ledger_did_what_upgrade.py -v`
Expected: PASS (new module tests + existing ledger tests that use smart_truncate).

- [ ] **Step 6: Commit**

```bash
git add app/core/context_budget.py app/orchestration/ledger_extract.py tests/test_context_budget.py
git commit -m "feat(context_budget): core truncation primitives (smart_truncate move + keep_tail + keep_head_tail)"
```

---

### Task 2: `parse_output_sections` + `SECTION_PRIORITY`

**Files:**
- Modify: `app/core/context_budget.py`
- Test: `tests/test_context_budget.py`

**Interfaces:**
- Consumes: (none — pure text)
- Produces:
  - `parse_output_sections(text: str) -> "collections.OrderedDict[str, str]"` — maps a canonical section key to its body; ungrouped preamble under key `""`.
  - `SECTION_PRIORITY: tuple[tuple[str, int], ...]` — (canonical_key, priority) where 0 = inviolable conclusion, higher = more elastic.
  - `classify_section(title: str) -> tuple[str, int]` — normalise a raw heading to (canonical_key, priority); returns `("other", 3)` when unknown.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_context_budget.py
from app.core.context_budget import parse_output_sections, classify_section

_REAL_OUTPUT = """Some preamble line.

## ACCOMPLISHED
- Selected downdraft gasifier type.
- Calculated main dimensions.

## KEY FINDINGS
- Downdraft optimal for high-ash biomass.

## STILL MISSING
- Detailed mechanical drawing.
"""

def test_parse_sections_splits_named_blocks():
    secs = parse_output_sections(_REAL_OUTPUT)
    assert "downdraft gasifier" in secs["accomplished"]
    assert "high-ash biomass" in secs["key_findings"]
    assert "mechanical drawing" in secs["still_missing"]
    assert "preamble" in secs[""]

def test_parse_sections_vietnamese_headings():
    secs = parse_output_sections("## Kết quả\n- Đã tính xong.\n")
    assert "Đã tính xong" in secs["conclusion"]

def test_parse_sections_no_headings_is_preamble():
    secs = parse_output_sections("just prose, no headings here")
    assert secs[""].strip() == "just prose, no headings here"
    assert len(secs) == 1

def test_classify_accomplished_is_priority_zero():
    assert classify_section("ACCOMPLISHED")[1] == 0
    assert classify_section("Kết quả")[1] == 0
    assert classify_section("STILL MISSING")[1] == 1
    assert classify_section("KEY FINDINGS")[1] == 2
    assert classify_section("Random Heading") == ("other", 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_context_budget.py -k "sections or classify" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_output_sections'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to app/core/context_budget.py
import re
from collections import OrderedDict

# Canonical section keys → priority (0 = inviolable conclusion/done-work,
# 1 = inviolable remaining-work steer, 2 = elastic findings, 3 = elastic other).
# EN + VI aliases observed in real ACTIVITY.md across the audited runs.
_SECTION_ALIASES: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("accomplished", "summary", "conclusion", "final", "result",
      "kết quả", "kết luận", "đã hoàn thành", "tóm lại", "báo cáo hoàn thành",
      "báo cáo"), "conclusion" if False else "accomplished", 0),
    (("still missing", "next focus", "next step", "remaining", "còn thiếu",
      "chưa hoàn thành"), "still_missing", 1),
    (("key findings", "key data", "findings", "dữ liệu", "phát hiện"),
     "key_findings", 2),
)
# Flatten to a lookup that also yields a stable canonical key for VI conclusion
# headings (they map to "conclusion" so extract_did_what can prefer them too).
_CANON_OVERRIDES = {"kết quả": "conclusion", "kết luận": "conclusion",
                    "đã hoàn thành": "conclusion", "tóm lại": "conclusion"}

SECTION_PRIORITY: tuple[tuple[str, int], ...] = (
    ("accomplished", 0), ("conclusion", 0),
    ("still_missing", 1), ("key_findings", 2), ("other", 3),
)

_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,4}\s*(?P<title>[^\n#][^\n]*?)\s*$")


def classify_section(title: str) -> tuple[str, int]:
    """Normalise a raw heading to (canonical_key, priority)."""
    t = (title or "").strip().lower().rstrip(":")
    if t in _CANON_OVERRIDES:
        return _CANON_OVERRIDES[t], 0
    for aliases, canon, prio in _SECTION_ALIASES:
        for a in aliases:
            if t.startswith(a):
                return canon, prio
    return "other", 3


def parse_output_sections(text: str):
    """Split agent Output into named sections. Preamble (before any heading) is
    stored under key "". Unknown headings map to their canonical key via
    classify_section (so "## Random" lands under "other"). Fail-open: no heading
    → the whole text under key ""."""
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
        canon, _ = classify_section(m.group("title"))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if canon in out:
            out[canon] += "\n" + body
        else:
            out[canon] = body
    return out
```

Note: the `"conclusion" if False else "accomplished"` above is a mistake artifact — write it as plain `"accomplished"`. (Kept the alias list; VI conclusion headings map via `_CANON_OVERRIDES` to `"conclusion"`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_context_budget.py -k "sections or classify" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/context_budget.py tests/test_context_budget.py
git commit -m "feat(context_budget): parse_output_sections + section priority (semantic preservation)"
```

---

### Task 3: `allocate_tiered`

**Files:**
- Modify: `app/core/context_budget.py`
- Test: `tests/test_context_budget.py`

**Interfaces:**
- Consumes: `smart_truncate` (Task 1)
- Produces: `allocate_tiered(fragments: list[tuple[int, str, str]], budget: int) -> str` — `fragments` are `(priority, text, recovery_hint)`; priority 0/1 kept whole, lower tiers shrunk newest-first, a single recovery line appended for anything elided.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_context_budget.py
from app.core.context_budget import allocate_tiered


def test_allocate_keeps_conclusions_whole_even_over_budget():
    frags = [
        (0, "CONCLUSION: sizing closes within 5 percent for the reactor.", ""),
        (2, "detail " * 100, "activity_search(reactor, T3)"),
    ]
    out = allocate_tiered(frags, budget=80)
    assert "sizing closes within 5 percent" in out  # conclusion intact
    assert "activity_search(reactor, T3)" in out     # recovery pointer present

def test_allocate_shrinks_details_not_conclusions():
    frags = [
        (0, "DONE: built the model.", ""),
        (2, "x" * 400, "recall_x"),
    ]
    out = allocate_tiered(frags, budget=60)
    assert "built the model" in out
    assert "x" * 400 not in out  # detail shrunk

def test_allocate_all_fits_returns_everything():
    frags = [(0, "a done", ""), (2, "b detail", "")]
    out = allocate_tiered(frags, budget=1000)
    assert "a done" in out and "b detail" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_context_budget.py -k allocate -v`
Expected: FAIL — `ImportError: cannot import name 'allocate_tiered'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to app/core/context_budget.py
def allocate_tiered(fragments, budget: int) -> str:
    """Fit (priority, text, recovery_hint) fragments into ``budget`` chars.

    Conclusion-tier fragments (priority <= 1) are kept WHOLE first — they are
    never dropped or cut. Whatever budget remains is split across lower tiers,
    smart_truncated, most-recent-first (input order = oldest→newest, so we spend
    the remainder on the tail of the list). Anything elided contributes its
    recovery_hint to a single trailing pointer line so nothing is silently lost.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_context_budget.py -k allocate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/context_budget.py tests/test_context_budget.py
git commit -m "feat(context_budget): allocate_tiered — conclusions whole, details elastic + recovery pointer"
```

---

### Task 4: `extract_did_what` schema fix (recognise ACCOMPLISHED + VI, prefer done-work)

**Files:**
- Modify: `app/orchestration/ledger_extract.py:71-81` (Strategy-1 markers)
- Test: `tests/test_ledger_did_what_schema.py`

**Interfaces:**
- Consumes: `parse_output_sections`, `classify_section` (Task 2), `smart_truncate` (Task 1)
- Produces: (same signature) `extract_did_what(output: str) -> str` — now prefers the ACCOMPLISHED/Conclusion section over the tail heuristic.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger_did_what_schema.py
"""did_what must capture DONE-work when the output uses the engine's own schema
(## ACCOMPLISHED / KEY FINDINGS / STILL MISSING) — audit tri-model 2026-07-01.
Previously extract_did_what only knew Summary/Conclusion/Final/Result, so an
ACCOMPLISHED-only output fell through to the tail heuristic and could capture
STILL MISSING (undone work) instead."""

from __future__ import annotations

from app.orchestration.ledger_extract import extract_did_what

_ACCOMPLISHED_OUTPUT = (
    "Preamble reasoning.\n\n"
    "## ACCOMPLISHED\n"
    "- Selected downdraft gasifier type based on high-ash rice husk.\n"
    "- Calculated reactor diameter 0.55 m and height 1.65 m.\n\n"
    "## KEY FINDINGS\n- Downdraft optimal for high-ash biomass.\n\n"
    "## STILL MISSING\n- Detailed mechanical drawing and supplier info.\n"
) + ("x" * 600)  # push over the 500-char short-circuit


def test_did_what_prefers_accomplished_over_still_missing():
    out = extract_did_what(_ACCOMPLISHED_OUTPUT)
    assert "downdraft gasifier" in out or "reactor diameter" in out
    assert "Detailed mechanical drawing" not in out  # must NOT be the STILL MISSING


def test_did_what_vietnamese_conclusion():
    text = "Dài dòng mở đầu.\n\n## Kết quả\n- Đã hoàn thành mô phỏng, hiệu suất 23%.\n" + ("y" * 600)
    out = extract_did_what(text)
    assert "hoàn thành mô phỏng" in out


def test_did_what_existing_summary_still_works():
    text = "intro\n\n## Summary\nBuilt and validated the model end to end.\n" + ("z" * 600)
    out = extract_did_what(text)
    assert "Built and validated" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ledger_did_what_schema.py -v`
Expected: FAIL — `test_did_what_prefers_accomplished_over_still_missing` (tail heuristic returns the STILL MISSING / trailing x's) and the VI case.

- [ ] **Step 3: Write minimal implementation**

Replace Strategy 1 in `extract_did_what` (`app/orchestration/ledger_extract.py:71-81`) with a section-aware preference that reuses Task 2, keeping the existing regex fallback:

```python
    # Strategy 1: named-section preference — the engine prompts agents for
    # ## ACCOMPLISHED / KEY FINDINGS / STILL MISSING (agent_worker.py:1844). Prefer
    # the DONE-work section (accomplished/conclusion) so did_what records what was
    # completed, never the STILL MISSING todo. (audit tri-model 2026-07-01)
    from app.core.context_budget import parse_output_sections
    _secs = parse_output_sections(text)
    for _key in ("accomplished", "conclusion"):
        _body = _secs.get(_key, "").strip()
        if len(_body) > 20 and not _body.startswith(("{", "[")):
            return smart_truncate(_body, 500)

    # Strategy 1b (legacy regex, kept): explicit summary section headers.
    summary_patterns = [
        r"##\s*(?:Summary|Conclusion|Final|Result)[^\n]*\n([\s\S]{20,500}?)(?:\n##|\Z)",
        r"(?:FINAL ANSWER|CONCLUSION|SUMMARY)[:\s]*\n?([\s\S]{20,500}?)(?:\n##|\n\n\n|\Z)",
    ]
    for pattern in summary_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if len(candidate) > 30 and not candidate.startswith(("{", "[")):
                return smart_truncate(candidate, 500)
```

(Leave Strategy 2 and Strategy 3 below unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ledger_did_what_schema.py tests/test_ledger_did_what_upgrade.py -v`
Expected: PASS (new schema tests + existing upgrade tests — no regression).

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/ledger_extract.py tests/test_ledger_did_what_schema.py
git commit -m "fix(ledger_extract): did_what recognises ACCOMPLISHED + VI headings, prefers done-work"
```

---

### Task 5: Persist-side smart_truncate — H2 (did_what notes), H3 (synth), M1 (epoch)

**Files:**
- Modify: `app/orchestration/pipeline.py:1574` (H2), `app/orchestration/pipeline.py:1624` (H3)
- Modify: `app/orchestration/context_ledger.py:771-772` (M1)
- Test: `tests/test_persist_smart_truncate.py`

**Interfaces:**
- Consumes: `smart_truncate` (Task 1, via `ledger_extract` or `context_budget`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_persist_smart_truncate.py
"""Persisted LEDGER/EPOCHS fields must be word-boundary truncated, not hard-cut
mid-word (audit tri-model 2026-07-01 — the surviving variant of the did_what
mid-word bug lived on the orchestrator-notes branch, not the extract fallback)."""

from __future__ import annotations

from app.core.context_budget import smart_truncate


def test_notes_truncation_is_word_boundary():
    notes = "Completed the reactor sizing and validated closure " * 40  # long
    out = smart_truncate(notes, 800)
    assert len(out) <= 801
    assert out.endswith("…")
    assert not out[:-1].rstrip().endswith(" ")  # ends on a whole word
```

This test pins the shared helper's behaviour (the sites just call it). The site
edits below are verified by the full-suite run in Step 4 + manual diff review.

- [ ] **Step 2: Run test to verify it passes as the contract, then edit the sites**

Run: `python -m pytest tests/test_persist_smart_truncate.py -v`
Expected: PASS (it validates `smart_truncate`, already implemented in Task 1).

- [ ] **Step 3: Edit the three sites to use smart_truncate**

H2 — `app/orchestration/pipeline.py:1574`, change:
```python
                    did_what = ae.get("notes", "")[:_did_what_cap]
```
to:
```python
                    from app.orchestration.ledger_extract import smart_truncate
                    did_what = smart_truncate(ae.get("notes", ""), _did_what_cap)
```

H3 — `app/orchestration/pipeline.py:1624`, change:
```python
                did_what=synth_text[:4000],
```
to:
```python
                did_what=smart_truncate(synth_text, 4000),
```
(ensure `from app.orchestration.ledger_extract import smart_truncate` is in scope in that function; add the local import if needed.)

M1 — `app/orchestration/context_ledger.py:771-772`, change:
```python
    if len(summary) > 4000:
        summary = summary[:4000] + "..."
```
to:
```python
    if len(summary) > 4000:
        from app.orchestration.ledger_extract import smart_truncate
        summary = smart_truncate(summary, 4000)
```

- [ ] **Step 4: Run tests + full suite slice**

Run: `python -m pytest tests/test_persist_smart_truncate.py tests/ -k "ledger or pipeline or epoch or persist" -v`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/pipeline.py app/orchestration/context_ledger.py tests/test_persist_smart_truncate.py
git commit -m "fix(persist): smart_truncate did_what notes / team-consensus / epoch summary (no mid-word cut)"
```

---

### Task 6: N1 — TODO.md keeps the RECENT status, not the stale head

**Files:**
- Modify: `app/orchestration/turn_executor.py:248-256`
- Test: `tests/test_todo_context_recent.py`

**Interfaces:**
- Consumes: `keep_tail` (Task 1)

**Background:** `TODO.md` is rewritten each turn by `update_todo` (nexus_orchestrator.py:2470) with the plan first and a `## Current Status — Turn N` block near the end. When it grows past 3000 chars, `read_text()[:3000]` keeps the plan head and drops the newest status — the completed-items / next-focus the agent most needs. Fix: prefer the tail (recent status) when over budget.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_todo_context_recent.py
"""When TODO.md exceeds the context cap, the injected slice must keep the MOST
RECENT status (tail), not the stale plan head — else the agent can't see what's
just been completed and repeats it (audit tri-model 2026-07-01)."""

from __future__ import annotations

from pathlib import Path

from app.orchestration.turn_executor import _todo_context_slice


def test_todo_slice_keeps_recent_status_over_stale_head():
    todo = (
        "# Task TODO\n## Execution Plan\n"
        + ("### Turn 1: PHASE 1 — recon\n- Pending\n" * 200)  # long stale head
        + "\n## Current Status — Turn 9 | Progress: 40%\n"
        "### Completed\n- [x] Built the model this turn\n"
        "### Next Focus\nRun the validation suite.\n"
    )
    out = _todo_context_slice(todo, max_chars=3000)
    assert "Current Status — Turn 9" in out
    assert "Built the model this turn" in out
    assert "Run the validation suite" in out


def test_todo_slice_unchanged_when_small():
    todo = "# Task TODO\n## Current Status — Turn 1 | Progress: 10%\n"
    assert _todo_context_slice(todo, max_chars=3000) == todo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_todo_context_recent.py -v`
Expected: FAIL — `ImportError: cannot import name '_todo_context_slice'`

- [ ] **Step 3: Write minimal implementation**

Add a small helper to `turn_executor.py` (module level, near the other context helpers) and use it at line 250.

```python
# module-level in app/orchestration/turn_executor.py
def _todo_context_slice(todo_text: str, max_chars: int) -> str:
    """Slice TODO.md for injection so the RECENT status survives, not the stale
    plan head. TODO is written plan-first with a "## Current Status — Turn N"
    block near the end; when over budget keep from the last Current-Status marker
    onward (tail), falling back to keep_tail if the marker is absent."""
    if len(todo_text) <= max_chars:
        return todo_text
    from app.core.context_budget import keep_tail
    marker = todo_text.rfind("## Current Status")
    if marker > 0:
        tail = todo_text[marker:]
        if len(tail) <= max_chars:
            # prepend as much of the plan head as still fits (context for the status)
            head_budget = max_chars - len(tail) - 1
            head = todo_text[:head_budget].rstrip() if head_budget > 200 else ""
            return (head + "\n" + tail) if head else tail
        return keep_tail(tail, max_chars)
    return keep_tail(todo_text, max_chars)
```

Then at `app/orchestration/turn_executor.py:250`, change:
```python
                    _todo_cap = min(3000, _ctx_remaining)
                    _todo_content = _todo_path.read_text(encoding="utf-8")[:_todo_cap]
```
to:
```python
                    _todo_cap = min(3000, _ctx_remaining)
                    _todo_content = _todo_context_slice(
                        _todo_path.read_text(encoding="utf-8"), _todo_cap
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_todo_context_recent.py tests/test_workspace_reader_progress.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/turn_executor.py tests/test_todo_context_recent.py
git commit -m "fix(turn_executor): TODO context keeps recent status, not stale plan head (N1)"
```

---

### Task 7: N4 — `_force_synthesis` keeps tool-output tail

**Files:**
- Modify: `app/orchestration/agent_worker.py:1883,1891` (inside `_force_synthesis`)
- Test: `tests/test_force_synthesis_tail.py`

**Interfaces:**
- Consumes: `keep_head_tail` (Task 1)

**Background:** `_force_synthesis` builds the agent's FINAL answer from tool outputs but slices `tc.output[:8000]` (head only). A command whose final result is at the end loses it exactly when the conclusion is being written. Keep both ends.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_force_synthesis_tail.py
"""_force_synthesis must keep the TAIL of a long tool output — the final result
of a command often lives at the end, and this step writes the agent's conclusion
(audit tri-model 2026-07-01: N3 react-loop already keeps the tail; N4 did not)."""

from __future__ import annotations

from app.core.context_budget import keep_head_tail


def test_keep_head_tail_preserves_final_result_line():
    output = "RUN LOG START\n" + ("step\n" * 2000) + "FINAL RESULT: efficiency = 23.4%"
    kept = keep_head_tail(output, head=4000, tail=4000)
    assert "RUN LOG START" in kept
    assert "FINAL RESULT: efficiency = 23.4%" in kept
```

(Behaviour is in the shared helper; the site edit is verified by full-suite +
diff review, matching the Task-5 pattern.)

- [ ] **Step 2: Run test to verify it passes as contract**

Run: `python -m pytest tests/test_force_synthesis_tail.py -v`
Expected: PASS (validates `keep_head_tail`).

- [ ] **Step 3: Edit `_force_synthesis`**

At `app/orchestration/agent_worker.py:1883`, change:
```python
                    tool_data_parts.append(header + tc.output[:8000])
```
to:
```python
                    from app.core.context_budget import keep_head_tail
                    tool_data_parts.append(header + keep_head_tail(tc.output, 6000, 2000))
```
At `app/orchestration/agent_worker.py:1891` (fallback message-level), change:
```python
                        tool_data_parts.append(content[:8000])
```
to:
```python
                        tool_data_parts.append(keep_head_tail(content, 6000, 2000))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_force_synthesis_tail.py tests/ -k "agent_worker or synthesis or phantom" -v`
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/agent_worker.py tests/test_force_synthesis_tail.py
git commit -m "fix(agent_worker): _force_synthesis keeps tool-output tail (N4)"
```

---

### Task 8: H1 — `next_turn_focus` word-boundary, no lost steering

**Files:**
- Modify: `app/orchestration/nexus_orchestrator.py:1262, 1684, 1719, 1727, 1776`
- Test: `tests/test_next_focus_truncation.py`

**Interfaces:**
- Consumes: `smart_truncate` (Task 1)

**Decision (refines spec):** the corrective directive (RESOLVE CONTRADICTION / FORECAST PIVOT / …) is the NEW steering the orchestrator just decided and belongs first; the trailing `_focus` is prior context. So the fix is: raise the cap to 800 and `smart_truncate` (word boundary) — at 800 both the directive and a normal `_focus` fit, and nothing is cut mid-word. (Putting `_focus` first, as the spec sketched, would bury the corrective directive — rejected.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_next_focus_truncation.py
"""next_turn_focus must not be hard-cut mid-word; at the raised cap both the
corrective directive and the trailing prior-focus survive (audit 2026-07-01)."""

from __future__ import annotations

from app.core.context_budget import smart_truncate

_NEXT_FOCUS_CAP = 800


def test_directive_plus_focus_fits_and_is_word_boundary():
    directive = "[RESOLVE CONTRADICTION] Settle with ONE ground-truth call: LHV 5.30 vs 4.87. "
    focus = "Then finalise the reactor sizing section and the BOM table."
    out = smart_truncate(directive + focus, _NEXT_FOCUS_CAP)
    assert "RESOLVE CONTRADICTION" in out
    assert "BOM table" in out           # trailing focus preserved at 800
    assert not out.endswith(" ")


def test_overlong_focus_cut_on_word_boundary():
    out = smart_truncate("[PIVOT] " + "word " * 400, _NEXT_FOCUS_CAP)
    assert len(out) <= _NEXT_FOCUS_CAP + 1
    assert out.endswith("…")
```

- [ ] **Step 2: Run test to verify it passes as contract**

Run: `python -m pytest tests/test_next_focus_truncation.py -v`
Expected: PASS (validates the helper + chosen cap).

- [ ] **Step 3: Edit the five sites**

Add `from app.orchestration.ledger_extract import smart_truncate` at the top of the methods if not already in scope, then replace each `)[:500]` on a `next_turn_focus` / `eval_data["next_turn_focus"]` assignment with `smart_truncate(<expr>, 800)`. Concretely:

`nexus_orchestrator.py:1261-1262`:
```python
                eval_data["next_turn_focus"] = smart_truncate(
                    f"[RESOLVE CONTRADICTION] Settle this with ONE ground-truth call "
                    f"(API/tool/file check), not another analysis doc: {_conflict}. {_focus}",
                    800,
                )
```
`:1679-1684`:
```python
            evaluation.next_turn_focus = smart_truncate(
                "[RECONCILIATION — FINAL TURN] The loop has plateaued. Pin EVERY headline "
                "number to the actual stdout of the on-disk script that produces it (run it). "
                "Report the REAL progress %, list every unresolved contradiction explicitly, "
                "and do NOT fake completion. After this turn the task stops. " + _focus,
                800,
            )
```
`:1718-1719` (FORECAST PIVOT), `:1723-1727` (INSTALL REQUIRED), `:1772-1776` (METACOGNITIVE): same transform — wrap the parenthesised string in `smart_truncate(<expr>, 800)` instead of `(<expr>)[:500]`.

- [ ] **Step 4: Run tests + full suite slice**

Run: `python -m pytest tests/test_next_focus_truncation.py tests/ -k "termination or orchestrator or decisions" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/nexus_orchestrator.py tests/test_next_focus_truncation.py
git commit -m "fix(orchestrator): next_turn_focus word-boundary at cap 800, keep directive+focus (H1)"
```

---

### Task 9: Regression canary + doctrine note + full-suite gate

**Files:**
- Modify: `app/core/context_budget.py` (module docstring doctrine note)
- Test: `tests/test_context_truncation_canary.py`

**Interfaces:** (none new)

- [ ] **Step 1: Write the canary test**

```python
# tests/test_context_truncation_canary.py
"""Canary: the specific sites fixed in the context-allocation work must not
regress to a bare `[:N]` on agent-authored content. Targeted (not a blanket ban —
safe preview/log [:N] elsewhere is fine). audit tri-model 2026-07-01."""

from __future__ import annotations

from pathlib import Path

import re

_ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_did_what_notes_not_hard_sliced():
    src = _src("app/orchestration/pipeline.py")
    assert 'ae.get("notes", "")[:_did_what_cap]' not in src
    assert "smart_truncate(ae.get(\"notes\", \"\")" in src


def test_synth_text_not_hard_sliced():
    src = _src("app/orchestration/pipeline.py")
    assert "synth_text[:4000]" not in src


def test_epoch_summary_not_hard_sliced():
    src = _src("app/orchestration/context_ledger.py")
    assert 'summary[:4000] + "..."' not in src


def test_todo_uses_recent_slice():
    src = _src("app/orchestration/turn_executor.py")
    assert '.read_text(encoding="utf-8")[:_todo_cap]' not in src
    assert "_todo_context_slice(" in src


def test_next_focus_not_hard_sliced_at_500():
    src = _src("app/orchestration/nexus_orchestrator.py")
    # the goal_anchor read at :1118 legitimately uses [:500]; the next_turn_focus
    # assignments must not. Assert the directive strings are wrapped in smart_truncate.
    assert ")[:500]" not in src.replace('read_text(encoding="utf-8")[:500]', "")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_context_truncation_canary.py -v`
Expected: PASS (all sites already fixed in Tasks 4-8).

- [ ] **Step 3: Add the doctrine note**

Prepend to `app/core/context_budget.py` module docstring (after the existing summary):

```python
# DOCTRINE: content destined for an agent's context window or a persistent
# _context/ file (LEDGER, DECISIONS, TODO, EPOCHS, FINAL_REPORT, next_turn_focus)
# MUST be fitted through these helpers — smart_truncate / keep_tail /
# keep_head_tail / allocate_tiered — NEVER a bare text[:N]. A bare slice drops the
# tail, where an agent's completed-work record and conclusions live. Safe [:N]
# stays allowed for UI-preview / log / read-side prompt-budget only.
```

- [ ] **Step 4: Run the FULL suite**

Run: `python -m pytest tests/ -q`
Expected: PASS — 1247+ passed (new tests added), 0 failed.

- [ ] **Step 5: Commit**

```bash
git add app/core/context_budget.py tests/test_context_truncation_canary.py
git commit -m "test(context_budget): regression canary + doctrine note for tiered truncation"
```

---

## Self-Review

**Spec coverage:**
- §3 Layer 1 module → Tasks 1-3 ✓
- §3 Layer 2 EW (extract_did_what) → Task 4 ✓; N1 → Task 6 ✓; H2/H3/M1 → Task 5 ✓; N4 → Task 7 ✓; H1 → Task 8 ✓
- §3 Layer 3 invariant + canary → Task 9 ✓
- §2 tiered priority (ACCOMPLISHED & STILL MISSING inviolable) → Task 2 (`SECTION_PRIORITY` 0/1) + Task 3 (`allocate_tiered` keeps priority<=1 whole) ✓
- §6 non-goals (no relevance-ranking, don't touch safe/correct paths) → respected; no task edits extract_conclusion / compaction / DECISIONS / FINAL_REPORT ✓

**Placeholder scan:** No TBD/TODO. One artifact flagged inline in Task 2 Step 3 (`"conclusion" if False else "accomplished"`) with an explicit correction note — implementer writes plain `"accomplished"`.

**Type consistency:** `smart_truncate(text, max_len, ellipsis)`, `keep_tail(text, max_len, marker)`, `keep_head_tail(text, head, tail, marker)`, `parse_output_sections(text) -> OrderedDict`, `classify_section(title) -> (str, int)`, `allocate_tiered(fragments, budget)`, `_todo_context_slice(todo_text, max_chars)` — names/signatures consistent across producing (Tasks 1-3, 6) and consuming (Tasks 4-8) tasks.

**Note on `allocate_tiered` usage:** Tasks 4-8 fix the concrete sites directly; `allocate_tiered` is built (Task 3) as the reusable primitive for section-tiered assembly and is exercised by its unit tests. Wiring it into `build_context_for_agent` is intentionally NOT in this plan — that path is already section-based and correct (spec §1.2); introducing `allocate_tiered` there is a separate, optional follow-up to avoid destabilising a working path.
