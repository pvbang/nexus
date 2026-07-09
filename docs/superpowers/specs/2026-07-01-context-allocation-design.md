# Section-Aware Tiered Context Allocation — Design

**Date:** 2026-07-01
**Status:** Approved (design), pending implementation plan
**Origin:** tri-model audit follow-up — the user flagged that pervasive `text[:N]`
truncation across the engine risks cutting off the *tail* of an agent's output,
where conclusions / completed work live, causing later turns to lose finished
work and repeat it.

---

## 1. Problem

The engine truncates data with hard slices (`text[:N]`) in ~1472 places. The
concern: in a sequential multi-turn task, almost nothing an agent produces is
"filler" — cutting the end silently drops the conclusion / completed-work record,
so the next turn can't see what's done and redoes it (or loses a needed result).

### 1.1 What the investigation actually found (reports 40 + 41)

Not all 1472 sites are dangerous. Three findings reframe the problem:

1. **The core summary pipeline is already correct.** `extract_did_what`,
   `_extract_conclusion`, and `_compress_context_if_needed` already prioritise the
   tail / summary section, use `smart_truncate` (word-boundary), and lean on
   `ACTIVITY.md` + `EPOCHS` as an always-recoverable net. Most `[:N]` sites are
   safe (UI preview, logs, read-side prompt-budget).

2. **Conclusions are SEMANTIC, not positional.** Observing the real
   `ACTIVITY.md` of all three runs: an agent's Output block follows a fixed schema
   the engine itself prompts for (`agent_worker.py:1844`):

   ```
   ## ACCOMPLISHED      ← what got DONE this turn (most important for next turn)
   ## KEY FINDINGS      ← data/results
   ## STILL MISSING     ← what remains (steers the next turn)
   ```

   `ACCOMPLISHED` is at the **head**, `STILL MISSING` at the **tail**. So neither
   "keep head" nor "keep tail" is right on its own — preservation must be **by
   named section**, not by position.

3. **A real schema-coupling gap exists.** `extract_did_what` (ledger_extract.py:73)
   recognises `## Summary|Conclusion|Final|Result` but **NOT** `## ACCOMPLISHED` —
   the very heading the engine tells agents to write, nor the Vietnamese headings
   that appear in real runs (`## Kết quả`, `## Kết luận`, `## BÁO CÁO HOÀN THÀNH`).
   So the did_what extractor usually falls through to the tail heuristic, which can
   capture `STILL MISSING` (undone work) instead of `ACCOMPLISHED` (done work).

### 1.2 The ~6 genuinely dangerous truncation sites

| ID | Site | What it cuts | Why dangerous |
|----|------|--------------|---------------|
| N1 | `turn_executor.py:250` `TODO.md[:3000]` | keeps HEAD of an append-log TODO | newest progress (✅/next-focus) is at the END → dropped when TODO grows → repeat work |
| H2 | `pipeline.py:1574` `notes[:cap]` | did_what from orchestrator notes | hard cut mid-word into a persisted LEDGER conclusion (no smart_truncate) |
| H1 | `nexus_orchestrator.py` ×5 `(directive + _focus)[:500]` | next_turn_focus | prepend-directive pushes the LLM's ORIGINAL steering to the tail → `[:500]` drops it |
| N4 | `agent_worker.py:1883,1891` `tc.output[:8000]` | tool data for final synthesis | keeps HEAD of tool output when synthesising the agent's conclusion |
| H3 | `pipeline.py:1624` `synth_text[:4000]` | team consensus → LEDGER | hard cut of a persisted binding agreement |
| M1 | `context_ledger.py:772` `summary[:4000]+"..."` | epoch summary → EPOCHS | hard cut may drop the last role's contribution |
| EW | `ledger_extract.py:73` | did_what extractor markers | misses `ACCOMPLISHED` / VI headings (§1.1.3) |

Confirmed NOT broken (do not touch): DECISIONS.md entry splitting (line-anchored,
drops whole entries), FINAL_REPORT section capping (stops at `##`/`---` boundary +
tail-verify pass), `_extract_conclusion` (head+middle+tail), compaction (keeps
anchor + last 20 msgs).

---

## 2. Design principle

> **Conclusions are inviolable. Details are elastic. Never blind-cut a tail.**

When context for one turn exceeds its token budget, the engine must (a) preserve
every *conclusion-tier* fragment whole, (b) shrink *detail-tier* fragments,
dropping oldest-first, and (c) always leave a recovery pointer (`activity_search`)
for anything elided — never a silent hard `[:N]`.

Preservation priority (highest → lowest), by SEMANTIC section:

1. **ACCOMPLISHED / Summary / Conclusion / Kết quả / Kết luận** — done work. Inviolable.
2. **STILL MISSING / next-focus** — remaining work. Inviolable (steers next turn).
3. **KEY FINDINGS / numeric results** — elastic, `smart_truncate`.
4. **Raw detail / tool output** — dropped first, replaced by an `activity_search` pointer.

---

## 3. Architecture

Three layers. Reuse the correct existing machinery; only add a central toolkit,
fix the ~6 sites, and add regression guards.

### Layer 1 — `app/core/context_budget.py` (NEW, pure, no I/O)

Single home for every "fit text/items into a budget" primitive. Pure functions,
unit-testable, no LLM/DB. `smart_truncate` MOVES here (re-exported from
`ledger_extract` for back-compat) so there is one source of truth.

