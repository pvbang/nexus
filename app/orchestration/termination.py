"""Termination / completion policy for the orchestration loop.

Extracted from ``nexus_orchestrator`` (audit item C1: split the god-object).
This module holds the *pure* policy that decides when a task may stop:

  - the safety-ceiling constants (max turns, stagnation windows, min delta),
  - the keyword signals that classify a blocker as external vs actionable,
  - the two pure predicates ``is_externally_blocked_terminal`` and
    ``contradictions_block_completion``.

All functions here are pure (no LLM, no I/O) and are covered by
``tests/test_orchestrator_completion.py`` and
``tests/test_orchestration_fixes.py``. ``nexus_orchestrator`` re-exports these
names, so existing imports (e.g. ``from ...nexus_orchestrator import _MAX_TURNS``)
keep working unchanged.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Quality-based exit with SOFT SAFETY NET.
# Primary: Orchestrator LLM decides completion based on progress and stagnation.
# Safety net: Only triggers on TRUE stagnation (zero meaningful work), not slow progress.
# Complex tasks (security research, deep audits) may legitimately need 50-100+ turns.
_MAX_TURNS = 500  # Safety ceiling for runaway bugs only — NOT a task limit.
# FIX C1: 15 (was 25). Force complete after 15 turns of ZERO progress delta.
_MAX_STAGNANT_TURNS = 15
_MAX_STAGNANT_TURNS_SECURITY = 25  # Security/intel tasks get longer runway.
_MIN_PROGRESS_DELTA = 3  # Even 3% progress = still working, don't force-stop.

# Blocked-terminal short-circuit (audit fb1a7a3c). A blocked-by-design / safety-
# refusal task reaches Definition-of-Done almost immediately; once it stops moving
# we must NOT wait for the full stagnation window (15-25 turns) — that runway is
# what let a refusal loop re-issue the prohibited request 15 times. Require this
# many history points so a single turn-1 reading cannot trip it prematurely.
_BLOCKED_TERMINAL_MIN_HISTORY = 2

# Plateau (high-water-mark) detection — audit f188a4c4 (Group A).
# The stagnation gate (range < _MIN_PROGRESS_DELTA over a window) is defeated by
# an oscillation that keeps variance >= 3% (e.g. 88<->91). Plateau is variance-
# independent: it asks whether progress has reached a NEW high-water-mark recently.
# A loop that never exceeds its peak is converged-or-stuck regardless of wobble.
_MAX_PLATEAU_TURNS = 20  # non-security: turns since the last new peak → stop.
_MAX_PLATEAU_TURNS_SECURITY = 30  # security/intel tasks get a longer runway.
_PLATEAU_PEAK_EPSILON = 1  # a "new peak" must exceed the prior HWM by > this (pct).

# Per-kind veto limit — audit f188a4c4 (Group A). Each anti-theater gate veto is
# counted by KIND (needs_review/tautology/reexec_mismatch/numeric_conflict/
# contradiction). When the SAME kind vetoes this many times without progress
# reaching a new peak, the gate is stuck in a self-reinforcing loop (inject critic
# → "new" finding → re-veto); allow completion through to the reconciliation turn.
_MAX_VETOES_PER_KIND = 4

# Honest-termination ladder (audit Gói 1: A1 + A2).
# A1 — pipeline loop: the same assignment hash is warned once at 3 occurrences
#      (give the team a chance to change approach), then TERMINATED at 5 — or at
#      3 already if an agent was force-stopped (its approach is provably exhausted).
# A2 — evaluator unparseable: warned at 3 (kept as-is), then forced to an HONEST
#      completion at 5 rather than staying frozen at 50% forever.
_LOOP_REPEAT_TERMINATE_AT = 5
_EVAL_PARSE_FAIL_TERMINATE_AFTER = 5

# Evolution TERMINATE enforcement — audit 3c44a93f bug #4a. Evolution's
# "TERMINATE NOW" is advisory text the orchestrator can (and did) ignore for 6
# straight turns while burning budget. When this many CONSECUTIVE high-severity
# stagnating turns accumulate, force an HONEST halt — the advisory becomes a
# machine-readable ceiling. Counts are reset by the caller on any new progress
# high-water-mark, so a recovering task is never force-halted.
_CONSECUTIVE_TERMINATE_LIMIT = 3

# Barren-turn honest-halt — audit tri-model 2026-07-01 (E7). All three runs
# burned 5–9 consecutive turns where NO agent landed a single successful tool
# call (budget-death before first call / prose "done" / phantom tool_call markup).
# Every other loop gate keyed on progress/hash/severity, so a prose-only turn
# looked like activity and none tripped. This is an LLM-agnostic backstop: after
# this many consecutive fully-barren turns, force an HONEST halt. ANY one
# successful tool call (including read/search) resets the streak at the caller,
# so a legitimate multi-turn read/plan phase is never killed.
_BARREN_TURNS_HALT_AT = 4


def should_halt_for_barren_turns(consecutive_barren_turns: int) -> bool:
    """Whether a run of turns with ZERO successful tool calls must honestly halt.

    Pure policy (like ``should_terminate_for_loop``): the caller counts how many
    consecutive turns landed no successful tool call across the whole team and
    resets that count the moment any tool succeeds. Returns True at the ceiling.
    """
    return consecutive_barren_turns >= _BARREN_TURNS_HALT_AT


# Cross-turn semantic loop — audit f188a4c4 (Group A). The assignment-hash
# terminator keys on task_description[:200], which a critic varies each turn so
# the hash never repeats. This keys on the RESULT instead: when the same
# (role, normalized-outcome) recurs this many times, the team is re-producing the
# same finding under different task text — a loop the hash terminator misses.
_SEMANTIC_LOOP_TERMINATE_AT = 5


def normalize_outcome(output: str) -> str:
    """Reduce an agent output to a loop-detection key over its WHOLE content.

    Audit f188a4c4 follow-up (T7): keying on ``output[:120]`` risked a false
    positive — a fixed ≥120-char boilerplate header repeated across turns would
    collapse genuinely-different outputs to the same prefix and falsely trip the
    loop terminator. Hashing the full whitespace-collapsed, lower-cased text
    instead means two outputs match only when their ENTIRE content is the same
    (modulo whitespace/case) — exactly the "same result re-produced" signal the
    terminator wants, with no prefix-collision path. Empty/blank → "" (so empty
    turns don't all hash-collide into a spurious repeat).
    """
    if not output:
        return ""
    collapsed = " ".join(output.split()).lower()
    if not collapsed:
        return ""
    import hashlib

    return hashlib.md5(collapsed.encode("utf-8")).hexdigest()


def semantic_loop_terminal(role_outcome_history: list[tuple[str, str]]) -> bool:
    """Whether the latest (role, outcome) pair has recurred to the loop ceiling.

    ``role_outcome_history`` is ordered oldest→newest; each entry is
    ``(agent_role, normalized_outcome_summary)``. Returns True when the most
    recent pair appears at least ``_SEMANTIC_LOOP_TERMINATE_AT`` times. A blank
    outcome (``""`` from :func:`normalize_outcome`) never trips the terminator —
    an empty/missing agent output is not evidence of a repeated result.
    """
    if len(role_outcome_history) < _SEMANTIC_LOOP_TERMINATE_AT:
        return False
    latest = role_outcome_history[-1]
    if not latest[1]:
        return False
    return role_outcome_history.count(latest) >= _SEMANTIC_LOOP_TERMINATE_AT


# Role lock — audit f188a4c4 (Group A). 163 turns spawned ~90+ ad-hoc role names
# (mostly "finalizer / synthesis_agent" variants), abandoning the fixed team so
# knowledge never accumulated (the T1 Swerling finding was re-discovered at T160).
# Canonicalize any role to the nearest DECLARED team role by token overlap.
_ROLE_SYNONYMS = {
    "writer": ("writer", "synthesis", "synthesizer", "finalizer", "assembly",
               "assembler", "document", "editor", "report", "publish", "delivery"),
    "engineer": ("engineer", "radar", "rf", "sensor", "link-budget", "systems"),
    "critic": ("critic", "review", "reviewer", "qa", "audit", "verification",
               "refute", "judge"),
    "data_scientist": ("data_scientist", "estimation", "state-estimation", "kalman",
                        "filter", "statistician"),
    "developer": ("developer", "code", "simulation", "compute", "implementation"),
    "physicist": ("physicist", "electromagnetic", "propagation", "physics", "clutter"),
}


def canonicalize_role(role: str, team_roles: list[str]) -> str:
    """Map ``role`` to the nearest DECLARED team role (returns it unchanged if
    already declared). Falls back to token-overlap against ``_ROLE_SYNONYMS``;
    if nothing matches, returns the first team role."""
    if not team_roles:
        return role
    low = (role or "").lower()
    if role in team_roles:
        return role
    best_role, best_score = team_roles[0], 0
    for canon in team_roles:
        tokens = _ROLE_SYNONYMS.get(canon, (canon,))
        score = sum(1 for t in tokens if t in low)
        if score > best_score:
            best_role, best_score = canon, score
    return best_role


# Token Governor: per-task cumulative-effort ceiling. When the task has spent
# ~95% of task_token_budget, the orchestrator allows ONE synthesis turn then
# terminates honestly (reports the REAL progress %, never fakes completion).
_TOKEN_BUDGET_TERMINATE_REASON = "token_budget_exhausted"

# CC-7: below this progress, a bare "complete" is not trusted unless the caller
# supplies a justification (blocked-by-design / safety refusal / terminated_reason).
_LOW_PROGRESS_COMPLETE_CEILING = 80

# H1 (audit 2026-06-29): Evolution severity code-gate. The orchestrator prompt
# tells the LLM "if Evolution severity >= this you MUST NOT complete", but a
# prompt instruction is advisory — the radar task completed at 48% while its own
# reasoning admitted severity 0.8 forbade it. This makes the rule a hard gate:
# a 'complete' at high severity and not-near-done progress is blocked, UNLESS the
# task is a genuine blocked/refusal terminal (those complete via SAFETY CEILING 2c).
_EVOLUTION_SEVERITY_FORBID_COMPLETE = 0.8
# At/above this progress the work is essentially finished — severity must not trap
# a near-done task in an endless "one more turn" loop.
_SEVERITY_GATE_PROGRESS_CEILING = 95


# Headroom above the artifact-delivery ratio (mirrors the objective_pct + 10
# allowance): a task may report slightly above its delivered-artifact ratio for
# in-flight work, but never a full 100% while declared artifacts are missing.
_ARTIFACT_CAP_HEADROOM = 10


def artifact_delivery_cap(declared_count: int, present_count: int) -> int | None:
    """Ground-truth progress cap from CONTRACT artifacts that EXIST on disk.

    Task 3c44a93f bug #4c: the orchestrator reported 100% while only 1 of 7
    declared artifacts existed (the LLM marked never-run phases DONE). This caps
    the reported progress by the fraction of declared artifacts actually present,
    so a self-graded number nothing on disk backs can never reach 100%.

    Returns ``None`` when there are no declared artifacts (no cap applies — e.g.
    a pure-conversation task). Otherwise returns ``(present/declared)*100`` plus a
    small headroom, clamped to >= 100 only when all artifacts are present.
    """
    if declared_count <= 0:
        return None
    present = max(0, min(present_count, declared_count))
    ratio_pct = int((present / declared_count) * 100)
    if present >= declared_count:
        return 100
    return ratio_pct + _ARTIFACT_CAP_HEADROOM


# Structured self-verification verdicts. Prose ("7/7 ĐẠT / complete") is NOT
# trusted — that is the theater we catch; only a machine-shaped verdict counts.
_SELFVERIFY_RATIO_RE = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\s+PASS\b", re.IGNORECASE)
_SELFVERIFY_FAILCOUNT_RE = re.compile(r"\b(\d{1,3})\s+FAIL(?:ED|URES?)?\b", re.IGNORECASE)


def self_verification_cap(output_text: str) -> int | None:
    """Cap progress from the task's OWN structured verification verdict.

    The artifact-delivery cap proves a file EXISTS; it can't tell a PASSING
    deliverable from a FAILING one (rerun ea36dbc1: 100% shown while its own
    verify script returned 5/7 PASS). This reads the STRUCTURED verdict the agent
    already emitted and, if it reports failures, caps progress below 100%.

    Signals (conservative — completion gate):
      - ``N/M PASS`` with N<M  → cap = int(N/M*100) + headroom (worst ratio wins).
      - ``K FAIL`` with K>0    → cap = 90 (some named checks failed; not 100%).
    Prose claims with no structured verdict → None (fail-open, no cap). A clean
    verdict (``M/M PASS`` / ``0 FAIL``) is NOT a signal → None.
    """
    if not output_text:
        return None
    caps: list[int] = []
    for m in _SELFVERIFY_RATIO_RE.finditer(output_text):
        passed, total = int(m.group(1)), int(m.group(2))
        if 0 < total and passed < total:
            caps.append(int(passed / total * 100) + _ARTIFACT_CAP_HEADROOM)
    for m in _SELFVERIFY_FAILCOUNT_RE.finditer(output_text):
        if int(m.group(1)) > 0:
            caps.append(90)
    if not caps:
        return None
    return min(caps)


def lawful_refusal_progress(reason: str, progress_pct: int) -> int:
    """Report-time progress for a completion decision.

    H2 (audit 2026-06-29): a correct safety/authorization refusal is the
    Definition of Done for its branch, not "incomplete 50%". When the gate
    terminated BECAUSE of a lawful refusal (reason ``lawful_refusal``), report
    100% so the handoff reads "✅ correctly refused" instead of "HALTED 50%".
    Every other completion reason keeps its real progress untouched.
    """
    return 100 if reason == "lawful_refusal" else progress_pct


# Signals that a blocker is owned by an external party (owner / third-party),
# i.e. nothing the team can act on this turn.
_EXTERNAL_BLOCK_SIGNALS = (
    "outside agent control",
    "outside team control",
    "outside our control",
    "awaiting owner",
    "owner reply",
    "owner credential",
    "owner action",
    "third-party",
    "third party",
    "awaiting maintainer",
    "maintainer merge",
    "awaiting review",
    "pending merge",
    "web-only",
    "kyc",
)

# Multi-word phrases signalling a GENUINE safety/authorization/ownership refusal —
# the kind that must be treated as TERMINAL for that branch (audit CC-5, task
# eb5c09bd: a safety refusal was reframed as stagnation and "broken through" into
# a live unauthorized attack). Deliberately multi-word + anchored to the
# ownership/authorization/safety frame so an ordinary network "connection refused"
# or HTTP 403 never trips it (the bare-keyword trap). EN + VI.
_SAFETY_REFUSAL_SIGNALS = (
    # First-person / decision-bearing refusals — the AGENT declining to act. These
    # are anchored to a refusal VERB so an objective finding that merely DESCRIBES a
    # target (e.g. "endpoint reachable without authorization", "accessible without
    # authorization") is NOT misread as the agent refusing (audit follow-up: that
    # false-positive wrongly marked a turn blocked-by-design and let CC-7 complete).
    "refuse to scan",
    "refuse to attack",
    "refuse to exploit",
    "refuse to proceed",
    "refuse: the user does not own",
    "does not own this target",
    "does not own the target",
    "not authorized to attack",
    "not authorized to scan",
    "cannot proceed without authorization",
    "proceeding requires authorization",
    "ownership cannot be verified",
    "cannot verify ownership",
    "declining on safety",
    "declining: this is third-party",
    "declining this request",
    "refusing on safety",
    "decline for safety",
    "this request is not authorized to attack",
    # Decision-bearing terminal markers + ownership/authorization anchors that real
    # refusals use (audit fb1a7a3c: these phrasings looped 15 turns because none of
    # the above matched). Each stays anchored to a refusal/authorization frame so an
    # objective finding that merely DESCRIBES a target never trips it.
    "terminal refusal",
    "safety refusal is the correct terminal",
    "will not perform reconnaissance",
    "no per-target written authorization",
    "no authorization on file",
    "standing authorization does not cover",
    "proceed only with per-target proof",
    # Vietnamese
    "từ chối vì an toàn",
    "từ chối quét",
    "từ chối tấn công",
    "từ chối terminal",
    "không sở hữu hạ tầng",
    "không được phép tấn công",
    "không thể xác minh quyền sở hữu",
    "chưa được ủy quyền tấn công",
    "chưa có ủy quyền",
    "không nhắm vào hạ tầng quan trọng của bên thứ ba",
)


def refusal_is_terminal(text: str) -> bool:
    """Whether ``text`` expresses a genuine safety/authorization/ownership refusal.

    A safety refusal is a VALID terminal outcome for its branch — the orchestrator
    must NOT treat it as a stagnation to "break through" (metacognitive override)
    nor a model error to route around. Anchored to multi-word ownership/auth/safety
    phrases so a network 'connection refused' / HTTP 403 / ordinary work is never
    misread as a safety refusal (which would wrongly terminate a legitimate task).
    """
    if not text:
        return False
    low = text.lower()
    return any(sig in low for sig in _SAFETY_REFUSAL_SIGNALS)


# Verbs that indicate the next focus is genuinely actionable in-team work.
_ACTIONABLE_FOCUS_VERBS = (
    "fix", "implement", "build", "create", "write", "run", "execute",
    "install", "deploy", "test", "scan", "exploit", "investigate", "refactor",
    "add", "patch", "publish", "push", "code",
)


def is_externally_blocked_terminal(
    missing_items: list[str],
    next_turn_focus: str,
    stagnant: bool,
) -> bool:
    """Decide whether the task has reached a genuine terminal state.

    Returns True only when ALL of these hold:
      - progress is stagnant (no meaningful movement), AND
      - every remaining missing_item is an EXTERNAL blocker (owner/third-party),
        AND
      - the next focus is NOT an actionable in-team step.

    In that situation, spawning more agents only produces duplicate "what to do
    if unblocked" docs (the exact doc-churn loop seen in task 2dc763a5). The
    correct move is to stop and let monitoring/resume triggers handle it.
    """
    if not stagnant:
        return False

    focus_low = (next_turn_focus or "").strip().lower()
    # If the next focus starts with an actionable verb, there is real work left.
    first_word = focus_low.split()[0].rstrip(":") if focus_low else ""
    if first_word in _ACTIONABLE_FOCUS_VERBS:
        return False

    items = [m for m in (missing_items or []) if m and m.strip()]
    if not items:
        # Stagnant with nothing concrete left and no actionable focus → terminal.
        return True

    # Every remaining item must be an external blocker.
    for item in items:
        low = item.lower()
        if not any(sig in low for sig in _EXTERNAL_BLOCK_SIGNALS):
            return False
    return True


def should_terminate_for_loop(occurrences: int, has_force_stopped: bool) -> bool:
    """Decide whether a repeating pipeline assignment has become a dead loop.

    The orchestrator warns once when the same assignment hash recurs (3x) by
    prepending a "change your approach" directive. If the loop persists past
    that warning, continuing only reproduces the infinite-loop failure mode the
    task brief forbids — so terminate the task (honest stop) instead.

    Returns True when EITHER:
      - the assignment has repeated to the hard ceiling (``_LOOP_REPEAT_TERMINATE_AT``),
        i.e. the post-warning chance was wasted, OR
      - it has repeated at least to the warn rung (3) AND an agent was
        FORCE-STOPPED this round — a killed-for-stuck agent means the approach is
        provably exhausted, so reassigning it is hopeless; stop one rung earlier.
    """
    if occurrences >= _LOOP_REPEAT_TERMINATE_AT:
        return True
    if occurrences >= 3 and has_force_stopped:
        return True
    return False


def is_stagnant_range(
    progress_history: list[int] | None, window: int, min_delta: int
) -> bool:
    """Whether the last ``window`` progress points span less than ``min_delta``.

    A2 (arch review 2026-07-03): this exact ``max(recent) - min(recent) < min_delta``
    computation was copy-pasted in the completion gate (window 15/25), the
    terminal-block check (window 3), and TaskHealth. Unifying the PRIMITIVE here —
    while each caller passes ITS OWN window — is DRY without changing any verdict.
    Fewer than ``window`` points → not stagnant (mirrors the callers' original
    ``len(progress_history) >= window`` guard; never a false alarm on thin data).
    """
    if not progress_history or len(progress_history) < window:
        return False
    recent = progress_history[-window:]
    return (max(recent) - min(recent)) < min_delta


def turns_since_new_peak(progress_history: list[int]) -> int:
    """How many of the most-recent turns failed to set a NEW progress peak.

    A "new peak" is a value exceeding the running high-water-mark by more than
    ``_PLATEAU_PEAK_EPSILON``. Returns the count of trailing turns that did not
    establish such a peak. 0 when the latest turn set a new peak (still climbing).
    Variance below the peak does not reset the counter — that is the whole point.
    """
    if not progress_history:
        return 0
    hwm = progress_history[0]
    last_peak_idx = 0
    for i in range(1, len(progress_history)):
        if progress_history[i] > hwm + _PLATEAU_PEAK_EPSILON:
            hwm = progress_history[i]
            last_peak_idx = i
    return (len(progress_history) - 1) - last_peak_idx


def plateau_reached(
    progress_history: list[int],
    is_security_task: bool = False,
) -> bool:
    """Whether progress has plateaued: no new peak for the plateau window.

    Distinct from the stagnation gate — plateau cannot be defeated by an
    oscillation that keeps the progress RANGE >= _MIN_PROGRESS_DELTA, because it
    keys off reaching a NEW high-water-mark, not off variance.
    """
    limit = _MAX_PLATEAU_TURNS_SECURITY if is_security_task else _MAX_PLATEAU_TURNS
    # Need at least `limit` trailing non-peak turns AND enough history to judge.
    if len(progress_history) <= limit:
        return False
    return turns_since_new_peak(progress_history) >= limit


def veto_limit_reached(count: int) -> bool:
    """Whether a single veto KIND has fired enough times to be a stuck loop."""
    return count >= _MAX_VETOES_PER_KIND


def contradictions_block_completion(
    contradictions: list[str],
    llm_decision: str,
) -> bool:
    """Block 'complete' whenever ANY unresolved contradiction exists.

    Previously only contradictions containing 'constraint/invariant/drift/violat'
    blocked completion — so a plain factual contradiction (e.g. "AICrowd LIVE vs
    DEAD") slipped through and the task could complete on contradictory evidence.
    Any unresolved contradiction now forces one more turn to resolve it against
    ground truth.
    """
    if llm_decision != "complete":
        return False
    return bool([c for c in (contradictions or []) if c and c.strip()])


class CompletionDecision(NamedTuple):
    """Result of the completion gate.

    ``reason`` is a stable code describing WHY the loop stops/continues, so the
    caller can emit the appropriate structured log without re-deriving anything:
      - "max_turns"            → hit the absolute turn ceiling
      - "stagnation"           → progress flat for the stagnation window
      - "plateau"              → no new progress peak for the plateau window
      - "climbing"             → LLM said complete but progress still climbing
      - "llm_decision"         → trusting the LLM's complete/continue verdict
    ``detail`` carries the numbers relevant to ``reason`` for logging.
    """

    complete: bool
    reason: str
    detail: dict


def evaluate_completion(
    llm_decision: str,
    progress_pct: int,
    turn_count: int,
    progress_history: list[int],
    prev_progress: int,
    is_security_task: bool = False,
    has_blocked_justification: bool = False,
    evolution_severity: float = 0.0,
    is_safety_refusal: bool = False,
    consecutive_terminate_signals: int = 0,
    consecutive_barren_turns: int = 0,
) -> CompletionDecision:
    """Pure completion gate — decides whether the task loop may stop.

    Extracted from ``NexusOrchestrator._determine_completion`` (audit C1/C2) so
    the rule is testable without an orchestrator instance and the per-task state
    is passed in explicitly rather than read off ``self``.

    The Orchestrator LLM has authority to decide completion; this only applies
    minimal guardrails plus hard safety ceilings that prevent infinite loops.
    """
    # SAFETY CEILING 1: absolute turn limit.
    if turn_count >= _MAX_TURNS:
        return CompletionDecision(True, "max_turns", {"turns": turn_count})

    # SAFETY CEILING 1b: Evolution TERMINATE enforcement (audit 3c44a93f bug #4a).
    # A run of consecutive high-severity stagnating turns is Evolution telling the
    # loop to stop. The orchestrator ignored that advice for 6 turns; make it a
    # hard ceiling. "evolution_terminate" routes to an HONEST HALTED handoff (real
    # progress, never a faked 100%). The caller resets the count on any new
    # high-water-mark, so a recovering task never trips this.
    if consecutive_terminate_signals >= _CONSECUTIVE_TERMINATE_LIMIT:
        return CompletionDecision(
            True,
            "evolution_terminate",
            {"consecutive_terminate_signals": consecutive_terminate_signals,
             "progress_pct": progress_pct},
        )

    # SAFETY CEILING 1c: barren turns (E7, audit tri-model 2026-07-01). N
    # consecutive turns landed ZERO successful tool calls across the whole team —
    # budget-death, prose-only "done", or phantom tool_call markup. The other
    # gates key on progress/hash/severity and miss this because prose looks like
    # activity. Force an HONEST halt (real progress, partial handoff) rather than
    # dispatch yet another barren turn. The caller resets the streak on ANY tool
    # success, so a legitimate read/plan phase never trips this.
    if should_halt_for_barren_turns(consecutive_barren_turns):
        return CompletionDecision(
            True,
            "barren_no_production",
            {"consecutive_barren_turns": consecutive_barren_turns,
             "progress_pct": progress_pct},
        )

    # SAFETY CEILING 2: stagnation — same progress for too many turns.
    # Non-security tasks use a shorter window than security/intel tasks.
    stagnant_limit = _MAX_STAGNANT_TURNS_SECURITY if is_security_task else _MAX_STAGNANT_TURNS
    # A2: shared range-stagnation primitive (same rule as before, one definition).
    if is_stagnant_range(progress_history, stagnant_limit, _MIN_PROGRESS_DELTA):
        recent = progress_history[-stagnant_limit:]
        return CompletionDecision(
            True,
            "stagnation",
            {
                "turns_stagnant": stagnant_limit,
                "progress_range": max(recent) - min(recent),
                "current_pct": progress_pct,
            },
        )

    # SAFETY CEILING 2c: blocked-justified AND no longer moving → terminal.
    # Audit fb1a7a3c: a genuine safety/authorization refusal (or any blocked-by-
    # design objective) reaches Definition-of-Done at turn 1-2 and sits at a STABLE
    # high progress. The orchestrator LLM kept emitting decision="continue" — re-
    # issuing the prohibited request under reframings — so trusting llm_decision
    # looped 15 turns. A 100%-stable, blocked-justified task is the EXPECTED terminal
    # signature, NOT a stagnation to "break through". When blocked AND progress has
    # not advanced since the previous turn, stop regardless of the LLM decision.
    # (Still moving → don't force-stop: the team may be producing the lawful
    # alternative deliverable. Needs >= 2 history points so a turn-1 blip can't fire.)
    if (
        has_blocked_justification
        and len(progress_history) >= _BLOCKED_TERMINAL_MIN_HISTORY
        and (progress_pct - prev_progress) < _MIN_PROGRESS_DELTA
    ):
        return CompletionDecision(
            True,
            # H2 (audit 2026-06-29) + H2a fix: a GENUINE safety/authorization
            # refusal is the Definition of Done for its branch — name it
            # "lawful_refusal" so the orchestrator reports it at 100%
            # ("✅ correctly refused"). But a task that is merely blocked for
            # another reason (infeasible / external owner action / blocked
            # objective) is NOT a refusal — keep "blocked_terminal" and its REAL
            # progress so it is not mislabeled "correctly refused" and inflated
            # to 100%.
            "lawful_refusal" if is_safety_refusal else "blocked_terminal",
            {
                "progress_pct": progress_pct,
                "progress_delta": progress_pct - prev_progress,
                "turn_count": turn_count,
            },
        )

    # SAFETY CEILING 2b: plateau — no NEW progress peak for the plateau window.
    # Catches oscillations the stagnation range-check misses (88<->91 keeps range
    # >= 3 forever). Variance-independent: keys off reaching a new high-water-mark.
    if plateau_reached(progress_history, is_security_task=is_security_task):
        return CompletionDecision(
            True,
            "plateau",
            {
                "turns_since_new_peak": turns_since_new_peak(progress_history),
                "hwm": max(progress_history) if progress_history else 0,
                "current_pct": progress_pct,
            },
        )

    # H1 (audit 2026-06-29): Evolution severity code-gate. A 'complete' while
    # Evolution severity is high and the task is not near-done is the desync that
    # let the radar task complete at 48% (its reasoning admitted "severity 0.8
    # forbids complete" yet it completed anyway). Enforce the rule the prompt only
    # advised. A genuine blocked/refusal terminal already returned above via
    # SAFETY CEILING 2c, so this never traps a lawful refusal.
    if (
        llm_decision == "complete"
        and evolution_severity >= _EVOLUTION_SEVERITY_FORBID_COMPLETE
        and progress_pct < _SEVERITY_GATE_PROGRESS_CEILING
        and not has_blocked_justification
    ):
        return CompletionDecision(
            False,
            "evolution_severity_block",
            {"evolution_severity": evolution_severity, "progress_pct": progress_pct},
        )

    # If progress is climbing significantly and the LLM says complete early,
    # give it one more turn (but don't override if it insists next turn).
    progress_delta = progress_pct - prev_progress
    if progress_delta > _MIN_PROGRESS_DELTA and llm_decision == "complete" and progress_pct < 95:
        return CompletionDecision(
            False,
            "climbing",
            {"progress_pct": progress_pct, "progress_delta": progress_delta},
        )

    # CC-7: a 'complete' at LOW STATIC progress must carry a reason. Closing a task
    # at 10%/33% with no "blocked-by-design"/refusal/terminated_reason justification
    # is the 7688c9cc / 367a9793 pathology — the symmetric counterpart to the
    # "climbing" guard above. With a justification (genuinely blocked, safe refusal,
    # honest halt) a low-progress completion is legitimate and allowed.
    if (
        llm_decision == "complete"
        and progress_pct < _LOW_PROGRESS_COMPLETE_CEILING
        and not has_blocked_justification
    ):
        return CompletionDecision(
            False,
            "low_progress_complete_unjustified",
            {"progress_pct": progress_pct},
        )

    # Trust the Orchestrator's decision.
    return CompletionDecision(llm_decision == "complete", "llm_decision", {})
