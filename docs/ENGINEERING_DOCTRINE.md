# Engineering Doctrine

Working principles ported from the deer-flow harness review (2026-06). These
are about **how we work** — they constrain process, not any one module. They
complement the LLM Guidelines in `CLAUDE.md`.

---

## 1. Teeth test — a guard test must prove it can fail

A test that guards an invariant (security gate, regression, integrity check) is
only real if it has **teeth**: reintroduce the bug → the test goes RED; restore
the fix → it goes GREEN. A test that is green on the happy path but was never
seen to fail proves nothing.

**Apply when:** writing any regression test, security gate, or integrity check
(`ledger_verifier`, `secret_scanner`, `command_guard`, authorization gates).

**How:**
- After writing the test, deliberately break the code it guards and confirm RED.
- Restore and confirm GREEN. Only then is the guard trustworthy.
- This is the antidote to verification theater (see `MEMORY.md`:
  verification-theater-task-d8b78042) — a verifier that auto-passes is worse
  than none.

> Why: a guard that cannot fail gives false confidence. Most shipped-bug
> incidents trace to a "passing" test that never actually exercised the failure.

---

## 2. Comment-plane safety boundary for autonomous agents

An agent running unattended must be confined to **reversible** surfaces. A wrong
comment costs a correction; a wrong merge, deploy, or fund transfer costs far
more and may be irreversible.

**Apply when:** building self-maintenance / automation loops — `/bounty`,
auto-resume, evolution agent, any background agent acting without a human in the
loop.

**How:**
- Default autonomous actions to draft/comment/propose, not commit/merge/release.
- Irreversible or outward-facing actions (publish, pay, delete, push to a shared
  branch) require explicit, scoped authorization — re-confirm per context, do
  not treat one approval as standing.
- This is consistent with `CLAUDE.md` §4 (backups before destructive actions).

> Why: autonomy is safe in proportion to how reversible its blast radius is.

---

## 3. Confidence × Severity gate for critics and reviewers

A reviewer/critic agent that surfaces everything it notices is quickly ignored.
Gate output on **two independent axes**: confidence AND severity.

**Apply when:** any critic, evaluator, code-reviewer, or finding-emitter
(`evolution_agent`, `metacognition`, security review, audit passes).

**How:**
- Only emit/escalate a finding when it is **high-confidence AND at or above a
  severity threshold** (e.g. ≥ P2). "Nothing to report" means nothing crossed
  both bars — not merely "no P0".
- Everything below threshold goes to a private notes channel, not the main
  output. Keep signal high; noise gets the whole reviewer muted.

> Why: a single severity score conflates "I'm unsure" with "it's minor". Two
> axes separate them, so high-confidence-low-severity and low-confidence-
> high-severity are handled differently.

---

## 4. Reason about batches, not just artifacts

When fanning out parallel agents over independent items, the parallel workers
**cannot see each other's findings**. Independent results can be individually
correct yet collectively wrong: overlaps, ordering conflicts, composition risks.

**Apply when:** using `dispatching-parallel-agents`, multi-agent turns, batched
reviews, or any fan-out followed by integration.

**How:**
- Always end a fan-out with a **synthesis pass** that sees all results together
  and reasons about their interaction (dedupe, conflict, composition).
- Do not split blind: if items can interact (shared files, merge order, total
  count gates), a barrier + re-aggregation is required, not optional.
- Verified examples this session: parallel subagents comparing two codebases —
  their individual claims were re-verified against real code before synthesis;
  several "gaps" turned out to already exist (prompt-cache, dangling-tool-call
  repair). The synthesis pass caught what blind trust would have shipped.

> Why: the value of parallelism is coverage; the risk is incoherence. The
> synthesis pass converts N independent views into one consistent decision.

---

### Relationship to existing guidance
- `CLAUDE.md` — LLM Guidelines (think before coding, surgical changes, verify).
- `docs/HARNESS_ ENGINEERING.md` — harness-level engineering notes.
- These doctrines were distilled from `src/open-source/deer-flow` during the
  2026-06 hardening port; see `MEMORY.md`: deerflow-comparison-2026-06.