```
smart_truncate(text, max_len, ellipsis="…") -> str
    # word-boundary cut + ellipsis (existing behaviour, relocated).

keep_tail(text, max_len, marker=True) -> str
    # keep the END (for append-logs / outputs whose latest state is last).

keep_head_tail(text, head, tail, marker=True) -> str
    # keep both ends, drop the middle, with a "[… elided …]" marker.

parse_output_sections(text) -> OrderedDict[str, str]
    # split an agent Output into its named sections (ACCOMPLISHED / KEY FINDINGS /
    # STILL MISSING / Summary / Kết quả / Kết luận / BÁO CÁO …), EN + VI, case-insensitive.
    # Returns {section_title: body}; ungrouped preamble under key "".

SECTION_PRIORITY: tuple    # canonical ordering used by allocate_tiered & extract.

allocate_tiered(fragments, budget) -> str
    # fragments: list of (priority:int, text:str, recovery_hint:str|"").
    # Give every priority-0/1 (conclusion) fragment its full length first; split
    # the remainder across lower tiers newest-first with smart_truncate; append a
    # single recovery line naming what was elided + how to recall it. Never drops
    # a conclusion-tier fragment; never blind-cuts.
```

`recovery_hint` = a short `activity_search(keyword, turn)` string the caller
supplies, so elision is always recoverable (matches the existing truncated_topics
convention in the ledger).

### Layer 2 — fix the ~6 sites via Layer 1

- **EW (root schema fix):** `extract_did_what` gains `ACCOMPLISHED` + VI markers
  (`Kết quả`, `Kết luận`, `Đã hoàn thành`, `Tóm lại`, `BÁO CÁO HOÀN THÀNH`) in its
  Strategy-1 markers, and prefers **ACCOMPLISHED over STILL MISSING** when both
  exist (done-work is the did_what, not the todo). Reuses `parse_output_sections`.
- **N1 TODO.md:** replace `read_text()[:3000]` — parse the append-log and keep the
  MOST RECENT status block (tail) + the unchecked plan items, not the stale head.
  Prefer `keep_tail` if parsing is unavailable.
- **H2 did_what notes:** `smart_truncate(notes, cap)` instead of `notes[:cap]`.
- **H1 next_turn_focus ×5:** put the LLM's original `_focus` FIRST (so a hard cap
  never eats it), directive after; and `smart_truncate` the whole to the cap.
- **N4 _force_synthesis:** `keep_head_tail(tc.output, …)` so the tail (final
  result of a command) survives into the conclusion-synthesis step.
- **H3 / M1:** `smart_truncate` for synth_text and epoch summary (word boundary +
  marker) instead of hard `[:4000]`.

No change to the already-correct paths (§1.2 "do not touch").

### Layer 3 — invariant + regression guard

- A short doctrine note in the module docstring: content destined for an agent's
  context or a persistent `_context/` file MUST go through `context_budget`, never
  a bare `[:N]` on agent-authored content.
- A canary test that scans the ~6 fixed sites for regression to `[:N]` on those
  specific expressions (targeted, not a blanket ban — safe preview `[:N]` stays).

---

## 4. Data flow (unchanged topology, safer nodes)

```
agent output ──parse_output_sections──► extract_did_what (ACCOMPLISHED-first) ──► LedgerEntry.did_what (smart_truncate)
                                    └──► key_data (unchanged)
LEDGER ──build_context_for_agent (section-tiered, allocate_tiered)──► agent system prompt
TODO append-log ──keep recent status + unchecked items──► previous_context
tool outputs ──keep_head_tail──► _force_synthesis ──► final agent conclusion
next_turn_focus (_focus first) ──smart_truncate──► DECISIONS / TODO / PROGRESS_TRACKER
```

`ACTIVITY.md` + `EPOCHS` remain the always-full recovery source; every elision
carries an `activity_search` pointer.

---

## 5. Testing

- **context_budget unit tests:** `smart_truncate` word-boundary; `keep_tail`
  keeps the end; `keep_head_tail` drops middle + marker; `parse_output_sections`
  on real EN+VI schema (incl. a block with only preamble); `allocate_tiered`
  never drops a conclusion-tier fragment, shrinks details oldest-first, emits a
  recovery line.
- **extract_did_what:** an output whose only heading is `## ACCOMPLISHED` returns
  the accomplished text (not STILL MISSING); VI `## Kết quả` recognised; existing
  Summary/Conclusion cases still pass (no regression).
- **N1:** a long append-log TODO yields the LATEST status block, not the stale head.
- **H1/H2/H3/M1:** over-cap inputs are cut on a word boundary with a marker, and
  H1 preserves the original `_focus`.
- **Regression:** the full suite (currently 1247) stays green.

---

## 6. Scope / non-goals

- NOT a relevance-scoring / embedding-ranked context selector (considered, deferred
  as YAGNI — semantic-section priority + recency is enough for the observed failures).
- NOT touching the safe `[:N]` sites (UI preview, logs, read-side budget).
- NOT changing `ACTIVITY.md`/`EPOCHS`/DECISIONS/FINAL_REPORT topology — they work.
- Domain knowledge stays out of the engine (per project guidelines).

---

## 7. Risk

Low–medium. Each site change is surgical and independently testable; the central
module is pure. Main risk is a `parse_output_sections` regex being too greedy/loose
— mitigated by EN+VI fixtures from the real runs and a preamble fallback that
returns text unchanged when no known section is present (fail-open, never worse
than today).
