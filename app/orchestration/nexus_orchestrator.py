"""
MekongAI Nexus - Nexus Orchestrator.

IMPROVEMENT: Never settle for 'good enough' - push for excellence.

The Master Agent that plans tasks, selects agents, evaluates progress,
and coordinates the entire execution lifecycle. Strict evaluation that
refuses to mark tasks complete until quality is EXCELLENT.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

import structlog

from app.orchestration.models import (
    AgentAssignment,
    EvaluationResult,
    ExecutionState,
    ToolCall,
    TurnPlan,
    TurnResult,
)

if TYPE_CHECKING:
    from app.orchestration.evolution_agent import EvolutionInsight

from app.orchestration.prompts import (
    build_contract,
    build_orchestrator_evaluation_prompt,
    build_orchestrator_planning_prompt,
    build_progress_tracker,
    build_roe,
    build_task_brief,
    build_todo,
)

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# Wide safety backstop for injecting DECISIONS.md into the Orchestrator prompt.
# The writer already bounds the file to the last 3 turns; under normal conditions
# those entries are returned in full. This ceiling (~4K tokens) only engages for a
# pathological single turn and then drops the OLDEST whole entries — never cuts an
# entry mid-content (which would make prior conclusions look unfinished).
_DECISIONS_READ_BACKSTOP = 16000

from app.core import task_paths

# Termination / completion policy lives in a dedicated module (audit C1: split the
# god-object). Re-exported here so existing imports keep working unchanged.
from app.orchestration.termination import (  # noqa: F401  (re-exported)
    _ACTIONABLE_FOCUS_VERBS,
    _CONSECUTIVE_TERMINATE_LIMIT,
    _EVAL_PARSE_FAIL_TERMINATE_AFTER,
    _EVOLUTION_SEVERITY_FORBID_COMPLETE,
    _EXTERNAL_BLOCK_SIGNALS,
    _LOOP_REPEAT_TERMINATE_AT,
    _MAX_PLATEAU_TURNS,
    _MAX_PLATEAU_TURNS_SECURITY,
    _MAX_STAGNANT_TURNS,
    _MAX_STAGNANT_TURNS_SECURITY,
    _MAX_TURNS,
    _MAX_VETOES_PER_KIND,
    _MIN_PROGRESS_DELTA,
    _PLATEAU_PEAK_EPSILON,
    _SEMANTIC_LOOP_TERMINATE_AT,
    canonicalize_role,
    contradictions_block_completion,
    evaluate_completion,
    is_externally_blocked_terminal,
    is_stagnant_range,
    lawful_refusal_progress,
    normalize_outcome,
    plateau_reached,
    refusal_is_terminal,
    semantic_loop_terminal,
    should_terminate_for_loop,
    turns_since_new_peak,
    veto_limit_reached,
)
from app.orchestration.progress_tracker import ProgressTracker
from app.orchestration.json_parse import parse_json_response
from app.orchestration.text_budget import smart_truncate as _smart_truncate


# ─────────────────────── Assignment / completion helpers ───────────────────────
# Pure functions (no LLM, no I/O) so they are unit-testable and reusable.


def derive_success_criteria(plan_data: dict[str, Any]) -> str:
    """Build a MEASURABLE success bar for the GOAL_ANCHOR from the planner output.

    Audit A7: the old GOAL_ANCHOR read a non-existent top-level ``success_criteria``
    key and fell back to echoing the user's prompt, so the orchestrator had no real
    completion bar. The planner already emits a ``deliverable_contract`` with a
    ``definition_of_done`` and per-artifact ``verify`` steps ("how to PROVE it works,
    not just file exists") — that is the measurable bar. Use it.

    Priority: definition_of_done + each artifact's "<what> — <verify>". If there is
    no contract, fall back to ``intent_analysis``. If there is nothing measurable,
    return "" — the caller stays HONEST ("no measurable criteria") rather than
    pasting the raw prompt back as if it were a criterion.
    """
    contract = plan_data.get("deliverable_contract")
    if isinstance(contract, dict):
        parts: list[str] = []
        dod = str(contract.get("definition_of_done", "") or "").strip()
        if dod:
            parts.append(f"Done when: {dod}")
        for art in contract.get("artifacts", []) or []:
            if not isinstance(art, dict):
                continue
            what = str(art.get("what", "") or "").strip()
            verify = str(art.get("verify", "") or "").strip()
            if what and verify:
                parts.append(f"{what} — verify: {verify}")
            elif verify:
                parts.append(f"verify: {verify}")
            elif what:
                parts.append(what)
        if parts:
            return " | ".join(parts)

    return str(plan_data.get("intent_analysis", "") or "").strip()


# Soft cap for the SUCCESS WHEN line. Large enough for a multi-artifact BUILD
# contract's verify steps; trimmed at a " | " boundary so a verify step is never
# cut mid-sentence (audit A7 review).
_GOAL_ANCHOR_SUCCESS_CAP = 1000
# The USER-ASKED line in the anchor: keep the intent readable at a real budget, not
# a ~300-char stub that dropped a long, detailed request (Bug B, 2026-07-01). The
# FULL request always lives verbatim in TASK_BRIEF; when the request exceeds this
# budget the anchor appends a pointer so the agent knows to read it.
_GOAL_ANCHOR_MESSAGE_CAP = 2000


def render_goal_anchor(
    message: str,
    plan_data: dict[str, Any],
    success_criteria: str | None = None,
) -> str:
    """Render the GOAL_ANCHOR text (pure — no I/O, no LLM).

    ``success_criteria`` lets the resume path pass the value restored from the
    checkpoint directly (resume has no fresh ``plan_data``); when None, it is
    derived from ``plan_data``. Empty criteria render an HONEST line rather than
    echoing the prompt (audit A7).
    """
    from app.orchestration.ledger_extract import smart_truncate

    success = success_criteria if success_criteria is not None else derive_success_criteria(plan_data)
    success = (success or "").strip()
    if len(success) > _GOAL_ANCHOR_SUCCESS_CAP:
        # Trim at the last " | " boundary that fits, so no verify step is half-shown.
        cut = success.rfind(" | ", 0, _GOAL_ANCHOR_SUCCESS_CAP)
        success = (success[:cut] if cut > 0 else success[:_GOAL_ANCHOR_SUCCESS_CAP]).rstrip() + " | …"
    # USER ASKED: keep the intent readable at a real budget (was 300 chars, which
    # reduced a long multi-deliverable request to a 2-line stub — Bug B). When the
    # request is longer than the budget, point to TASK_BRIEF, which holds it verbatim,
    # so the agent knows the anchor is a summary and reads the full request there.
    _msg = (message or "").strip()
    if len(_msg) > _GOAL_ANCHOR_MESSAGE_CAP:
        _asked = smart_truncate(_msg, _GOAL_ANCHOR_MESSAGE_CAP)
        _asked += "\n(This is a summary — the FULL request is in TASK_BRIEF; read it via task_doc_read(doc=\"TASK_BRIEF\") for every detail.)"
    else:
        _asked = _msg
    head = (
        "🎯 GOAL ANCHOR (NEVER deviate from this):\n"
        f"USER ASKED: {_asked}\n"
    )
    if success:
        return head + f"SUCCESS WHEN: {success}\n"
    return head + (
        "SUCCESS WHEN: (no measurable criteria derived — "
        "verify delivered work against USER ASKED above)\n"
    )

# Similarity primitives live in a dedicated module (audit C1: split the
# god-object) so they can be reused without importing the whole orchestrator.
# Re-exported here so existing imports keep working unchanged.
from app.orchestration.task_similarity import (  # noqa: E402, F401  (re-exported)
    _INJECTED_PREFIX_MARKERS,
    _normalize_task_text,
    _task_similarity,
    assignment_loop_hash,
)


def dedupe_assignments(
    assignments: list["AgentAssignment"],
    threshold: float = 0.8,
) -> tuple[list["AgentAssignment"], list["AgentAssignment"]]:
    """Collapse near-identical assignments within a SINGLE turn.

    Prevents the failure mode where the planner gives two agents (e.g.
    money_maker + revenue_strategist) the same work — producing double Telegram
    escalations and duplicate contingency docs. First occurrence wins; later
    near-duplicates are dropped regardless of agent_role (the point is that the
    WORK is redundant, not the role).

    Returns (kept, dropped).
    """
    kept: list[AgentAssignment] = []
    dropped: list[AgentAssignment] = []
    for a in assignments:
        is_dup = any(
            _task_similarity(a.task_description, k.task_description) >= threshold
            for k in kept
        )
        if is_dup:
            dropped.append(a)
        else:
            kept.append(a)
    return kept, dropped


# ROOT-D: escalate after this many consecutive unparseable orchestrator evaluations.
_EVAL_PARSE_FAIL_ESCALATE_AFTER = 3


# Roles whose job is to PRODUCE the written deliverable (vs. analyze/verify it).
# Audit c527e1c2: a missing report needs a WRITER, not another critic/analyst.
_WRITER_ROLES = ("writer", "technical_writer", "editor", "synthesizer")
# Roles whose "completed" claim MUST be backed by production this turn (E5, run
# 68c276ac). These build/write/execute deliverables, so a self-graded "done" with
# nothing on disk is a phantom completion. Analysis/verify roles (critic, analyst,
# researcher, judge, reviewer, qa, project_manager) legitimately finish a turn
# without producing and are deliberately EXCLUDED — never downgraded.
_PRODUCER_ROLES = frozenset({
    "writer", "technical_writer", "editor", "synthesizer",
    "engineer", "developer", "data_scientist", "architect", "designer",
})
# Words in an outstanding-gap description that signal the gap is a missing
# WRITTEN artifact (so a starved writer should lead rotation priority).
_REPORT_GAP_SIGNALS = (
    "report", "document", "deliverable", "write", "consolidat", "section",
    "báo cáo", "tài liệu", "viết", "tổng hợp",
)


def is_progress_stagnant(
    self_progress: list[int] | None,
    capped_progress: list[int] | None,
    min_delta: int = 5,
) -> bool:
    """Whether progress is stuck, reading the SELF-graded estimate (pre-cap).

    E-R5 (tri-run audit 2026-07-02): a single progress number carried two meanings —
    self-graded ("how much the agent thinks is done") and ground-truth-capped ("how
    much is verifiably on disk"). The stagnation gate read the CAPPED number, so an
    artifact-cap that pinned progress low (gasifier: 10% while ~90% real) looked
    identical to a genuinely stuck task and drove runaway busywork.

    This reads the self-progress history (pre-cap) so a low-pinning cap can no longer
    fake stagnation, while a genuinely flat self-estimate (deepseek: stuck at ~20%)
    is still caught. Falls back to the capped history when self-progress is
    unavailable (turn 1 / resume without the new store) so behavior never regresses.

    Stagnant := over the last 3 points, max-min < ``min_delta``. Fewer than 3 points
    → not stagnant (never a false alarm on thin data). A2: delegates the range check
    to the shared ``is_stagnant_range`` primitive (window 3) — one definition.
    """
    series = self_progress if self_progress else capped_progress
    return is_stagnant_range(series, 3, min_delta)


def build_mandatory_directive_text(
    suggestion: str, severity: float, stagnant_turns: int
) -> str:
    """Render a HIGH-PRIORITY Evolution suggestion for the orchestrator to weigh.

    Hướng B (tri-run audit 2026-07-02, owner-approved): Evolution ADVISES, the
    orchestrator DECIDES. This used to render a coercive "MANDATORY DIRECTIVE — you
    MUST include this as the PRIMARY assignment; agents MUST NOT analyze, must
    EXECUTE", which stripped the orchestrator's judgement and turned a raw
    suggestion into an executable order (on the deepseek run that order was
    "output a plausible list ... 'found' data" — a fabrication). It now surfaces the
    idea with high priority so a stuck task doesn't ignore it, but leaves the
    orchestrator to decide whether/how to turn it into next_assignments.

    Still carries the anti-fabrication guardrail AND scrubs a fabrication-shaped
    suggestion (E-R10) so the orchestrator never even reads an order to fake data.
    """
    from app.orchestration.evolution_agent import (
        _ANTI_FABRICATION_GUARDRAIL,
        scrub_fabrication_directive,
    )

    # E-R10: scrub a fabrication-shaped suggestion before it reaches the prompt, so
    # the orchestrator cannot be nudged toward manufacturing data.
    suggestion = scrub_fabrication_directive(suggestion)

    return (
        f"\n\n## 🧬 High-Priority Evolution Suggestion\n"
        f"**Severity {severity:.1f} + stagnating for "
        f"{stagnant_turns}+ turns** — the current approach is not working, so weigh "
        f"this idea seriously when you decide next_assignments:\n\n"
        f"> {suggestion}\n\n"
        f"You are the decision-maker: consider this suggestion (it is a strong "
        f"priority, not an order), and if you adopt it, translate it into a concrete "
        f"assignment with a REAL, verifiable action — or choose a better real approach "
        f"and say why. Do NOT copy it verbatim if it does not fit.\n"
        f"\n⛔ {_ANTI_FABRICATION_GUARDRAIL}\n"
    )


def _gap_priority(outstanding_gap: str) -> tuple[str, ...]:
    """Pick the rotation priority order from WHAT is missing.

    A missing written report → a writer-type role leads (the gap is unwritten
    content, not unverified content). Anything else → critic leads (the default
    independent-perspective bias from the d8b78042 audit). This replaces the old
    hardcoded ``("critic",)`` that could never route to the one role a stuck
    documentation task actually needs (audit c527e1c2)."""
    low = (outstanding_gap or "").lower()
    if low and any(sig in low for sig in _REPORT_GAP_SIGNALS):
        return _WRITER_ROLES + ("critic",)
    return ("critic",)


def enforce_role_rotation(
    proposed_roles: list[str],
    recent_turn_roles: list[list[str]],
    team_roles: list[str],
    *,
    dominance_window: int = 2,
    priority_fresh: tuple[str, ...] | None = None,
    starvation_window: int = 0,
    outstanding_gap: str = "",
) -> tuple[list[str], str | None, str]:
    """Break agent monoculture/starvation by forcing a neglected role in next turn.

    Audit of task d8b78042: one role (physicist) ran turns 2→9 while critic /
    engineer / writer / developer never executed once — so nobody independently
    challenged the work and a false-positive spiral set in. The planner's LLM is
    biased toward "the agent who already has context", and nothing counterbalanced
    that. This pure function is that counterbalance.

    Audit of task c527e1c2 added two failure modes the original missed:
      1. ANALYTICAL ALTERNATION — engineer/critic alternated for 11 turns while the
         writer NEVER ran. The 2-turn dominance check saw no single dominant role
         (critic≠engineer) so it stayed blind, and the deliverable's report (which
         only the writer produces) was never written. ``starvation_window`` (when
         >0) catches a role that ran ZERO times across a WIDE window even with no
         single-role monoculture.
      2. WRONG PRIORITY — priority was hardcoded to critic, so even when rotation
         did fire it injected a critic, never the writer the stuck task needed.
         ``outstanding_gap`` now steers priority: a missing report → writer leads.

    Args:
        proposed_roles: roles the evaluator wants to run next turn.
        recent_turn_roles: roles that actually ran in recent turns, oldest→newest.
        team_roles: the full assigned team.
        dominance_window: a role is "dominant" if it ran in each of the last N turns.
        priority_fresh: explicit injection priority; when None it is derived from
            ``outstanding_gap`` (report gap → writer-first, else critic-first).
        starvation_window: when >0, also rotate if a team role ran ZERO times in the
            last N turns (catches analytical alternation that dodges dominance).
        outstanding_gap: short description of what the deliverable is still missing;
            steers priority (report/document gap → writer leads).

    Returns:
        (adjusted_roles, injected_role | None, reason). When no rotation is needed
        the input is returned unchanged with ``injected_role=None``.
    """
    if not team_roles or len(team_roles) < 2:
        return proposed_roles, None, ""

    priority = priority_fresh if priority_fresh is not None else _gap_priority(outstanding_gap)

    def _swap(inject: str, fresh_roles: list[str], reason: str):
        # Don't intervene if a fresh perspective is already coming on its own.
        if proposed_roles and any(r in fresh_roles for r in proposed_roles):
            return proposed_roles, None, ""
        adjusted = list(proposed_roles)
        if adjusted:
            adjusted[-1] = inject  # swap one stale slot — keeps agent count stable
        else:
            adjusted = [inject]
        return adjusted, inject, reason

    # ── Mode 1: pure single-role monoculture over the short dominance window ──
    window = [t for t in recent_turn_roles[-dominance_window:] if t]
    if len(window) >= dominance_window:
        ran_each_turn = set(window[0])
        for turn_roles in window[1:]:
            ran_each_turn &= set(turn_roles)
        seen_recently = {r for turn_roles in window for r in turn_roles}
        fresh_roles = [r for r in team_roles if r not in seen_recently]
        if ran_each_turn and fresh_roles:
            inject = next((r for r in priority if r in fresh_roles), fresh_roles[0])
            reason = (
                f"role rotation: '{'/'.join(sorted(ran_each_turn))}' ran the last "
                f"{dominance_window} turns while {fresh_roles} never ran — injecting "
                f"'{inject}' for an independent perspective"
            )
            return _swap(inject, fresh_roles, reason)

    # ── Mode 2: starvation across a wide window (analytical alternation) ──
    # A role that ran ZERO times across the wider window is starved even when no
    # single role dominates the 2-turn check. Catches the c527e1c2 pattern.
    if starvation_window and starvation_window >= 1:
        wide = [t for t in recent_turn_roles[-starvation_window:] if t]
        if len(wide) >= starvation_window:
            seen_wide = {r for turn_roles in wide for r in turn_roles}
            starved = [r for r in team_roles if r not in seen_wide]
            if starved:
                inject = next((r for r in priority if r in starved), starved[0])
                reason = (
                    f"role starvation: {starved} never ran in the last "
                    f"{starvation_window} turns (analytical alternation) — injecting "
                    f"'{inject}'"
                    + (f" to address gap: {outstanding_gap[:80]}" if outstanding_gap else "")
                )
                return _swap(inject, starved, reason)

    return proposed_roles, None, ""


# _EXTERNAL_BLOCK_SIGNALS, _ACTIONABLE_FOCUS_VERBS, is_externally_blocked_terminal,
# and contradictions_block_completion now live in app.orchestration.termination
# (re-exported at the top of this module).


class NexusOrchestrator:
    """Master Agent — plans, evaluates, and coordinates the task lifecycle.

    IMPROVEMENT: Never settle for 'good enough' - push for excellence.
    Strict evaluation: progress must reach at least 80% before completion
    is allowed. If progress is still increasing, don't force-complete.

    This is a pure LLM-based coordinator. It does NOT execute tools itself.
    It analyzes the user request, selects agents, creates plans, and evaluates results.
    """

    def __init__(
        self,
        llm: Any,
        agent_catalog: str = "",
        event_callback: Optional[Callable] = None,
    ) -> None:
        self._llm = llm
        self._agent_catalog = agent_catalog
        self._event_callback = event_callback
        # Single source of truth for per-task progress (audit C1/C2).
        # The legacy dict attributes are kept as backward-compatible properties
        # below so existing call sites and tests keep working unchanged.
        self._progress = ProgressTracker()
        self._agent_crash_history: dict[str, int] = {}  # role → consecutive crash count
        # FIX C1: Assignment deduplication — detect pipeline-level loops
        self._assignment_hashes: dict[str, list[str]] = {}  # task_id → [hash, hash, ...]
        # A1: whether the most recent evaluated turn force-stopped an agent (killed
        # for being stuck). A force-stopped approach is provably exhausted, so a
        # repeating assignment should terminate one rung earlier than the plain
        # loop ceiling. Set in evaluate_turn, read in plan_next_turn.
        self._force_stopped_by_task: dict[str, bool] = {}  # task_id → bool
        # ROOT-D: consecutive evaluation-parse failures per task. When the evaluator
        # LLM keeps returning unparseable output, the old code looped forever at a
        # frozen 50% (task d8b78042: parse failed turns 7/8/9, progress never moved).
        # Track the streak so we can escalate instead of silently continuing.
        self._eval_parse_fail_streak: dict[str, int] = {}  # task_id → streak
        self._budget_synthesis_used_by_task: dict[str, bool] = {}  # task_id → granted one synthesis turn before token-budget halt
        # task_id → {veto_kind → count}. Reset when progress sets a new HWM peak.
        self._veto_counts: dict[str, dict[str, int]] = {}
        # task_id → bool: granted the single reconciliation turn before force-stop.
        self._reconciliation_used_by_task: dict[str, bool] = {}
        # task_id → bool: reconciliation directive injected into next_turn_focus.
        self._reconciliation_directive_by_task: dict[str, bool] = {}
        # Reason code from the most recent _determine_completion call — read back
        # by evaluate_turn to apply the lawful-refusal report progress (H2).
        self._last_completion_reason: str = ""
        # task_id → count of CONSECUTIVE high-severity stagnating turns (audit
        # 3c44a93f bug #4a). Reset on any new progress high-water-mark; at the
        # limit, the completion gate forces an HONEST halt.
        self._consecutive_terminate_by_task: dict[str, int] = {}
        # task_id → count of CONSECUTIVE BARREN turns (zero successful tool calls
        # across the whole team) — audit tri-model 2026-07-01 (E7). Reset the
        # instant any tool succeeds; at _BARREN_TURNS_HALT_AT the gate forces an
        # HONEST halt so a run can't burn 5–9 turns of pure talk / budget-death.
        self._consecutive_barren_by_task: dict[str, int] = {}
        # A1/A2: latest computed TaskHealth (single source of truth for progress
        # condition). Populated in evaluate_turn; read by DECISIONS/prompt surfacing.
        self._last_task_health = None

    # Backward-compatible views onto the ProgressTracker's maps. They return the
    # SAME dict objects the tracker holds, so `.get`, `[k] = v`, and
    # `.setdefault(k, []).append(v)` mutate the tracker's state in place.
    @property
    def _progress_by_task(self) -> dict[str, int]:
        return self._progress.current_map

    @property
    def _progress_history_by_task(self) -> dict[str, list[int]]:
        return self._progress.history_map

    def get_deeper_ledger_context(self, task_id: str, current_turn: int, max_chars: int = 8000) -> str:
        """Build expanded ledger context when standard budget is insufficient.

        Called when prior evaluation signals ambiguity (contradictions, stagnation).
        Uses ContextLedger epochs + detailed recent entries — never reads raw ACTIVITY.md.
        """
        from app.orchestration.context_ledger import ContextLedger
        ledger = ContextLedger(task_id)

        parts: list[str] = []
        remaining = max_chars

        # Full epoch history (compressed summaries of all completed epochs)
        epochs = ledger._read_epochs()
        if epochs:
            section = "## Full Epoch History (all completed phases):\n"
            for ep in epochs:
                line = f"- Turns {ep.turns_range}: {ep.summary}"
                if ep.key_data:
                    line += f" | Key: {'; '.join(ep.key_data[:5])}"
                line += "\n"
                if len(section) + len(line) > remaining * 0.5:
                    break
                section += line
            parts.append(section)
            remaining -= len(section)

        # Detailed entries for recent 5 turns (full key_data, blockers, dependencies)
        recent_entries = ledger.query(
            since_turn=max(1, current_turn - 5),
            until_turn=current_turn,
            limit=40,
        )
        if recent_entries and remaining > 500:
            section = "## Detailed Recent Work (verify claims against this):\n"
            for e in sorted(recent_entries, key=lambda x: x.turn):
                line = f"- T{e.turn} [{e.agent_role}]: {e.did_what}"
                if e.key_data:
                    line += f"\n  Data: {'; '.join(e.key_data)}"
                if e.blockers:
                    line += f"\n  BLOCKED: {e.blockers}"
                line += "\n"
                if len(section) + len(line) > remaining:
                    break
                section += line
            parts.append(section)

        return "\n".join(parts) if parts else ""

    def record_agent_crash(self, agent_role: str) -> None:
        """Track consecutive crashes per agent role."""
        self._agent_crash_history[agent_role] = self._agent_crash_history.get(agent_role, 0) + 1

    def record_agent_success(self, agent_role: str) -> None:
        """Reset crash counter on success."""
        self._agent_crash_history[agent_role] = 0

    def get_crash_blacklist(self) -> list[str]:
        """Return agent roles that have crashed 3+ consecutive turns."""
        return [role for role, count in self._agent_crash_history.items() if count >= 3]

    def build_crash_context_for_planning(self) -> str:
        """Build context string about crash history for planning prompts."""
        if not self._agent_crash_history:
            return ""
        blacklisted = self.get_crash_blacklist()
        if not blacklisted:
            return ""
        return (
            "\n\n[AGENT RELIABILITY WARNING]\n"
            f"The following agent roles have crashed 3+ consecutive turns and MUST NOT be assigned: "
            f"{', '.join(blacklisted)}.\n"
            "Use alternative roles (e.g., 'code_developer' instead of crashed specialists) "
            "or assign their work to the orchestrator_self.\n"
        )

    async def plan_task(
        self,
        message: str,
        memory_context: str = "",
    ) -> tuple[TurnPlan, dict[str, Any]]:
        """Analyze user request and create the initial execution plan.

        Returns:
            Tuple of (first TurnPlan, raw planning metadata dict)
        """
        system_prompt = build_orchestrator_planning_prompt(
            agent_catalog=self._agent_catalog,
            memory_context=memory_context,
            skills_catalog=_build_skills_catalog(),
        )

        # Inject infrastructure awareness so planner knows what's ACTUALLY possible
        infra_context = ""
        try:
            from app.core.infrastructure_map import get_infrastructure_map
            infra_context = await get_infrastructure_map().build_map()
        except Exception:
            pass

        # Inject pentest-scope awareness (audit fb1a7a3c): if the request names a
        # target covered by a declared, documented engagement, tell the planner so
        # it does NOT refuse an authorized target as unauthorized. Out-of-scope or
        # non-infra requests yield "" → nothing changes (no authorization laundering).
        scope_note = ""
        try:
            from app.security.authorization_gate import build_authorized_scope_note
            scope_note = build_authorized_scope_note(message)
        except Exception:
            pass

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"{message}\n\n"
                f"{f'## Available Infrastructure{chr(10)}{infra_context}{chr(10)}{chr(10)}' if infra_context else ''}"
                f"{scope_note}"
            )},
        ]

        await self._emit("planning_start", {
            "description": "Nexus Orchestrator analyzing request...",
        })

        try:
            result = await self._llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=8192,
            )
            content = result.get("content", "")
            plan_data = _parse_json(content)

            if not plan_data:
                logger.warning("orchestrator_plan_parse_failed", content_preview=content[:200])
                plan_data = _build_fallback_plan(message)

        except Exception as e:
            logger.error("orchestrator_plan_failed", error=str(e))
            plan_data = _build_fallback_plan(message)

        # A8: a TRUNCATION-recovered plan keeps its complete prefix keys but may have
        # dropped a core field that was cut off (e.g. truncation landed inside
        # selected_agents → the whole key is gone). Backfill any missing/empty core
        # field from the fallback so a partial recovery is never WORSE than the plain
        # fallback (which always has 1 agent + 1 turn). Security tasks get a proper
        # team rebuilt later by _enforce_security_plan; this covers all task types.
        _fb = None
        if not plan_data.get("execution_plan"):
            _fb = _build_fallback_plan(message)
            plan_data["execution_plan"] = _fb["execution_plan"]
        if not plan_data.get("selected_agents"):
            _fb = _fb or _build_fallback_plan(message)
            plan_data["selected_agents"] = _fb["selected_agents"]
        if _fb is not None:
            logger.warning("orchestrator_plan_partial_backfilled")

        # Normalize agent names: if LLM returned name == role, use template default_name
        _normalize_agent_names(plan_data)

        # Extract first turn plan
        execution_plan = plan_data.get("execution_plan", [])
        if not execution_plan:
            execution_plan = [_build_fallback_turn(message)]

        first_turn_data = execution_plan[0]
        first_plan = _convert_to_turn_plan(first_turn_data, turn_number=1, user_message=message)

        selected_agents = plan_data.get("selected_agents", [])
        intent_text = plan_data.get("intent_analysis", "")
        plan_summary = "; ".join(
            t.get("objective", "")[:100] for t in execution_plan[:5]
        )

        # Build structured assignments for UI display
        plan_assignments = []
        for turn in execution_plan[:5]:
            for assignment in turn.get("assignments", []):
                plan_assignments.append({
                    "agent": assignment.get("agent_name") or assignment.get("agent", ""),
                    "role": assignment.get("agent_role") or assignment.get("role", ""),
                    "task": (assignment.get("task_description") or assignment.get("task", ""))[:300],
                    "success_criteria": (assignment.get("success_criteria") or "")[:200],
                })

        await self._emit("planning_done", {
            "complexity": plan_data.get("complexity", "medium"),
            "agents_count": len(selected_agents),
            "turns_planned": len(execution_plan),
            "selected_agents": [a.get("name", a.get("role", "")) for a in selected_agents][:10],
            "intent": intent_text[:500] if intent_text else "",
            "plan_summary": plan_summary[:500],
            "assignments": plan_assignments[:10],
        })

        logger.info(
            "orchestrator_plan_created",
            complexity=plan_data.get("complexity"),
            agents=len(plan_data.get("selected_agents", [])),
            turns=len(execution_plan),
        )

        return first_plan, plan_data

    async def evaluate_turn(
        self,
        turn_result: TurnResult,
        state: ExecutionState,
        user_message: str,
        turn_plan: Optional[TurnPlan] = None,
        evolution_context: Optional["EvolutionInsight"] = None,
        ledger_context: str = "",
    ) -> EvaluationResult:
        """Evaluate a completed turn and decide whether to continue or complete.

        IMPROVEMENT: Strict evaluation logic:
        - Progress < 80% → MUST continue, never allow "complete"
        - If progress delta > 5% (still making gains) → continue even at max turns
        - Only allow completion when result is EXCELLENT, not just acceptable
        """
        # EMPTY TURN DETECTION: If no agents produced meaningful output, retry up to 2 times
        # An agent "produced output" if it has non-empty output OR if it at least tried
        # (has tool_calls or an error — meaning it ran but failed, not that it was missing)
        real_results = [
            r for r in turn_result.agent_results
            if r.agent_role != "_discussion_synthesis"
            and (r.output or r.tool_calls or r.error)
        ]

        # A1: record whether any agent was FORCE-STOPPED this turn (killed for being
        # stuck). plan_next_turn uses this to terminate a repeating assignment one
        # rung earlier — a killed-for-stuck approach won't succeed on reassignment.
        self._force_stopped_by_task[state.task_id] = any(
            "FORCE-STOPPED" in str(getattr(r, "output", ""))
            for r in turn_result.agent_results
        )
        if not real_results and turn_plan and turn_plan.assignments:
            # FIX F2: Track in state.failure_tracker (survives resume) instead of instance dict
            _empty_key = "_empty_turns"
            _empty_list = state.failure_tracker.setdefault(_empty_key, [])
            _empty_list.append(str(turn_result.turn_number))
            consecutive_empty = len(_empty_list)

            if consecutive_empty <= 2:
                logger.warning(
                    "empty_turn_detected",
                    turn=turn_result.turn_number,
                    planned_agents=len(turn_plan.assignments),
                    retry_attempt=consecutive_empty,
                )
                prev_progress = self._progress_by_task.get(state.task_id, 0)
                return EvaluationResult(
                    turn_number=turn_result.turn_number,
                    progress_pct=max(prev_progress - 5, 0),
                    is_complete=False,
                    next_turn_focus=(
                        f"[RETRY {consecutive_empty}/2] Turn {turn_result.turn_number} produced NO output — "
                        f"all {len(turn_plan.assignments)} agents failed. "
                        f"Try different agent roles or simplify the task."
                    ),
                    next_assignments=[
                        {
                            "agent_role": a.agent_role,
                            "agent_name": a.agent_name,
                            "task": a.task_description[:500],
                            "success_criteria": a.success_criteria,
                        }
                        for a in turn_plan.assignments
                    ],
                    evaluation_text=f"Empty turn (retry {consecutive_empty}/2).",
                )
            else:
                # Max retries exhausted — reset and let LLM evaluation decide
                logger.error(
                    "empty_turn_max_retries_exhausted",
                    turn=turn_result.turn_number,
                    consecutive_empty=consecutive_empty,
                )
                state.failure_tracker[_empty_key] = []  # Reset counter
                # Fall through to normal LLM evaluation
        else:
            # Successful output — reset empty turn counter
            _empty_key = "_empty_turns"
            if _empty_key in state.failure_tracker:
                state.failure_tracker[_empty_key] = []

        system_prompt = build_orchestrator_evaluation_prompt()

        # CONTEXT MANAGEMENT: Orchestrator evaluates summaries.
        # Full data in ACTIVITY.md for report generation.
        # Each agent output: conclusion-extracted for evaluation LLM call.
        # Tool calls: summary only (tool name + success/fail + result hint).
        # Budget: cap TOTAL evaluation input to ~60KB to fit any model context window.
        _EVAL_TOTAL_BUDGET = 60000
        _eval_remaining = _EVAL_TOTAL_BUDGET

        from app.orchestration.models import _extract_conclusion

        num_agents = max(1, len(turn_result.agent_results))
        _EVAL_AGENT_CAP = min(12000, _eval_remaining // num_agents)

        results_text = ""
        for r in turn_result.agent_results:
            agent_section = f"### {r.agent_name} ({r.agent_role})\n"
            # Tool calls: show ALL tools (name + status) with hints for key ones
            if r.tool_calls:
                agent_section += f"**Tools used** ({len(r.tool_calls)}):\n"
                # First 15: full detail with output hint
                for tc in r.tool_calls[:15]:
                    status = "✅" if tc.success else "❌"
                    hint = tc.output[:400].replace("\n", " ") if tc.output else ""
                    agent_section += f"  - {status} `{tc.tool_name}` {hint}\n"
                # Remaining: compact list so orchestrator knows what happened
                if len(r.tool_calls) > 15:
                    remaining_tcs = r.tool_calls[15:]
                    success_count = sum(1 for tc in remaining_tcs if tc.success)
                    tool_names = list(dict.fromkeys(tc.tool_name for tc in remaining_tcs))
                    agent_section += (
                        f"  - ... +{len(remaining_tcs)} more ({success_count} succeeded): "
                        f"{', '.join(tool_names[:8])}\n"
                    )
            else:
                agent_section += "**Tools used**: 0\n"
            # Agent output: use conclusion extraction for better evaluation
            if r.output:
                per_agent_cap = min(_EVAL_AGENT_CAP, _eval_remaining // max(1, num_agents))
                output_text = _extract_conclusion(r.output, per_agent_cap)
                agent_section += f"**Output**: {output_text}\n"
            if r.error:
                agent_section += f"**Error**: {r.error}\n"
            agent_section += "\n"
            results_text += agent_section
            _eval_remaining -= len(agent_section)

        # --- FIX 1: Partial Data Recovery ---
        # If agent crashed (no output / error) but had successful tool calls,
        # extract useful data so next turn agents don't refetch.
        for r in turn_result.agent_results:
            has_successful_tools = any(tc.success and tc.output for tc in r.tool_calls)
            agent_failed = not r.success or (r.error and not r.output)
            if agent_failed and has_successful_tools:
                partial_data = self._extract_partial_data(r.tool_calls)
                if partial_data:
                    # Find assignment context for this agent
                    assignment_task = ""
                    if turn_plan:
                        for a in turn_plan.assignments:
                            if a.agent_role == r.agent_role:
                                assignment_task = a.task_description[:200]
                                break
                    state.collected_data_cache.append({
                        "agent": r.agent_role,
                        "turn": turn_result.turn_number,
                        "purpose": assignment_task or f"Data from {r.agent_role} before crash",
                        "data_summary": partial_data[:4000],
                    })
                    # Cap cache to prevent unbounded memory growth
                    # GAP-5 FIX: Increased from 10 to 25 items, evict shortest entries
                    # first (they contain least useful data) instead of simple FIFO.
                    if len(state.collected_data_cache) > 25:
                        state.collected_data_cache.sort(
                            key=lambda x: len(x.get("data_summary", "")), reverse=True
                        )
                        state.collected_data_cache = state.collected_data_cache[:25]
                    logger.info(
                        "partial_data_recovered",
                        agent=r.agent_role,
                        turn=turn_result.turn_number,
                        data_len=len(partial_data),
                    )

        # --- FIX 2: Failure Tracking ---
        for r in turn_result.agent_results:
            if not r.success or r.error:
                error_sig = (r.error or "unknown_error")[:200]
                state.failure_tracker.setdefault(r.agent_role, []).append(error_sig)
                # Track consecutive crashes for blacklisting
                if "total_chars" in error_sig or "NameError" in error_sig or (
                    not r.tool_calls and r.error
                ):
                    self.record_agent_crash(r.agent_role)
            else:
                # Reset on success
                state.failure_tracker.pop(r.agent_role, None)
                self.record_agent_success(r.agent_role)

        # Build turn plan summary
        plan_text = ""
        if state.turns_completed:
            last_turn = state.turns_completed[-1] if state.turns_completed else None
            if last_turn and last_turn.orchestrator_evaluation:
                plan_text = last_turn.orchestrator_evaluation.next_turn_focus or ""

        # BUG-NEW-3 FIX: Include full assignment details (task + success criteria)
        # so the LLM can properly evaluate results against original requirements.
        turn_plan_text = ""
        if turn_plan and turn_plan.assignments:
            turn_plan_text = f"## Turn {turn_plan.turn_number} Plan (what was assigned)\n"
            turn_plan_text += f"**Objective**: {turn_plan.objective}\n"
            turn_plan_text += f"**Milestone**: {turn_plan.milestone}\n\n"
            for a in turn_plan.assignments:
                turn_plan_text += (
                    f"- **{a.agent_name}** ({a.agent_role})\n"
                    f"  - Task: {a.task_description}\n"
                    f"  - Success criteria: {a.success_criteria}\n"
                )
        elif turn_result.agent_results:
            # Fallback if plan not provided (backward compat)
            turn_plan_text = "## Turn Plan (what was assigned)\n"
            for r in turn_result.agent_results:
                turn_plan_text += f"- **{r.agent_name}** ({r.agent_role})\n"

        # Build evolution context section if available
        evolution_text = ""
        if evolution_context and evolution_context.should_inject:
            severity_label = (
                "🔴 CRITICAL" if evolution_context.severity_score >= 0.8
                else "🟡 MODERATE" if evolution_context.severity_score >= 0.5
                else "🟢 LOW"
            )
            evolution_text = (
                f"\n\n## 🧬 Evolution Agent Analysis [{severity_label}]\n"
                f"**Quality trajectory:** {evolution_context.quality_trajectory}\n"
                f"**Severity:** {evolution_context.severity_score:.1f}/1.0\n\n"
            )
            if evolution_context.blind_spots:
                evolution_text += "**Blind spots (MUST address):**\n"
                for spot in evolution_context.blind_spots[:4]:
                    evolution_text += f"  - ⚠️ {spot}\n"
            if evolution_context.challenge_questions:
                evolution_text += "**Challenge questions:**\n"
                for q in evolution_context.challenge_questions[:3]:
                    evolution_text += f"  - ❓ {q}\n"
            if evolution_context.improvement_suggestions:
                evolution_text += "**Required improvements:**\n"
                for s in evolution_context.improvement_suggestions[:3]:
                    evolution_text += f"  - 💡 {s}\n"
            evolution_text += (
                "\n**⚡ Evaluation instruction:** If Evolution severity >= 0.8, "
                "you MUST NOT mark as 'complete'. Continue until gaps are resolved.\n"
            )

            # MANDATORY DIRECTIVE: When Evolution Agent detects severe stagnation,
            # its first [NOVEL] suggestion becomes a MANDATORY assignment override.
            # This prevents the "evolution advises but agents ignore" failure mode.
            _is_stagnating = "stagnating" in (evolution_context.quality_trajectory or "").lower()
            # E-R5: count stagnant turns from the SELF-graded history (pre-cap),
            # falling back to the capped history — so a low-pinning artifact cap can't
            # make a task that is actually progressing look stagnant and wrongly
            # trigger the high-priority Evolution suggestion.
            _self_hist = list(state.self_progress_history.values())
            _stag_hist = _self_hist if _self_hist else self._progress_history_by_task.get(state.task_id, [])
            _prev_progress = _stag_hist[-1] if _stag_hist else self._progress_by_task.get(state.task_id, 0)
            _consecutive_stagnant = len([
                p for p in _stag_hist[-5:]
                if abs(p - _prev_progress) <= 3
            ]) if _stag_hist else 0

            # FIX C4: Lower threshold for mandatory evolution directives
            # Previously 0.85 + 3 stagnant → now 0.7 + 2 stagnant
            if (
                evolution_context.severity_score >= 0.7
                and _is_stagnating
                and _consecutive_stagnant >= 2
                and evolution_context.improvement_suggestions
            ):
                novel_suggestion = next(
                    (s for s in evolution_context.improvement_suggestions if "[NOVEL]" in s),
                    evolution_context.improvement_suggestions[0],
                )
                evolution_text += build_mandatory_directive_text(
                    suggestion=novel_suggestion,
                    severity=evolution_context.severity_score,
                    stagnant_turns=_consecutive_stagnant,
                )

        # Context deduplication: ledger and accumulated_briefing overlap significantly.
        # When ledger is active (has structured entries), it's the superior source —
        # skip accumulated_briefing entirely and give its budget to ledger.
        # Only fall back to flat briefing when ledger has no data (first turn).
        _total_context_budget = min(20000, max(6000, _eval_remaining))
        if ledger_context:
            # Include TASK_BRIEF (full objectives) + TODO (progress) + DECISIONS (prior reasoning)
            task_brief = self._read_task_brief(state.task_id)
            todo_state = self._read_todo_current_state(state.task_id)
            decisions = self._read_decisions(state.task_id)
            accumulated_briefing = f"## Original Request\n{state.user_message}\n"
            if task_brief:
                accumulated_briefing += f"\n{task_brief}\n"
            if todo_state:
                accumulated_briefing += f"\n{todo_state}\n"
            if decisions:
                accumulated_briefing += f"\n## Your Prior Decisions (recent turns)\n{decisions}\n"
            # Briefing takes priority; ledger gets whatever budget remains (0 if none).
            # max(0, …) guards against a negative slice silently chopping the ledger tail
            # when the briefing alone already exceeds the budget.
            ledger_context = ledger_context[:max(0, _total_context_budget - len(accumulated_briefing))]
        else:
            accumulated_briefing = state.get_accumulated_briefing(max_chars=_total_context_budget)
            ledger_context = ""

        # Inject the Deliverable Contract — the primary acceptance standard. The orchestrator
        # must judge completion against the concrete artifacts the user is owed.
        contract_text = self._read_contract(state.task_id)
        if contract_text:
            accumulated_briefing = f"{contract_text}\n\n{accumulated_briefing}"

        # ═══ Orchestrator Tool Loop (Autonomous Workspace Exploration) ═══
        # The orchestrator is an AGENT with read-only tool access.
        # It receives FULL context first (tree + ledger + results + history),
        # THEN decides what additional files to read for deeper understanding.
        from app.orchestration.orchestrator_tools import (
            run_orchestrator_tool_loop,
            _execute_workspace_tree,
        )

        # Always inject workspace tree (cheap, gives full structural picture)
        workspace_tree = _execute_workspace_tree(state.task_id, {})
        tree_section = f"\n\n## Workspace Tree\n```\n{workspace_tree[:3000]}\n```"
        accumulated_briefing += tree_section
        _eval_remaining -= len(tree_section)

        # Run autonomous tool loop: orchestrator gets FULL context first,
        # then can read specific files for deeper understanding.
        if "_work/" in workspace_tree or "TASK_BRIEF" in workspace_tree:
            tool_loop_prompt = (
                "You are NexusOrchestrator — the master coordinator evaluating a completed turn.\n"
                "You have read-only tools to explore the task workspace.\n\n"
                "## What you already have (in context below)\n"
                "- User's original request (what they want)\n"
                "- Full workspace tree (every file name + size in _context/ and _work/)\n"
                "- Structured progress history from ledger (what was done each turn)\n"
                "- This turn's agent output summaries (what was just produced)\n\n"
                "## Your job now\n"
                "Decide if you need to READ specific file CONTENTS to evaluate accurately.\n\n"
                "**Read files when:**\n"
                "- You see a design/architecture/spec doc in tree → read it to know the plan\n"
                "- You see code deliverables → read key ones to verify they match design\n"
                "- You need to check TASK_BRIEF constraints or TODO invariants\n"
                "- Agent claims don't match what the tree shows (e.g., 'built Next.js app'\n"
                "  but tree only shows index.html)\n\n"
                "**Call done_reading immediately when:**\n"
                "- The tree + ledger + agent results give you enough to evaluate\n"
                "- Workspace is empty (first turn, nothing to read yet)\n"
                "- You already understand the architecture from ledger history\n\n"
                "## Tools available\n"
                "- `read_workspace_file(path)` — read any file (e.g., '_work/ARCHITECTURE.md')\n"
                "- `search_workspace(query)` — find keywords across all files\n"
                "- `done_reading(summary)` — signal you have enough info\n\n"
                "Be efficient. 1-3 reads is typical. Up to 10 total for complex workspaces."
            )

            # Build FULL context for the tool loop (tree + history + current results)
            tool_loop_context_parts = [
                f"## User's Original Request\n{state.user_message}\n",
                f"## Current Status\nTurn just completed: {turn_result.turn_number} | "
                f"Total turns: {state.turn_count}\n",
                f"## Workspace Tree\n```\n{workspace_tree}\n```\n",
            ]

            # Include ledger (structured history of all prior work)
            if ledger_context:
                tool_loop_context_parts.append(
                    f"## Structured Progress History (Ledger)\n{ledger_context[:6000]}\n"
                )

            # Include what agents did THIS turn (conclusions)
            agent_summary_for_loop = []
            for r in turn_result.agent_results:
                output_preview = (r.output or "")[:800]
                agent_summary_for_loop.append(
                    f"- **{r.agent_name}** ({r.agent_role}): {output_preview}"
                )
            if agent_summary_for_loop:
                tool_loop_context_parts.append(
                    f"## This Turn's Agent Results\n" + "\n".join(agent_summary_for_loop[:5]) + "\n"
                )

            tool_loop_context_parts.append(
                "---\n"
                "Now: read any files you need for accurate evaluation, or call done_reading "
                "if you already have enough context."
            )

            tool_loop_context = "\n".join(tool_loop_context_parts)

            # Budget for exploration: leave room for ledger + results + plan in final eval
            _exploration_budget = max(2000, _eval_remaining - 30000)

            # Scale exploration depth to task complexity. The loop early-exits via
            # done_reading when the orchestrator has enough, so this is a CEILING, not a
            # fixed cost — but a lower ceiling on simple tasks prevents needless extra
            # read rounds when one pass over the workspace is plainly sufficient.
            _max_explore_rounds = {
                "simple": 3,
                "medium": 5,
                "complex": 8,
                "expert": 10,
            }.get(state.complexity, 6)

            try:
                exploration_context = await run_orchestrator_tool_loop(
                    llm=self._llm,
                    task_id=state.task_id,
                    system_prompt=tool_loop_prompt,
                    initial_context=tool_loop_context,
                    max_rounds=_max_explore_rounds,
                )
                if exploration_context:
                    capped = exploration_context[:_exploration_budget]
                    accumulated_briefing += f"\n\n## Orchestrator Exploration Results\n{capped}"
                    _eval_remaining -= len(capped)
            except Exception as e:
                logger.debug("orchestrator_tool_loop_skipped", error=str(e)[:200])
                # Fallback: use static WorkspaceReader digest
                from app.orchestration.workspace_reader import WorkspaceReader
                ws_reader = WorkspaceReader(state.task_id)
                workspace_digest = ws_reader.build_digest(max_chars=4000)
                if workspace_digest:
                    accumulated_briefing += f"\n\n{workspace_digest}"

        # BUG-4 FIX: Carry forward previous objective_tracking so orchestrator
        # doesn't forget objectives already marked DONE in earlier turns.
        prev_objective_text = ""
        if state.turns_completed:
            for prev_turn in reversed(state.turns_completed):
                prev_ev = prev_turn.orchestrator_evaluation
                if prev_ev and prev_ev.objective_tracking:
                    prev_objective_text = "\n## Previous Objective Status (from last evaluation)\n"
                    for obj in prev_ev.objective_tracking:
                        prev_objective_text += (
                            f"- [{obj.get('status', '?')}] {obj.get('objective', '')}\n"
                        )
                    prev_objective_text += (
                        "\n**IMPORTANT:** Do NOT regress objectives already marked DONE. "
                        "Only update IN_PROGRESS → DONE or add new objectives.\n"
                    )
                    break  # Only need the most recent

        # Ledger context provides structured, queryable history.
        # Cap to remaining budget after tree/exploration took their share.
        # When ambiguity detected from prior evaluation, expand budget with deeper epoch/entry detail.
        _prior_ambiguous = False
        if state.turns_completed:
            prev_ev = state.turns_completed[-1].orchestrator_evaluation if state.turns_completed[-1] else None
            if prev_ev:
                # E-R5: stagnation reads SELF-progress (pre-cap), falling back to the
                # capped history — a low-pinning artifact cap must not fake ambiguity.
                _prior_ambiguous = bool(getattr(prev_ev, 'contradictions', None)) or (
                    state.turn_count >= 5
                    and is_progress_stagnant(
                        list(state.self_progress_history.values()),
                        list(state.progress_history.values()),
                        min_delta=_MIN_PROGRESS_DELTA,
                    )
                )

        ledger_section = ""
        if ledger_context:
            ledger_budget = max(3000, _eval_remaining - len(results_text) - 5000)
            ledger_section = f"\n## Structured Progress (from Ledger)\n{ledger_context[:ledger_budget]}\n"
            _eval_remaining -= len(ledger_section)

        # Deeper context injection when prior evaluation was ambiguous
        if _prior_ambiguous and state.task_id:
            deeper = self.get_deeper_ledger_context(
                state.task_id, turn_result.turn_number,
                max_chars=min(8000, max(2000, _eval_remaining - 5000)),
            )
            if deeper:
                ledger_section += f"\n## DEEPER VERIFICATION (triggered by prior ambiguity)\n{deeper}\n"
                _eval_remaining -= len(deeper)

        # Inject crash blacklist context so orchestrator avoids assigning crashed agents
        crash_context = self.build_crash_context_for_planning()

        # Load Goal Anchor for evaluation grounding
        _anchor_path = task_paths.context_file(state.task_id, "GOAL_ANCHOR.txt")
        _goal_anchor_text = ""
        if _anchor_path.exists():
            try:
                _goal_anchor_text = _anchor_path.read_text(encoding="utf-8")[:500]
            except Exception:
                pass

        if _goal_anchor_text:
            results_text = f"{_goal_anchor_text}\n\n{results_text}"

        # Token Governor: surface a wrap-up directive when task budget hits warn.
        try:
            from app.core.token_governor import get_task_budget
            from app.config import get_settings as _gs3
            if state.task_id:
                _bs = get_task_budget(state.task_id, int(_gs3().task_token_budget)).status()
                if _bs.level in ("warn", "hard_stop"):
                    results_text = (
                        f"[TOKEN BUDGET {_bs.pct:.0%} USED — prioritize concluding the "
                        f"task; do NOT open new lines of investigation.]\n\n{results_text}"
                    )
        except Exception:
            pass

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"{accumulated_briefing}\n\n"
                f"{ledger_section}"
                f"---\n\n"
                f"{prev_objective_text}"
                f"{turn_plan_text}\n"
                f"## Turn {turn_result.turn_number} Results (CURRENT)\n\n"
                f"{results_text}\n\n"
                f"## Status\n"
                f"Turns completed: {state.turn_count}\n"
                f"Total agents active: {len(state.total_agents)}\n"
                f"{'Previous focus: ' + plan_text if plan_text else ''}"
                f"{crash_context}"
                f"{evolution_text}\n"
            )},
        ]

        try:
            result = await self._llm.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=3072,
            )
            content = result.get("content", "")
            eval_data = _parse_json(content)

            if not eval_data:
                # ROOT-D: never auto-complete on an unparseable eval — but don't loop
                # forever at a frozen 50% either. Count consecutive failures; once the
                # evaluator has been unparseable for several turns, stop pretending we
                # can still steer and surface it as an explicit blocker for the caller.
                streak = self._eval_parse_fail_streak.get(state.task_id, 0) + 1
                self._eval_parse_fail_streak[state.task_id] = streak
                eval_result = EvaluationResult(
                    turn_number=turn_result.turn_number,
                    progress_pct=50,
                    is_complete=False,
                    evaluation_text=(
                        f"(Evaluation parse failed {streak}x consecutively — using heuristic)"
                    ),
                )
                if streak >= _EVAL_PARSE_FAIL_TERMINATE_AFTER:
                    # The evaluator has been blind for too long. Continuing only
                    # loops at a frozen 50% (the exact pathology seen in task
                    # 4c05b7ff). Stop pretending we can steer: complete the task
                    # HONESTLY — keep the last real progress, mark it done, and
                    # hand off what is verified vs. still open.
                    logger.warning(
                        "orchestrator_eval_parse_fail_terminate",
                        task_id=state.task_id[:8],
                        streak=streak,
                    )
                    self._eval_parse_fail_streak.pop(state.task_id, None)
                    eval_result.progress_pct = self._progress_by_task.get(state.task_id, 50)
                    eval_result.is_complete = True
                    eval_result.terminated_reason = "eval_blind"
                    eval_result.missing_items = [
                        f"Orchestrator evaluation was unparseable for {streak} consecutive "
                        "turns — automated steering is impossible. Terminating HONESTLY: "
                        "consolidate what is VERIFIED vs. what remains OPEN rather than "
                        "looping blindly. Investigate the evaluator emitter before resuming."
                    ]
                    eval_result.next_turn_focus = (
                        "[EVAL STALLED — HONEST STOP] Evaluator unparseable "
                        f"{streak} turns running; task halted with a verified-vs-open handoff."
                    )
                    eval_result.evaluation_text = (
                        f"(Evaluation parse failed {streak}x consecutively — honest termination)"
                    )
                    return eval_result
                if streak >= _EVAL_PARSE_FAIL_ESCALATE_AFTER:
                    logger.warning(
                        "orchestrator_eval_parse_fail_escalation",
                        task_id=state.task_id[:8],
                        streak=streak,
                    )
                    eval_result.missing_items = [
                        f"Orchestrator evaluation has been unparseable for {streak} "
                        "consecutive turns — progress cannot be steered. Reassess the "
                        "approach or hand off honestly (state what is verified vs. open) "
                        "rather than continuing blindly."
                    ]
                    eval_result.next_turn_focus = (
                        "[EVAL STALLED] The evaluator failed to produce parseable output "
                        f"{streak} turns running. Bring a DIFFERENT agent in to consolidate "
                        "verified results and explicitly list remaining gaps."
                    )
                return eval_result

            # Parsed successfully — clear the parse-failure streak.
            self._eval_parse_fail_streak.pop(state.task_id, None)

            progress_pct = eval_data.get("progress_pct", 50)
            llm_decision = eval_data.get("decision", "continue")

            # FIX F1: Cap both regression (-10%) and inflation (+20%) per turn.
            # Prevents LLM from jumping 0→80% in one turn (hallucinates progress).
            prev = self._progress_by_task.get(state.task_id, 0)
            progress_pct = max(progress_pct, prev - 10)
            if prev > 0:
                progress_pct = min(progress_pct, prev + 20)

            # E-R5: record the SELF-graded progress (after anti-hallucination clamp,
            # but BEFORE the ground-truth artifact/self-verify caps below) so
            # stagnation detection reads the agent's own estimate, not a delivered-%
            # that a cap may pin low. UI + completion gate keep using the capped
            # progress_pct recorded further down.
            state.self_progress_history[turn_result.turn_number] = progress_pct

            # IMPROVEMENT: Smart completion logic — never settle for 'good enough'
            # Block completion on ANY unresolved contradiction (not just ones that
            # mention "constraint/invariant"). Two agents reaching opposite factual
            # conclusions (e.g. "AICrowd LIVE" vs "DEAD") must be resolved against
            # ground truth before the task may complete.
            contradictions = eval_data.get("contradictions", [])
            if contradictions_block_completion(contradictions, llm_decision):
                logger.info(
                    "orchestrator_blocking_completion_unresolved_contradiction",
                    contradictions=contradictions[:3],
                )
                llm_decision = "continue"
                # Steer the next turn at resolving the conflict with a real probe,
                # not another opinion doc.
                _conflict = "; ".join(c for c in contradictions[:2] if c)
                _focus = eval_data.get("next_turn_focus", "") or ""
                eval_data["next_turn_focus"] = _smart_truncate(
                    f"[RESOLVE CONTRADICTION] Settle this with ONE ground-truth call "
                    f"(API/tool/file check), not another analysis doc: {_conflict}. {_focus}",
                    800,
                )

            # Detect security task from user message or state context
            _is_security = any(
                kw in (state.user_message or "").lower()
                for kw in ("pentest", "security", "exploit", "vulnerability", "ctf", "hack")
            )
            # CC-7: a low-progress 'complete' is only honored with a justification.
            # The authoritative, schema-backed signal is an objective explicitly
            # marked BLOCKED in objective_tracking (prompts.py instructs the LLM to
            # use status "BLOCKED" for an infeasible objective, and progress counts
            # DONE+BLOCKED). Also honor explicit blocked/infeasible flags if present,
            # the model's terminated_reason, or a genuine safety refusal this turn.
            _obj_tracking = eval_data.get("objective_tracking", []) or []
            _has_blocked_objective = any(
                str(o.get("status", "")).upper() == "BLOCKED"
                for o in _obj_tracking if isinstance(o, dict)
            )
            # H2a (audit review 2026-06-29): keep the SAFETY-refusal signal SEPARATE
            # from the broader blocked-justification union. Only a genuine safety/
            # authorization refusal earns the "lawful_refusal = 100% correctly
            # refused" terminal; an infeasible / externally-blocked task is blocked
            # but NOT a refusal, and must keep its real progress.
            _is_safety_refusal = False
            if state.turns_completed:
                _last_out = " ".join(
                    (ar.output or "") for ar in state.turns_completed[-1].agent_results
                )
                _is_safety_refusal = refusal_is_terminal(_last_out)
            # E4 (run 68c276ac): do NOT accept a free-text ``terminated_reason``
            # from the LLM's eval_data as a justification. prompts.py never asks the
            # model to emit that field, so a model that invents one (to rationalise
            # "complete" at 10%) would unlock BOTH the CC-7 low-progress gate and the
            # artifact veto. The LEGITIMATE terminals (lawful_refusal /
            # evolution_terminate / semantic_loop / loop_exhausted) are set by CODE
            # on the evaluation object AFTER this gate — never via eval_data — so
            # excluding the self-asserted field cannot suppress a real terminal.
            _blocked_justified = bool(
                _has_blocked_objective
                or eval_data.get("blocked_by_design")
                or eval_data.get("infeasible")
                or _is_safety_refusal
            )
            # H1: feed Evolution's severity into the completion gate so the
            # "severity >= 0.8 ⇒ MUST NOT complete" rule is enforced in code, not
            # left to the LLM to honor (radar task completed at 48% despite it).
            _evolution_severity = (
                evolution_context.severity_score
                if (evolution_context and evolution_context.should_inject)
                else 0.0
            )
            # Evolution TERMINATE enforcement (audit 3c44a93f bug #4a): a turn
            # where Evolution is high-severity AND stagnating is a "terminate"
            # signal. Count CONSECUTIVE such turns; the count is reset below on any
            # new progress high-water-mark. At the limit the gate forces an honest
            # halt instead of letting the orchestrator ignore the advice for turns.
            _terminate_signal = (
                bool(evolution_context and evolution_context.should_inject)
                and _evolution_severity >= _EVOLUTION_SEVERITY_FORBID_COMPLETE
                and "stagnating" in (
                    (evolution_context.quality_trajectory or "").lower()
                    if evolution_context else ""
                )
            )
            if _terminate_signal:
                _consec_terminate = self._consecutive_terminate_by_task.get(state.task_id, 0) + 1
            else:
                _consec_terminate = 0
            self._consecutive_terminate_by_task[state.task_id] = _consec_terminate

            # E7: count CONSECUTIVE barren turns (zero successful tool calls across
            # the whole team this turn). A single successful tool call — of ANY
            # kind, including read/search — resets the streak, so a legitimate
            # multi-turn read/plan phase is never penalised. Only fully-barren
            # turns (budget-death / prose-only / phantom) accumulate.
            _turn_had_tool_success = any(
                tc.success
                for ar in turn_result.agent_results
                if ar.agent_role != "_discussion_synthesis"
                for tc in ar.tool_calls
            )
            if _turn_had_tool_success:
                _consec_barren = 0
            else:
                _consec_barren = self._consecutive_barren_by_task.get(state.task_id, 0) + 1
            self._consecutive_barren_by_task[state.task_id] = _consec_barren

            is_complete = self._determine_completion(
                llm_decision=llm_decision,
                progress_pct=progress_pct,
                turn_count=state.turn_count,
                task_id=state.task_id,
                is_security_task=_is_security,
                has_blocked_justification=_blocked_justified,
                evolution_severity=_evolution_severity,
                is_safety_refusal=_is_safety_refusal,
                consecutive_terminate_signals=_consec_terminate,
                consecutive_barren_turns=_consec_barren,
            )
            # H2: a lawful-refusal terminal is Definition-of-Done for its branch —
            # report it at 100% with an explicit reason so the handoff reads
            # "correctly refused", not "HALTED 50%".
            _completion_reason = getattr(self, "_last_completion_reason", "")
            if is_complete and _completion_reason == "lawful_refusal":
                progress_pct = lawful_refusal_progress(_completion_reason, progress_pct)

            # Objective-based progress validation:
            # If objective_tracking provided, use it to verify progress_pct
            objective_tracking = eval_data.get("objective_tracking", [])
            if objective_tracking:
                done_count = sum(1 for o in objective_tracking if o.get("status") == "DONE")
                total_count = len(objective_tracking)
                if total_count > 0:
                    objective_pct = int((done_count / total_count) * 100)
                    # Use the LOWER of LLM estimate and objective-based calculation
                    # This prevents inflated progress claims
                    progress_pct = min(progress_pct, objective_pct + 10)

            # M4 FIX: Merge with prior objective_tracking to prevent DONE regressions
            prior_tracking = state.objective_tracking
            if prior_tracking and objective_tracking:
                # Build lookup: objective text → prior status
                prior_by_text = {o.get("objective", ""): o.get("status", "") for o in prior_tracking}
                for obj in objective_tracking:
                    text = obj.get("objective", "")
                    prior_status = prior_by_text.get(text)
                    if prior_status == "DONE" and obj.get("status") != "DONE":
                        # LLM regressed a DONE objective — restore it
                        obj["status"] = "DONE"
                # Recalculate progress after merge
                done_count = sum(1 for o in objective_tracking if o.get("status") == "DONE")
                total_count = len(objective_tracking)
                if total_count > 0:
                    merged_pct = int((done_count / total_count) * 100)
                    progress_pct = max(progress_pct, merged_pct)

            # GROUND-TRUTH ARTIFACT CAP (audit 3c44a93f bug #4c).
            # The progress number above is self-graded by the LLM. Cap it by how
            # many CONTRACT artifacts actually EXIST on disk so a "100% / ALL
            # COMPLETE" claim can never outrun the deliverables (that run reported
            # 100% with 1 of 7 artifacts on disk). An empty scaffold dir does not
            # count as present (bug #1 synergy).
            try:
                from app.orchestration.workspace_reader import count_contract_artifacts
                from app.orchestration.termination import artifact_delivery_cap
                _declared, _present = count_contract_artifacts(state.task_id)
                _art_cap = artifact_delivery_cap(_declared, _present)
            except Exception:
                _declared = _present = 0
                _art_cap = None
            if _art_cap is not None:
                progress_pct = min(progress_pct, _art_cap)
                # Veto a VOLUNTARY "I'm done" completion (reason == llm_decision)
                # when declared artifacts are materially missing. Honest terminals
                # (lawful_refusal / blocked_terminal / plateau / stagnation /
                # max_turns / token-budget) are NOT vetoed — those must stop, and
                # update_todo already writes an HONEST partial handoff for them. A
                # genuinely-blocked task should mark its objectives BLOCKED (which
                # routes through blocked_terminal, not llm_decision).
                if (
                    is_complete
                    and getattr(self, "_last_completion_reason", "") == "llm_decision"
                    and _present < _declared
                ):
                    is_complete = False
                    logger.warning(
                        "completion_vetoed_artifacts_missing",
                        task_id=state.task_id,
                        declared=_declared, present=_present, progress_pct=progress_pct,
                    )
                    _missing_n = _declared - _present
                    _focus = (
                        f"{_present}/{_declared} CONTRACT artifacts exist on disk. "
                        f"Do NOT mark complete: produce the {_missing_n} missing "
                        f"artifact(s) to their declared Location, or mark genuinely "
                        f"blocked objectives BLOCKED with evidence."
                    )
                    eval_data["next_turn_focus"] = _focus

            # P2 (rerun audit 2026-07-01): the artifact cap proves files EXIST but
            # not that they PASS. When the task's OWN verification (a verify script
            # the agents ran this turn) reports structured failures, cap progress
            # below 100 so a "5/7 PASS" self-check can't surface as 100%. Only a
            # machine-shaped verdict counts; prose "done" claims are ignored.
            try:
                from app.orchestration.termination import self_verification_cap
                _verify_text = "\n".join(
                    (ar.output or "") for ar in turn_result.agent_results
                    if ar.agent_role != "_discussion_synthesis"
                )
                _sv_cap = self_verification_cap(_verify_text)
                if _sv_cap is not None and _sv_cap < progress_pct:
                    logger.info(
                        "progress_capped_by_self_verification",
                        task_id=state.task_id[:8],
                        from_pct=progress_pct, to_pct=_sv_cap,
                    )
                    progress_pct = _sv_cap
            except Exception as e:
                logger.debug("self_verification_cap_failed", error=str(e))

            # Persist for next turn
            state.objective_tracking = objective_tracking
            # Group A: a NEW high-water-mark is genuine progress — re-arm the
            # anti-theater gates by clearing this task's veto counts AND the
            # consecutive-terminate streak (a recovering task is never force-halted).
            _prior_hist = self._progress_history_by_task.get(state.task_id, [])
            _prior_hwm = max(_prior_hist) if _prior_hist else 0
            if progress_pct > _prior_hwm + _PLATEAU_PEAK_EPSILON:
                self._veto_counts.pop(state.task_id, None)
                # Reset the terminate streak only on a GENUINE recovery — a new
                # peak above an EXISTING history. The first measurement (0 → N on
                # turn 1) is not recovery, so it must not wipe a terminate signal.
                if _prior_hist:
                    self._consecutive_terminate_by_task.pop(state.task_id, None)
                    # E7: genuine progress also clears the barren streak.
                    self._consecutive_barren_by_task.pop(state.task_id, None)
            # Record the FINAL (objective-adjusted) progress into both stores so the
            # ProgressTracker — read by the completion/stagnation gate next turn —
            # agrees with state.progress_history and the returned EvaluationResult
            # (audit C2: previously the tracker held the raw pre-adjust value).
            self._progress.record(state.task_id, progress_pct)
            state.progress_history[turn_result.turn_number] = progress_pct

            # A1/A2 (arch review 2026-07-03): compute the task's progress condition
            # ONCE via TaskHealth — the single source of truth that will replace the
            # ~5 scattered stagnation recomputations. Strangler step 1: compute +
            # surface it (log + carried on the evaluation for DECISIONS/prompt) so it
            # runs alongside the existing gates and can be verified equivalent before
            # any gate is migrated to read it. Zero behavior change this step.
            try:
                from app.orchestration.task_health import compute_task_health
                _is_sec = _is_security
                _health = compute_task_health(
                    self_progress_history=list(state.self_progress_history.values()),
                    capped_progress_history=list(state.progress_history.values()),
                    stagnant_window=(_MAX_STAGNANT_TURNS_SECURITY if _is_sec else _MAX_STAGNANT_TURNS),
                    min_delta=_MIN_PROGRESS_DELTA,
                    plateau_limit=(_MAX_PLATEAU_TURNS_SECURITY if _is_sec else _MAX_PLATEAU_TURNS),
                    consecutive_barren_turns=_consec_barren,
                    evolution_severity=_evolution_severity,
                )
                self._last_task_health = _health
                logger.info("task_health", task_id=state.task_id[:8], summary=_health.summary())
            except Exception as e:
                logger.debug("task_health_compute_failed", error=str(e)[:100])

            # E5: a PRODUCER's self-graded "completed" must be backed by real
            # production this turn. Compute which roles actually landed a
            # successful PRODUCE tool, then downgrade any producer claiming done
            # with nothing on disk — so a phantom "done" can't enter "ALREADY
            # COMPLETED" or credit false progress. Uses the ground-truth
            # produced_deliverable signal (E3); analysis roles are never touched.
            _produced_roles = {
                ar.agent_role
                for ar in turn_result.agent_results
                if getattr(ar, "produced_deliverable", False)
            }
            _agent_evals = self._enforce_produced_completions(
                eval_data.get("agent_evaluations", []), _produced_roles
            )

            evaluation = EvaluationResult(
                turn_number=turn_result.turn_number,
                agent_evaluations=_agent_evals,
                contradictions=eval_data.get("contradictions", []),
                missing_items=eval_data.get("missing_items", []),
                progress_pct=progress_pct,
                is_complete=is_complete,
                next_turn_focus=eval_data.get("next_turn_focus", ""),
                next_assignments=eval_data.get("next_assignments", []),
                evaluation_text=eval_data.get("reasoning", ""),
                objective_tracking=objective_tracking,
                forecast=eval_data.get("forecast", ""),
            )
            # H2: stamp the lawful-refusal terminal reason so update_todo / the
            # final report write "✅ correctly refused" instead of a HALTED handoff.
            if is_complete and _completion_reason == "lawful_refusal":
                evaluation.terminated_reason = "lawful_refusal"
            # Evolution TERMINATE enforcement (audit 3c44a93f bug #4a): stamp the
            # HALTED reason so update_todo writes an HONEST partial handoff (real
            # progress), not "✅ ALL TASKS COMPLETE".
            elif is_complete and _completion_reason == "evolution_terminate":
                evaluation.terminated_reason = "evolution_terminate"
            # E7: a barren-turn halt is an HONEST partial handoff, never a genuine
            # completion — stamp the reason so update_todo / the final report write
            # a HALTED handoff with the real progress, not "✅ ALL TASKS COMPLETE".
            elif is_complete and _completion_reason == "barren_no_production":
                evaluation.terminated_reason = "barren_no_production"
            # G2-7 (tri-run round-2): a token-budget halt is an HONEST partial handoff,
            # NEVER a genuine completion. Stamp the reason so the DECISIONS label reads
            # "halted (token_budget_exhausted)" not "complete", and update_todo / the
            # final report write a HALTED partial handoff with the real progress.
            elif is_complete and _completion_reason in (
                "token_budget_exhausted", "stagnation", "eval_blind",
                "loop_exhausted", "semantic_loop", "max_turns",
            ):
                evaluation.terminated_reason = _completion_reason

            # C-4 (context-continuity audit 2026-07-03): a turn that CONTINUES but
            # emits an EMPTY next_turn_focus strands the hinge between turns — the
            # next agent loses "what to do next" exactly when it matters (gasifier
            # Turn 5 → 6). Carry forward the previous turn's focus so the directive
            # is never blank on a live task. (A genuine completion needs no focus.)
            if not evaluation.is_complete and not (evaluation.next_turn_focus or "").strip():
                _prev_focus = ""
                for _t in reversed(state.turns_completed):
                    _pe = getattr(_t, "orchestrator_evaluation", None)
                    if _pe and (_pe.next_turn_focus or "").strip():
                        _prev_focus = _pe.next_turn_focus
                        break
                if _prev_focus:
                    evaluation.next_turn_focus = (
                        f"[CARRIED FORWARD — orchestrator emitted no new focus this turn] {_prev_focus}"
                    )
                    logger.info("next_focus_carried_forward", task_id=state.task_id[:8])

            return evaluation

        except Exception as e:
            logger.error("orchestrator_evaluate_failed", error=str(e))
            return EvaluationResult(
                turn_number=turn_result.turn_number,
                progress_pct=50,
                is_complete=False,
                evaluation_text=f"(Evaluation failed: {e})",
            )

    @staticmethod
    def _enforce_produced_completions(
        agent_evaluations: list[dict[str, Any]],
        produced_roles: set[str],
    ) -> list[dict[str, Any]]:
        """Downgrade a PRODUCER's self-graded ``completed`` when it produced nothing.

        E5 (run 68c276ac): ``agent_evaluations[].completed`` is graded by the LLM,
        which stamped "done — created the drawing/file" on turns with zero
        successful tool calls. Those items then entered "ALREADY COMPLETED (do NOT
        redo)" and fed false progress. This links the claim to ground truth: a
        producer role (writer/engineer/developer/...) claiming ``completed`` must
        appear in ``produced_roles`` (a PRODUCE tool succeeded for it this turn),
        else its ``completed`` is set False and its note flagged UNVERIFIED.

        Analysis/verify roles are untouched — they legitimately finish without
        producing (phantom-write doctrine: never kill a valid analysis turn). This
        adjusts only the ``completed`` flag; it never changes agent ``success``.
        """
        for ae in agent_evaluations:
            role = str(ae.get("role", "")).lower()
            if (
                role in _PRODUCER_ROLES
                and ae.get("completed")
                and role not in produced_roles
            ):
                ae["completed"] = False
                _note = str(ae.get("notes", ""))
                ae["notes"] = (
                    "[UNVERIFIED: claimed done but no deliverable reached disk this "
                    f"turn] {_note}"
                )[:1000]
        return agent_evaluations

    def _determine_completion(
        self,
        llm_decision: str,
        progress_pct: int,
        turn_count: int,
        task_id: str = "",
        is_security_task: bool = False,
        has_blocked_justification: bool = False,
        evolution_severity: float = 0.0,
        is_safety_refusal: bool = False,
        consecutive_terminate_signals: int = 0,
        consecutive_barren_turns: int = 0,
    ) -> bool:
        """Respect the LLM Orchestrator's decision on completion.

        The Orchestrator has full authority to decide when a task is complete,
        including partial completion when objectives are genuinely infeasible.
        This method trusts the LLM's judgment — it only applies minimal
        guardrails for obvious cases (progress still climbing fast).

        SAFETY: Hard ceilings prevent infinite loops regardless of LLM decision.

        Thin wrapper: the decision logic lives in ``termination.evaluate_completion``
        (pure, unit-tested); this method supplies the per-task state and emits the
        structured log matching the decision's reason.
        """
        # Token Governor: per-task cumulative-effort ceiling.
        _budget_exhausted = False
        try:
            from app.core.token_governor import get_task_budget
            from app.config import get_settings as _gs2
            _tid = task_id
            if _tid:
                _bt = get_task_budget(_tid, int(_gs2().task_token_budget))
                _budget_exhausted = _bt.status().level == "hard_stop"
        except Exception:
            _budget_exhausted = False

        if _budget_exhausted:
            # Allow exactly one synthesis turn, then force an HONEST halt.
            # _budget_synthesis_used_by_task tracks whether we've already granted it.
            if not self._budget_synthesis_used_by_task.get(_tid, False):
                self._budget_synthesis_used_by_task[_tid] = True
                logger.warning(
                    "task_token_budget_warn_synthesis",
                    task_id=_tid, note="granting one synthesis turn before halt",
                )
                # fall through to normal evaluation (one more turn happens)
            else:
                from app.orchestration.termination import _TOKEN_BUDGET_TERMINATE_REASON
                logger.warning(
                    "task_token_budget_exhausted_halt",
                    task_id=_tid, progress_pct=progress_pct,
                )
                # H2b (audit review 2026-06-29): this early return skips the
                # evaluate_completion call that writes _last_completion_reason. Set
                # it explicitly so evaluate_turn does not re-apply a PRIOR turn's
                # stale "lawful_refusal" reason (which would falsely stamp this
                # budget halt as a 100% correct refusal).
                self._last_completion_reason = _TOKEN_BUDGET_TERMINATE_REASON
                return True  # _determine_completion returns bool, not CompletionDecision

        decision = evaluate_completion(
            llm_decision=llm_decision,
            progress_pct=progress_pct,
            turn_count=turn_count,
            progress_history=self._progress_history_by_task.get(task_id, []),
            prev_progress=self._progress_by_task.get(task_id, 0),
            is_security_task=is_security_task,
            has_blocked_justification=has_blocked_justification,
            evolution_severity=evolution_severity,
            is_safety_refusal=is_safety_refusal,
            consecutive_terminate_signals=consecutive_terminate_signals,
            consecutive_barren_turns=consecutive_barren_turns,
        )
        # Expose the gate's reason to evaluate_turn so it can apply the
        # lawful-refusal report progress (H2) and the severity-block log (H1).
        self._last_completion_reason = decision.reason

        # Group A: a plateau force-stop gets exactly ONE honest reconciliation
        # turn (pin numbers to on-disk script output, report the REAL %, list
        # unresolved contradictions) before the hard stop — mirrors the
        # token-budget synthesis one-shot above. (Semantic-loop termination halts
        # immediately with its own HALTED handoff in plan_next_turn; it does not
        # route through this gate.)
        if decision.complete and decision.reason == "plateau":
            if not self._reconciliation_used_by_task.get(task_id, False):
                self._reconciliation_used_by_task[task_id] = True
                logger.warning(
                    "plateau_reconciliation_turn_granted",
                    task_id=task_id[:8], **decision.detail,
                )
                return False  # one more turn — the reconciliation pass

        if decision.reason == "max_turns":
            logger.warning("hard_ceiling_max_turns", **decision.detail)
        elif decision.reason == "stagnation":
            logger.warning("hard_ceiling_stagnation", **decision.detail)
        elif decision.reason == "plateau":
            logger.warning("hard_ceiling_plateau", **decision.detail)
        elif decision.reason == "lawful_refusal":
            logger.warning("terminal_lawful_refusal", task_id=task_id[:8], **decision.detail)
        elif decision.reason == "evolution_severity_block":
            logger.info("orchestrator_blocking_high_severity_complete", **decision.detail)
        elif decision.reason == "climbing":
            logger.info("orchestrator_continuing_despite_complete", **decision.detail)
        elif decision.reason == "low_progress_complete_unjustified":
            logger.info("orchestrator_blocking_low_progress_complete", **decision.detail)
        elif decision.reason == "barren_no_production":
            logger.warning("hard_ceiling_barren_no_production", **decision.detail)
        return decision.complete

    async def plan_next_turn(
        self,
        state: ExecutionState,
        evaluation: EvaluationResult,
    ) -> Optional[TurnPlan]:
        """Create plan for the next turn based on evaluation.

        Returns None if task should be considered complete.
        """
        if evaluation.is_complete:
            return None

        # Inject reconciliation directive when the granted turn is about to be planned.
        if self._reconciliation_used_by_task.get(state.task_id) and not self._reconciliation_directive_by_task.get(state.task_id):
            self._reconciliation_directive_by_task[state.task_id] = True
            _focus = evaluation.next_turn_focus or ""
            evaluation.next_turn_focus = _smart_truncate(
                "[RECONCILIATION — FINAL TURN] The loop has plateaued. Pin EVERY headline "
                "number to the actual stdout of the on-disk script that produces it (run it). "
                "Report the REAL progress %, list every unresolved contradiction explicitly, "
                "and do NOT fake completion. After this turn the task stops. " + _focus,
                800,
            )

        next_turn_num = state.turn_count + 1

        # Log warning if turns are very high, but do not force-terminate
        if next_turn_num > 100 and next_turn_num % 50 == 0:
            logger.warning("high_turn_count", turns=next_turn_num)

        # ═══ Forecast-Based Dead-End Detection ═══
        # If evaluation's forecast signals a dead-end, inject pivot — BUT filter out
        # false "impossible" claims about tool installation (agents can install tools).
        forecast = getattr(evaluation, "forecast", "") or ""
        _dead_end_signals = ("dead-end", "dead end", "blocked", "no way")
        _false_dead_end_patterns = (
            "not available", "not installed", "no node", "no npm", "no docker",
            "unavailable", "environment", "cannot install",
        )
        forecast_lower = forecast.lower()
        # A forecast describing future turn plans (Turn N+1, Turn N+2) is NOT a dead-end
        _is_plan_description = any(
            pat in forecast_lower
            for pat in ("turn n+", "turn n +", "next turn", "will need to")
        )
        is_real_dead_end = (
            forecast
            and any(sig in forecast_lower for sig in _dead_end_signals)
            and not any(pat in forecast_lower for pat in _false_dead_end_patterns)
            and not _is_plan_description
        )
        if is_real_dead_end:
            logger.info("forecast_dead_end_detected", forecast=forecast[:200])
            current_focus = evaluation.next_turn_focus or ""
            # D-X1: don't order an unconditional "pivot IMMEDIATELY" — that fights
            # the Failure-Recovery ladder (diagnose→fix→bypass before switching).
            # Ask for the ladder first; pivot is the escalation, not the reflex.
            evaluation.next_turn_focus = _smart_truncate(
                f"[FORECAST PIVOT] The current path is forecast to dead-end: {forecast[:300]}. "
                f"Before repeating it: name the exact blocker, try a fix/bypass for it, "
                f"and only if that is genuinely exhausted, switch to a different approach "
                f"toward the SAME goal (do not abandon the goal). {current_focus}",
                800,
            )
        elif forecast and any(pat in forecast_lower for pat in _false_dead_end_patterns):
            logger.info("forecast_false_dead_end_rejected", forecast=forecast[:200])
            current_focus = evaluation.next_turn_focus or ""
            evaluation.next_turn_focus = _smart_truncate(
                f"[INSTALL REQUIRED] Agent claims tool unavailable but has NOT tried installing. "
                f"Next step: INSTALL the missing tool, then retry the original approach. "
                f"Do NOT pivot to a simpler alternative. {current_focus}",
                800,
            )

        # ═══ Metacognitive Override: when stagnating, analyze WHY ═══
        # Trigger after just 1 stagnant turn (>= 2 total) to prevent consecutive empty turns.
        # Previously required 3 turns which allowed 2 wasted turns before intervention.
        # SAFETY GUARD (CC-5): a stagnation caused by a genuine safety/authorization
        # refusal is TERMINAL for that branch — never "break through" it (task eb5c09bd:
        # override reversed a refusal into a live unauthorized attack). Skip the override
        # when the most recent agent output is a safety refusal.
        _last_agent_output = ""
        if state.turns_completed and state.turns_completed[-1].agent_results:
            _last_agent_output = " ".join(
                (ar.output or "") for ar in state.turns_completed[-1].agent_results
            )
        _is_safety_refusal = refusal_is_terminal(_last_agent_output)
        _prev_progress = self._progress_by_task.get(state.task_id, 0)
        _progress_delta = abs(evaluation.progress_pct - _prev_progress)
        if state.turn_count >= 2 and _progress_delta < 3 and _is_safety_refusal:
            logger.info(
                "metacognitive_override_skipped_safety_refusal",
                task_id=state.task_id[:8] if state.task_id else "",
            )
        if state.turn_count >= 2 and _progress_delta < 3 and not _is_safety_refusal:
            try:
                from app.orchestration.metacognition import MetacognitiveAgent
                metacog = MetacognitiveAgent(llm=self._llm)
                insight = await metacog.analyze_blockage(
                    state=state,
                    stuck_pattern=f"Progress stagnant at {evaluation.progress_pct}% for {state.turn_count} turns",
                )
                if insight.concrete_next_action:
                    # E-R10 (tri-run audit 2026-07-02): a stalled weak model made
                    # metacognition emit "output a plausible list ... analyze this
                    # 'found' data" — a fabrication order that then flowed into BOTH
                    # next_turn_focus and next_assignments below. Scrub at the source
                    # (code, not prompt text) so no fabrication directive is injected.
                    from app.orchestration.evolution_agent import scrub_fabrication_directive
                    insight.concrete_next_action = scrub_fabrication_directive(
                        insight.concrete_next_action
                    )
                    logger.info(
                        "metacognitive_override",
                        root_cause=insight.root_cause[:100],
                        action=insight.concrete_next_action[:200],
                    )
                    if self._event_callback:
                        await self._emit("metacognitive_insight", {
                            "root_cause": insight.root_cause[:300],
                            "reframe": insight.reframe[:300],
                            "concrete_action": insight.concrete_next_action[:300],
                            "should_ask_user": insight.should_ask_user,
                            "user_question": insight.user_question[:200] if insight.should_ask_user else "",
                        })
                    # Inject metacognitive insight into next_turn_focus. PREPEND
                    # (not overwrite): a same-turn [RECONCILIATION]/[FORECAST PIVOT]
                    # already sits in next_turn_focus and carries the anti-fake /
                    # pin-numbers-to-real-output guardrail — overwriting it dropped
                    # that guardrail exactly when the loop was worst (audit D-Y1).
                    _prior_focus = (evaluation.next_turn_focus or "").strip()
                    evaluation.next_turn_focus = _smart_truncate(
                        f"[METACOGNITIVE BREAKTHROUGH] {insight.concrete_next_action}. "
                        f"Root cause of stagnation: {insight.root_cause}. "
                        f"Reframe: {insight.reframe}"
                        + (f" {_prior_focus}" if _prior_focus else ""),
                        800,
                    )
                    # Hướng B (tri-run audit 2026-07-02, owner-approved): metacognition
                    # ADVISES, the orchestrator DECIDES. Previously this wrote
                    # evaluation.next_assignments DIRECTLY here — AFTER the orchestrator
                    # had already concluded — a true bypass of its judgement. The
                    # recommended agent + action now travel as a next_turn_focus HINT
                    # (enriched above), which the orchestrator on the NEXT turn weighs
                    # when it builds next_assignments. If this turn produced no
                    # assignments, the `if not assignments:` fallback below still keeps
                    # the task moving (no stall), so nothing is lost by not force-writing.
                    if insight.recommended_agent:
                        _prior = (evaluation.next_turn_focus or "").strip()
                        _rec = f"[SUGGESTED AGENT] Evolution suggests '{insight.recommended_agent}' for the next step; you decide the assignment."
                        if insight.recommended_agent.lower() not in _prior.lower():
                            evaluation.next_turn_focus = _smart_truncate(
                                f"{_prior} {_rec}".strip(), 900
                            )
            except Exception as e:
                logger.debug("metacognitive_override_failed", error=str(e))

        # Build assignments from evaluation.
        from app.services.agent_spawner import AGENT_TEMPLATES
        assignments: list[AgentAssignment] = []
        delegation_candidates: list[dict] = []

        # Filter out blacklisted agent roles (crashed 3+ consecutive turns)
        blacklisted_roles = self.get_crash_blacklist()
        if blacklisted_roles:
            logger.info("filtering_blacklisted_roles", blacklisted=blacklisted_roles)

        for a in evaluation.next_assignments:
            # Skip agents that are known to crash repeatedly
            if a.get("agent_role") in blacklisted_roles:
                logger.info("skipping_blacklisted_agent", role=a.get("agent_role"))
                continue
            # Auto-delegation: if evaluator flags should_delegate, wrap in delegate_task
            if a.get("should_delegate"):
                delegation_candidates.append(a)
            else:
                _role = a.get("agent_role", "researcher")
                _raw_name = a.get("agent_name", "")
                _resolved_name = (
                    _raw_name
                    if _raw_name and _raw_name.lower() != _role.lower()
                       and _raw_name.lower() != _role.replace("_", " ")
                    else AGENT_TEMPLATES.get(_role, {}).get("default_name", _role.title().replace("_", " "))
                )
                assignments.append(AgentAssignment(
                    agent_role=_role,
                    agent_name=_resolved_name,
                    task_description=a.get("task", "Continue previous work"),
                    success_criteria=a.get("success_criteria", "Complete the task"),
                    dependencies=a.get("dependencies", []),
                    tools_hint=a.get("tools_hint", []),
                ))

        # Convert delegation candidates into delegate_task assignments
        # The executing agent will call delegate_task() tool with the subtask description
        if delegation_candidates:
            for dc in delegation_candidates:
                delegate_instruction = (
                    f"Use the `delegate_task` tool to spawn an isolated subagent for this work:\n"
                    f"Task: {dc.get('task', '')}\n"
                    f"Success criteria: {dc.get('success_criteria', '')}\n"
                    f"This needs focused execution in isolation. Call delegate_task() with this description."
                )
                _dc_role = dc.get("agent_role", "researcher")
                _dc_raw_name = dc.get("agent_name", "")
                _dc_name = (
                    _dc_raw_name
                    if _dc_raw_name and _dc_raw_name.lower() != _dc_role.lower()
                       and _dc_raw_name.lower() != _dc_role.replace("_", " ")
                    else AGENT_TEMPLATES.get(_dc_role, {}).get("default_name", _dc_role.title().replace("_", " "))
                )
                assignments.append(AgentAssignment(
                    agent_role=_dc_role,
                    agent_name=_dc_name,
                    task_description=delegate_instruction,
                    success_criteria=f"Subtask completed via delegation: {dc.get('success_criteria', '')}",
                    dependencies=dc.get("dependencies", []),
                    tools_hint=["delegate_task"],
                ))

        if not assignments:
            # Fallback: assign only 1-2 agents (not ALL) with specific work.
            # Assigning all agents the same generic task wastes parallel slots.
            available_roles = [
                r for r in state.total_agents
                if r not in self.get_crash_blacklist()
            ]
            # Pick max 2 agents for fallback (avoid 6 agents doing same thing)
            fallback_roles = available_roles[:2] if available_roles else list(state.total_agents)[:1]
            missing_text = "; ".join(evaluation.missing_items[:3]) if evaluation.missing_items else "complete remaining work"
            for role in fallback_roles:
                assignments.append(AgentAssignment(
                    agent_role=role,
                    agent_name=AGENT_TEMPLATES.get(role, {}).get("default_name", role.title().replace("_", " ")),
                    task_description=f"Address missing items: {missing_text}",
                    success_criteria="Fill the identified gaps with concrete deliverables",
                ))

        # Group A role lock: pin ad-hoc role names to the declared team so
        # knowledge accumulates per role instead of fragmenting across variants.
        if assignments and state.total_agents:
            for _a in assignments:
                _canon = canonicalize_role(_a.agent_role, list(state.total_agents))
                if _canon != _a.agent_role:
                    logger.info(
                        "role_canonicalized",
                        task_id=state.task_id[:8],
                        original=_a.agent_role, mapped=_canon,
                    )
                    _a.agent_role = _canon

        # --- ROOT-A: Enforce role rotation to break single-agent monoculture ---
        # The evaluator LLM is biased toward "the agent who already has context",
        # which let one role run 8 turns straight while critic/engineer/writer never
        # executed (task d8b78042). When a role has dominated recent turns and the
        # team has someone fresh, swap one stale slot for an independent perspective.
        if assignments and len(state.total_agents) >= 2:
            # Short window for the monoculture check; a wider one for starvation
            # (audit c527e1c2: analytical roles alternated 11 turns while the writer
            # never ran, dodging the 2-turn dominance check). 8 turns is enough to
            # prove a never-run role is genuinely starved, not just briefly skipped.
            _STARVATION_WINDOW = 8
            recent_turn_roles = [
                [ar.agent_role for ar in t.agent_results]
                for t in state.turns_completed[-_STARVATION_WINDOW:]
            ]
            blacklist = self.get_crash_blacklist()
            eligible_team = [r for r in state.total_agents if r not in blacklist]
            proposed = [a.agent_role for a in assignments]
            # Steer rotation priority by WHAT is missing: a report/document gap
            # should pull in the writer, not another critic (audit c527e1c2). Scan
            # ALL missing items (not a [:3] head) so a report signal lower in the
            # list still routes priority to the writer (final-review Minor).
            _gap = "; ".join(evaluation.missing_items) if evaluation.missing_items else ""
            adjusted, injected, reason = enforce_role_rotation(
                proposed_roles=proposed,
                recent_turn_roles=recent_turn_roles,
                team_roles=eligible_team,
                starvation_window=_STARVATION_WINDOW,
                outstanding_gap=_gap,
            )
            if injected:
                logger.info("orchestrator_role_rotation", injected=injected, reason=reason)
                # Repoint the last (swapped) assignment to the fresh role, keeping the
                # work description so the new agent attacks the same outstanding gap.
                swapped = assignments[-1]
                swapped.agent_role = injected
                swapped.agent_name = AGENT_TEMPLATES.get(
                    injected, {}).get("default_name", injected.title().replace("_", " "))
                if injected == "critic":
                    # If the static auditor already flagged self-cancelling checks,
                    # point the critic straight at those file:line suspects so the
                    # review starts from concrete targets, not a blank-page hunt.
                    suspect_pointer = ""
                    try:
                        from pathlib import Path

                        from app.core import task_paths
                        from app.orchestration.tautology_detector import scan_workspace_scripts
                        from app.orchestration.tautology_prober import probe_scan

                        _work_dir = task_paths.work_dir(state.task_id)
                        _scans = scan_workspace_scripts(_work_dir)
                        _confirmed: list[str] = []
                        _suspected: list[str] = []
                        for path, res in _scans.items():
                            try:
                                _src = (Path(_work_dir) / path).read_text(
                                    encoding="utf-8", errors="ignore")
                                probe_scan(_src, res)
                            except Exception:
                                pass
                            for s in res.suspects:
                                if s.confirmed == "refuted":
                                    continue
                                tag = f"{path}:{s.line} ({s.name})"
                                (_confirmed if s.confirmed == "confirmed" else _suspected).append(tag)
                        if _confirmed:
                            suspect_pointer = (
                                "\n\nPROVEN self-cancelling by re-execution (metric constant "
                                "across inputs — verifies nothing; the claim it 'passed' is "
                                "UNVERIFIED, fix the check): " + "; ".join(_confirmed[:5])
                            )
                        elif _suspected:
                            suspect_pointer = (
                                "\n\nSTATIC AUDIT flagged these as possibly self-cancelling "
                                "(re-run with perturbed inputs to confirm): "
                                + "; ".join(_suspected[:5])
                            )
                    except Exception:
                        pass
                    swapped.task_description = (
                        "INDEPENDENT REVIEW (you did NOT produce this work). "
                        "Re-execute / re-derive the key numbers yourself and try to REFUTE "
                        "the headline claims. Watch for tautologies (a check that passes for "
                        "all inputs), back-solved targets (a result reverse-engineered from the "
                        "goal), and self-attested 'verified/PASS' verdicts with no independent "
                        f"re-run.{suspect_pointer}\n\nOutstanding work to scrutinise:\n"
                        f"{swapped.task_description}"
                    )
                elif injected in _WRITER_ROLES:
                    # Audit c527e1c2: a starved writer was injected to close a missing
                    # report. Give it a WRITE-ONLY directive so it doesn't inherit the
                    # swapped analytical slot's task (e.g. "re-derive radar") and burn
                    # the turn re-computing instead of producing the deliverable.
                    _missing = "; ".join(evaluation.missing_items[:6]) if evaluation.missing_items else swapped.task_description
                    swapped.task_description = (
                        "WRITE THE DELIVERABLE (you are the technical writer; this is a "
                        "transcription/assembly task, NOT analysis). Consolidate the team's "
                        "already-produced results into the consolidated report file via ONE "
                        "write_file call. Do NOT re-derive, re-compute, or re-verify numbers — "
                        "use the values teammates already committed to the ledger/workspace. "
                        "Where a required result genuinely has no produced value yet, write the "
                        "section with the best available content and explicitly label the gap "
                        f"'PENDING — needs computation'.\n\nSections / items still missing:\n{_missing}"
                    )

        # --- Context injection: DO NOT inject ledger here ---
        # TurnExecutor already injects ledger context per-agent in BOTH
        # thinking phase (line ~282) and execution phase (line ~661).
        # Injecting here too would cause triple-duplication, wasting tokens
        # and confusing agents into thinking they did something 3x.
        #
        # Only inject a BRIEF "do not redo" hint from evaluation notes
        # (not full ledger data) so agents know the high-level picture.
        if evaluation.agent_evaluations:
            completed_notes: list[str] = []
            for ae in evaluation.agent_evaluations:
                if ae.get("completed") and ae.get("notes"):
                    completed_notes.append(
                        f"- [{ae.get('role', ae.get('agent', ''))}]: {ae.get('notes', '')[:200]}"
                    )
            if completed_notes:
                hint = "\n".join(completed_notes[-8:])
                for assignment in assignments:
                    assignment.task_description = (
                        f"{assignment.task_description}\n\n"
                        f"[DO NOT REDO — already completed this task:\n{hint}\n"
                        f"Full context injected separately via ledger.]"
                    )

        # --- Design Constraint Injection ---
        # NOTE: Constraints are ALREADY injected via TODO.md (through TurnExecutor's
        # previous_context injection at line ~169). Appending them again into each
        # agent's task_description caused 100+ duplicates in ACTIVITY.md (6 agents × N turns).
        # Removed redundant injection. Agents receive constraints via:
        #   1. TODO.md (previous_context) — contains Invariants section
        #   2. ROE (roe_context) — contains Hard Constraints
        # Both are injected by TurnExecutor already.

        # --- FIX 2: Repeated Failure Detection ---
        # If an agent failed 2+ consecutive times, inject warning into its assignment
        # so orchestrator/agents try a different approach.
        for assignment in assignments:
            failures = state.failure_tracker.get(assignment.agent_role, [])
            if len(failures) >= 2:
                failure_note = (
                    f" [WARNING: This agent failed {len(failures)} consecutive times. "
                    f"Last error: {failures[-1][:100]}. "
                    f"Use a DIFFERENT strategy/tools than previous attempts.]"
                )
                assignment.task_description += failure_note
                logger.info(
                    "repeated_failure_warning_injected",
                    agent=assignment.agent_role,
                    consecutive_failures=len(failures),
                )

        # ═══ Intra-turn assignment dedup ═══
        # Collapse near-identical assignments before they spawn agents, so two
        # agents don't do the same work (double Telegram escalation / duplicate
        # contingency docs). First occurrence wins.
        if len(assignments) > 1:
            assignments, _dropped = dedupe_assignments(assignments)
            if _dropped:
                logger.info(
                    "duplicate_assignments_collapsed",
                    turn=next_turn_num,
                    dropped=[d.agent_role for d in _dropped],
                    kept=[a.agent_role for a in assignments],
                )

        # ═══ Terminal-state detection (idle instead of doc-churn) ═══
        # If progress is stagnant AND every remaining gap is owned by an external
        # party (owner credential / third-party merge) AND the next focus is not
        # an actionable in-team step, stop. Spawning more agents only produces
        # duplicate "what to do when unblocked" docs.
        #
        # NOTE: evaluate_turn() already wrote this turn's progress into
        # _progress_by_task, so a "current vs stored" delta would always be ~0.
        # Use the recent progress-history RANGE (last 3 turns) instead, mirroring
        # _determine_completion's stagnation logic.
        # A2: shared range-stagnation primitive (window 3, same verdict as before).
        _history = self._progress_history_by_task.get(state.task_id, [])
        _stagnant = (
            state.turn_count >= 3
            and is_stagnant_range(_history, 3, _MIN_PROGRESS_DELTA)
        )
        # An unresolved contradiction is in-team work (resolve it), never terminal.
        _has_unresolved_contradiction = bool(
            [c for c in (evaluation.contradictions or []) if c and c.strip()]
        )
        if not _has_unresolved_contradiction and is_externally_blocked_terminal(
            missing_items=evaluation.missing_items,
            next_turn_focus=evaluation.next_turn_focus,
            stagnant=_stagnant,
        ):
            logger.info(
                "terminal_external_block_detected",
                turn=next_turn_num,
                missing=evaluation.missing_items[:3],
            )
            return None

        execution_mode = _determine_execution_mode(
            assignments,
            sequential_required=user_requires_sequential(state.user_message),
        )

        # Cross-turn semantic loop: same (role, outcome) recurring under varied
        # task text (the hash terminator misses this). De-dup WITHIN each turn so
        # a single turn with several identical same-role results contributes that
        # pair only ONCE — it must recur across DISTINCT turns to count as a loop.
        # The outcome key hashes the agent's WHOLE normalized output
        # (normalize_outcome), not a 120-char prefix, so a fixed boilerplate
        # header can't cause a false match.
        _ro_hist: list[tuple[str, str]] = []
        for _tr in state.turns_completed[-_SEMANTIC_LOOP_TERMINATE_AT * 2:]:
            _seen_this_turn: set[tuple[str, str]] = set()
            for _ar in _tr.agent_results:
                _pair = (
                    (_ar.agent_role or ""),
                    normalize_outcome(getattr(_ar, "output", "") or ""),
                )
                if _pair not in _seen_this_turn:
                    _seen_this_turn.add(_pair)
                    _ro_hist.append(_pair)
        if semantic_loop_terminal(_ro_hist):
            logger.warning(
                "semantic_loop_terminated",
                task_id=state.task_id[:8],
                pair=_ro_hist[-1] if _ro_hist else None,
            )
            # Leave an HONEST trail: the task is being stopped because the same
            # (role, outcome) recurred N times, NOT because it succeeded. Mark it
            # so update_todo writes a "HALTED" handoff instead of "✅ ALL TASKS
            # COMPLETE", and record the dead-end as a blocker.
            evaluation.terminated_reason = "semantic_loop"
            _halt_note = (
                f"HALTED — the same (role, outcome) pair recurred {_SEMANTIC_LOOP_TERMINATE_AT}x "
                f"under different task text. The team is re-producing the same finding in a loop "
                f"that the assignment-hash terminator misses. Stopped to avoid infinite "
                f"repetition. Consolidate what is VERIFIED vs. what remains OPEN; a different "
                f"approach is required to make further progress."
            )
            evaluation.missing_items = list(evaluation.missing_items or [])
            if _halt_note not in evaluation.missing_items:
                evaluation.missing_items.append(_halt_note)
            try:
                await self.update_todo(state.task_id, evaluation)
            except Exception as e:
                logger.debug("halt_todo_update_failed", error=str(e))
            return None

        # FIX C1: Assignment deduplication — detect pipeline-level loops.
        # Hash each assignment and check if same hash appeared 3+ times.
        # If so, the pipeline is looping on the same work → force different approach.
        # H3 (audit 2026-06-29): hash on NORMALIZED task text so the loop-breaker's
        # own "[LOOP DETECTED] …" prefix (injected below) does not change the hash
        # and reset the occurrence counter — otherwise the warn(3)→terminate(5)
        # ladder never reaches its ceiling and the loop runs forever.
        task_hashes = self._assignment_hashes.setdefault(state.task_id, [])
        turn_hash = assignment_loop_hash(assignments)
        task_hashes.append(turn_hash)

        # Check for repeated assignment patterns (same hash 3+ times)
        _occurrences = task_hashes.count(turn_hash)
        if _occurrences >= 3:
            _force_stopped = self._force_stopped_by_task.get(state.task_id, False)
            # Escalation ladder: TERMINATE if the loop has persisted past the
            # warning (or an agent was force-stopped — its approach is exhausted).
            # The old code only ever warned and re-scheduled, reproducing the exact
            # infinite-loop failure mode the brief forbids (task 4c05b7ff: same
            # live-exploit assignment re-scheduled 7+ times after STOP signals).
            if should_terminate_for_loop(_occurrences, _force_stopped):
                logger.warning(
                    "pipeline_loop_terminated",
                    turn=next_turn_num,
                    repeated_hash=turn_hash,
                    occurrences=_occurrences,
                    force_stopped=_force_stopped,
                )
                # Leave an HONEST trail (audit A1/G1-2): the task is being stopped
                # because the same approach looped, NOT because it succeeded. Mark
                # it so update_todo writes a "HALTED" handoff instead of
                # "✅ ALL TASKS COMPLETE", and record the dead-end as a blocker.
                evaluation.terminated_reason = "loop_exhausted"
                _halt_note = (
                    f"HALTED — the same assignment was re-scheduled {_occurrences}x with no "
                    f"progress ({'an agent was force-stopped; ' if _force_stopped else ''}"
                    "approach exhausted). Stopped to avoid an infinite loop. "
                    "Consolidate what is VERIFIED vs. what remains OPEN; a different "
                    "approach is required to make further progress."
                )
                evaluation.missing_items = list(evaluation.missing_items or [])
                if _halt_note not in evaluation.missing_items:
                    evaluation.missing_items.append(_halt_note)
                try:
                    await self.update_todo(state.task_id, evaluation)
                except Exception as e:
                    logger.debug("halt_todo_update_failed", error=str(e))
                return None
            logger.warning(
                "pipeline_loop_detected",
                turn=next_turn_num,
                repeated_hash=turn_hash,
                occurrences=_occurrences,
            )
            # Force different approach: prepend directive to all assignments
            for a in assignments:
                a.task_description = (
                    "[LOOP DETECTED: This exact assignment was given 3+ times before "
                    "with NO progress. You MUST use a COMPLETELY DIFFERENT approach. "
                    "If your previous method failed, try: different tools, different target, "
                    "different strategy, or report that this objective is BLOCKED.]\n\n"
                    + a.task_description
                )

        return TurnPlan(
            turn_number=next_turn_num,
            objective=evaluation.next_turn_focus or "Address remaining gaps",
            assignments=assignments,
            execution_mode=execution_mode,
            milestone=evaluation.next_turn_focus[:100] if evaluation.next_turn_focus else "Continue execution",
        )

    def _read_decisions(self, task_id: str) -> str:
        """Read DECISIONS.md — prior orchestrator + evolution assessments.

        Returns the recent entries (writer keeps the last 3) IN FULL so the
        Orchestrator sees its own complete prior reasoning and Evolution's advice.
        The 3-turn window is itself the compression step; cutting entry content on
        top of that makes conclusions look unfinished and invites the Orchestrator
        to re-derive decisions it already made.

        A wide backstop (_DECISIONS_READ_BACKSTOP) guards against a pathological
        single turn bloating the prompt: if the body exceeds it, the OLDEST whole
        entries are dropped — entries are never cut mid-content.
        """
        path = task_paths.context_file(task_id, "DECISIONS.md")
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            # Skip header, return body (last 3 entries, already bounded by writer).
            # Line-anchored ``^## Turn`` so an embedded ``### Turn N`` quoted in
            # an entry's reasoning is never mistaken for an entry boundary
            # (task 3c44a93f bug #2 family).
            body_match = re.search(r"(?m)^## Turn \d+", content)
            if not body_match:
                return ""
            body = content[body_match.start():]
            if len(body) <= _DECISIONS_READ_BACKSTOP:
                return body
            # Backstop hit: drop oldest whole entries until under budget,
            # always keeping the most recent entry intact.
            entries = [e for e in re.split(r"(?m)(?=^## Turn \d+)", body) if e.strip()]
            kept: list[str] = []
            total = 0
            for entry in reversed(entries):
                if kept and total + len(entry) > _DECISIONS_READ_BACKSTOP:
                    break
                kept.insert(0, entry)
                total += len(entry)
            return "".join(kept)
        except Exception:
            return ""

    def _read_task_brief(self, task_id: str) -> str:
        """Read TASK_BRIEF.md — the original objectives, team, and execution plan.

        This is the authoritative source for WHAT the task is trying to achieve.
        Must be read every evaluation to prevent drift from original goals.
        """
        path = task_paths.context_file(task_id, "TASK_BRIEF.md")
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            # Cap at 3KB — TASK_BRIEF should be concise by design
            return content[:3000]
        except Exception:
            return ""

    def _read_contract(self, task_id: str) -> str:
        """Read CONTRACT.md — the deliverable acceptance standard.

        This is what the orchestrator evaluates completion against: the concrete
        artifacts the user must receive and how each is verified.
        """
        path = task_paths.context_file(task_id, "CONTRACT.md")
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")[:3000]
        except Exception:
            return ""

    def _read_todo_current_state(self, task_id: str) -> str:
        """Read TODO.md — the current progress state (single source of truth).

        TODO.md is rewritten each turn with: confirmed achievements, blocked items,
        failed approaches, and next actions. This gives Orchestrator continuity.
        """
        path = task_paths.context_file(task_id, "TODO.md")
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8")
            return content[:5000]
        except Exception:
            return ""

    def _read_progress_tracker(self, task_id: str) -> str:
        """Read PROGRESS_TRACKER.md (legacy, kept for accumulation merge)."""
        tracker_path = task_paths.context_file(task_id, "PROGRESS_TRACKER.md")
        if not tracker_path.exists():
            return ""
        try:
            return tracker_path.read_text(encoding="utf-8")[:5000]
        except Exception:
            return ""

    def _read_todo_invariants(self, task_id: str) -> str:
        """Read the Invariants section from TODO.md.

        These are the constraints/tech-stack decisions made at planning time
        that must be maintained throughout all turns. Without this, orchestrator
        forgets the original design after a few turns.
        """
        todo_path = task_paths.context_file(task_id, "TODO.md")

        if not todo_path.exists():
            return ""

        try:
            content = todo_path.read_text(encoding="utf-8")

            # Extract Invariants section
            idx = content.find("## Invariants")
            if idx < 0:
                # Fallback: extract first ## Execution Plan section header for context
                plan_idx = content.find("## Execution Plan")
                if plan_idx >= 0:
                    # Get just the plan section headers (turn objectives)
                    plan_section = content[plan_idx:plan_idx + 1000]
                    return f"## Execution Plan (from TODO)\n{plan_section[:800]}"
                return ""

            # Get invariants section until next ## heading
            end_idx = content.find("\n## ", idx + 10)
            if end_idx < 0:
                end_idx = min(idx + 1000, len(content))

            return content[idx:end_idx].strip()

        except Exception:
            return ""


    async def write_task_brief(
        self,
        task_id: str,
        user_message: str,
        plan_data: dict[str, Any],
    ) -> None:
        """Write TASK_BRIEF.md to workspace."""
        content = build_task_brief(
            user_message=user_message,
            intent_analysis=plan_data.get("intent_analysis", ""),
            constraints=plan_data.get("constraints", []),
            selected_agents=plan_data.get("selected_agents", []),
            execution_plan=plan_data.get("execution_plan", []),
        )
        path = task_paths.context_file(task_id, "TASK_BRIEF.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def write_roe(
        self,
        task_id: str,
        plan_data: dict[str, Any],
        user_query: str = "",
    ) -> None:
        """Write ROE.md to workspace.

        Uses LLM classification from orchestrator's plan_data to determine ROE type.
        For security tasks: generates comprehensive pentest/intel ROE with playbook.
        For non-security: generates standard operational ROE.
        """
        task_type = plan_data.get("task_type", "general")

        if task_type in ("security", "intelligence"):
            # Security/intel task: LLM classifies pentest vs intel + extracts params.
            # Pass the planner's contract mode so the generator can fail-closed when
            # the deliverable intent is non-offensive (CC-10/CC-12).
            _contract = plan_data.get("deliverable_contract") or {}
            _contract_mode = _contract.get("mode", "") if isinstance(_contract, dict) else ""
            from app.security.roe_generator import generate_roe
            content = await generate_roe(
                task_description=user_query,
                task_id=task_id,
                llm=self._llm,
                task_type=task_type,
                contract_mode=_contract_mode,
            )
        else:
            # Non-security task: standard operational ROE
            content = build_roe(
                task_type=task_type,
                language=plan_data.get("language", "auto"),
                constraints=plan_data.get("constraints", []),
            )
        path = task_paths.context_file(task_id, "ROE.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def write_todo(
        self,
        task_id: str,
        plan_data: dict[str, Any],
    ) -> None:
        """Write initial TODO.md to workspace.

        Created once at task start with the execution plan checklist.
        Includes constraints as invariants for architecture drift prevention.
        """
        content = build_todo(
            execution_plan=plan_data.get("execution_plan", []),
            selected_agents=plan_data.get("selected_agents", []),
            constraints=plan_data.get("constraints", None),
        )
        path = task_paths.context_file(task_id, "TODO.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def write_contract(
        self,
        task_id: str,
        plan_data: dict[str, Any],
    ) -> None:
        """Write CONTRACT.md — the deliverable acceptance standard.

        The contract is the answer to "what concrete thing does the user receive?".
        It is binding for ALL agents and becomes the standard the orchestrator judges
        completion against. Written once at task start, alongside ROE.md.
        Skips writing if the planner produced no contract (degrades gracefully).
        """
        contract = plan_data.get("deliverable_contract")
        if not isinstance(contract, dict) or not contract.get("artifacts"):
            return
        content = build_contract(contract)
        path = task_paths.context_file(task_id, "CONTRACT.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def load_contract_skills(self, plan_data: dict[str, Any]) -> None:
        """Verify which contract-named skills exist and log the resolution.

        Skills are loaded on demand by agents (via the load_skill tool) to avoid bloating
        every agent's context — this method does NOT inject skill content. It only checks
        each id in `skills_needed`: ones that resolve are confirmed available; ones that
        don't are logged as missing and left for agents to scaffold on demand via
        scaffold_skill (the agent that hits the domain writes a far better playbook than a
        blank stub would). Kept as an explicit step so the resolution is observable in logs.
        """
        contract = plan_data.get("deliverable_contract")
        if not isinstance(contract, dict):
            return
        needed = contract.get("skills_needed") or []
        if not needed:
            return
        try:
            from app.skills import get_skills_hub
            hub = get_skills_hub()
        except Exception:
            return
        resolved, missing = [], []
        for sid in needed:
            if not isinstance(sid, str) or not sid.strip():
                continue
            sid = sid.strip()
            if hub.get_skill(sid) or hub.get_skill_by_command(f"/{sid}"):
                resolved.append(sid)
            else:
                missing.append(sid)
        logger.info(
            "contract_skills_resolved",
            resolved=resolved,
            missing=missing,
        )

    async def update_todo(
        self,
        task_id: str,
        evaluation: "EvaluationResult",
        user_message: str = "",
    ) -> None:
        """Rewrite TODO.md as the single source of truth for task progress.

        TODO.md is REWRITTEN each turn (not appended). It contains:
        1. Original plan header (team, invariants, execution plan) — preserved
        2. Accumulated status: confirmed achievements, blocked items, failed approaches
        3. Current turn evaluation + next actions

        This keeps TODO.md focused, bounded, and authoritative.
        """
        path = task_paths.context_file(task_id, "TODO.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            full_existing = path.read_text(encoding="utf-8") if path.exists() else ""
            # Preserve original plan header (everything before first "## Turn" or "## Current Status")
            header = full_existing
            for marker in ("\n## Turn ", "\n## Current Status"):
                idx = full_existing.find(marker)
                if idx != -1:
                    header = full_existing[:idx]
                    break

            # Build accumulated status from evaluation + prior state
            status_lines = [
                f"\n## Current Status — Turn {evaluation.turn_number} | Progress: {evaluation.progress_pct}%\n"
            ]

            # Gather achievements/blocked/failed from evaluation
            achievements = []
            blocked = []
            failed = []
            pending = []

            for obj in (evaluation.objective_tracking or []):
                s = obj.get("status", "").upper()
                d = obj.get("objective", "")
                if s == "DONE":
                    achievements.append(d)
                elif s == "BLOCKED":
                    blocked.append(d)
                elif s == "IN_PROGRESS":
                    pending.append(d)

            for ae in (evaluation.agent_evaluations or []):
                if ae.get("completed"):
                    s = ae.get("agent", "") + ": " + ae.get("notes", ae.get("quality", ""))
                    if s not in achievements:
                        achievements.append(s)

            for item in (evaluation.missing_items or []):
                il = item.lower()
                clean = item.replace("[Evolution] ", "")
                if any(k in il for k in ("blocked", "impossible", "not applicable", "unreachable", "patched")):
                    if clean not in blocked:
                        blocked.append(clean)
                elif any(k in il for k in ("never used", "tried and failed", "never executed")):
                    if clean not in failed:
                        failed.append(clean)
                else:
                    pending.append(clean)

            # Merge with prior TODO state to not lose accumulated items
            for line in full_existing.split("\n"):
                ls = line.strip()
                if ls.startswith("- [x]"):
                    item = ls[len("- [x] "):].strip()
                    if item and item not in achievements:
                        achievements.append(item)
                elif ls.startswith("- [!]"):
                    item = ls[len("- [!] "):].strip()
                    if item and item not in blocked:
                        blocked.append(item)
                elif ls.startswith("- [-]"):
                    item = ls[len("- [-] "):].strip()
                    if item and item not in failed:
                        failed.append(item)

            if achievements:
                status_lines.append("### Completed (DO NOT repeat)")
                for a in achievements[:30]:  # FIX A3: Increased from 15 → 30
                    status_lines.append(f"- [x] {a}")

            if blocked:
                status_lines.append("\n### Blocked / Infeasible (STOP trying)")
                for b in blocked[:10]:
                    status_lines.append(f"- [!] {b}")

            if failed:
                status_lines.append("\n### Tried & Failed (do NOT retry same approach)")
                for f_item in failed[:10]:
                    status_lines.append(f"- [-] {f_item}")

            if pending:
                status_lines.append("\n### Still Needed")
                for p in pending[:10]:
                    status_lines.append(f"- [ ] {p}")

            # Next focus — also surfaced on an honest termination (where
            # is_complete=True but the loop was halted, not finished) so the
            # verified-vs-open handoff reaches the final report.
            if (not evaluation.is_complete or evaluation.terminated_reason) and evaluation.next_turn_focus:
                focus = evaluation.next_turn_focus
                if "[Evolution:" in focus:
                    focus = focus.split("[Evolution:")[0].strip()
                if focus:
                    status_lines.append(f"\n### Next Focus\n{focus}")

            if evaluation.terminated_reason == "lawful_refusal":
                # H2c (audit review 2026-06-29): a lawful refusal is the CORRECT
                # terminal outcome, not a steering failure. Record it as a clean,
                # intentional stop — never as a "HALTED because steering failed" or
                # a "✅ ALL TASKS COMPLETE" that implies the offensive ask was done.
                status_lines.append(
                    "\n## ✅ CORRECTLY REFUSED — terminal safety/authorization stop\n"
                    "The request was declined for safety/authorization reasons. This is the "
                    "intended outcome: the task is COMPLETE as a lawful refusal, not a failed "
                    "objective. See the refusal explanation and the lawful path forward above."
                )
            elif evaluation.terminated_reason:
                # Honest stop: steering became impossible (evaluator blind / loop
                # exhausted). Do NOT claim success — record the halt truthfully.
                status_lines.append(
                    "\n## ⚠ HALTED — verified-vs-open handoff "
                    f"(reason: {evaluation.terminated_reason})\n"
                    "Stopped because automated steering was no longer possible, NOT because "
                    "every objective was met. See Still Needed above for what remains open."
                )
            elif evaluation.is_complete:
                status_lines.append("\n## ✅ ALL TASKS COMPLETE")

            status_lines.append("")
            path.write_text(header + "\n".join(status_lines), encoding="utf-8")
        except Exception as e:
            logger.warning("todo_update_failed", error=str(e), task_id=task_id[:8])

    async def update_progress_tracker(
        self,
        task_id: str,
        evaluation: "EvaluationResult",
        state: "ExecutionState",
        user_message: str = "",
    ) -> None:
        """Rewrite PROGRESS_TRACKER.md with current consolidated state.

        Unlike TODO.md (append-only log), this is the SINGLE SOURCE OF TRUTH
        that agents read first. It clearly separates:
        - What's DONE (don't repeat)
        - What's BLOCKED (don't try again)
        - What FAILED (don't retry same approach)
        - What to do NEXT (exactly)
        """
        # Gather achievements from objective_tracking (most authoritative source)
        confirmed_achievements = []
        confirmed_blocked = []
        tried_and_failed = []
        next_actions = []

        for obj in (evaluation.objective_tracking or []):
            status = obj.get("status", "").upper()
            desc = obj.get("objective", "")
            if status == "DONE":
                confirmed_achievements.append(desc)
            elif status == "BLOCKED":
                confirmed_blocked.append(desc)

        # Also gather from agent evaluations
        for ae in (evaluation.agent_evaluations or []):
            if ae.get("completed"):
                summary = ae.get("agent", "") + ": " + ae.get("notes", ae.get("quality", ""))
                if summary not in confirmed_achievements:
                    confirmed_achievements.append(summary)

        # Extract blocked/failed from missing_items
        for item in (evaluation.missing_items or []):
            item_lower = item.lower()
            if any(kw in item_lower for kw in ("blocked", "impossible", "not applicable", "patched", "hardened", "unreachable")):
                if item not in confirmed_blocked:
                    confirmed_blocked.append(item.replace("[Evolution] ", ""))
            elif any(kw in item_lower for kw in ("never used", "never executed", "never attempted", "tried and failed")):
                tried_and_failed.append(item.replace("[Evolution] ", ""))
            else:
                next_actions.append(item.replace("[Evolution] ", ""))

        # Merge with prior tracker to accumulate across turns (don't lose history)
        prior_tracker = self._read_progress_tracker(task_id)
        if prior_tracker:
            for line in prior_tracker.split("\n"):
                line_stripped = line.strip("- ✅❌⚠️ ").strip()
                if not line_stripped:
                    continue
                if line.startswith("- ✅") and line_stripped not in confirmed_achievements:
                    confirmed_achievements.append(line_stripped)
                elif line.startswith("- ❌") and line_stripped not in confirmed_blocked:
                    confirmed_blocked.append(line_stripped)
                elif line.startswith("- ⚠️") and line_stripped not in tried_and_failed:
                    tried_and_failed.append(line_stripped)

        # If next_turn_focus exists, add as primary next action
        if evaluation.next_turn_focus:
            focus = evaluation.next_turn_focus
            if "[Evolution:" in focus:
                focus = focus.split("[Evolution:")[0].strip()
            if focus:
                next_actions.insert(0, focus)

        # FIX A3: Increased caps to prevent losing history on long tasks
        confirmed_achievements = confirmed_achievements[:20]
        confirmed_blocked = confirmed_blocked[:15]
        tried_and_failed = tried_and_failed[:15]
        next_actions = next_actions[:5]

        content = build_progress_tracker(
            turn_number=evaluation.turn_number,
            progress_pct=evaluation.progress_pct,
            confirmed_achievements=confirmed_achievements,
            confirmed_blocked=confirmed_blocked,
            tried_and_failed=tried_and_failed,
            next_actions=next_actions,
            original_objective=user_message[:200] if user_message else "",
        )
        path = task_paths.context_file(task_id, "PROGRESS_TRACKER.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning("progress_tracker_update_failed", error=str(e), task_id=task_id[:8])

    def _extract_partial_data(self, tool_calls: list[ToolCall]) -> str:
        """Extract useful data from successful tool calls when agent output failed.

        Fix 4: When an agent crashes after tools succeeded, we recover
        the tool outputs so the next turn doesn't refetch the same data.
        """
        useful_parts: list[str] = []
        for tc in tool_calls:
            if tc.success and tc.output and len(tc.output) > 100:
                # Take first 1000 chars of each successful tool output
                useful_parts.append(f"[{tc.tool_name}]: {tc.output[:1000]}")
        return "\n".join(useful_parts[:10])  # Max 10 tool results

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event via callback."""
        if self._event_callback is None:
            return
        try:
            await self._event_callback({"event_type": event_type, **data})
        except Exception:
            pass


def _build_skills_catalog(max_skills: int = 60) -> str:
    """Build a compact catalog of available skills for the planning prompt.

    Lists each skill as `- <name>: <description>` so the orchestrator can pick
    which ones to load on demand into the deliverable contract. Best-effort:
    returns "" if the skills hub is unavailable.
    """
    try:
        from app.skills import get_skills_hub
        skills = get_skills_hub().list_skills()
    except Exception:
        return ""

    if not skills:
        return ""

    lines: list[str] = []
    for s in skills[:max_skills]:
        name = s.get("name", "")
        desc = (s.get("description", "") or "").replace("\n", " ").strip()
        if name:
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from an LLM response.

    Delegates to the shared lenient parser (audit A8): both real audited tasks
    collapsed to a 1-researcher fallback plan because the previous local copy of
    this function returned None on truncated/brace-in-prose/trailing-comma output.
    The shared parser recovers those shapes so a complex plan survives.
    """
    return parse_json_response(text)


def _normalize_agent_names(plan_data: dict[str, Any]) -> None:
    """Fix agent names where LLM returned name == role (e.g. 'developer' instead of 'Code Engineer')."""
    from app.services.agent_spawner import AGENT_TEMPLATES

    for agent in plan_data.get("selected_agents", []):
        role = agent.get("role", "")
        name = agent.get("name", "")
        if not name or name.lower() == role.lower() or name.lower() == role.replace("_", " "):
            agent["name"] = AGENT_TEMPLATES.get(role, {}).get("default_name", role.title().replace("_", " "))


def _build_fallback_plan(message: str) -> dict[str, Any]:
    """Create a minimal fallback plan when LLM planning fails.

    Security-aware (audit b2c44b12): when the LLM plan fails to parse, a HARDCODED
    "general" task_type silently strips an offensive request's security nature —
    so ``_enforce_security_plan`` (gated on task_type in {security,intelligence})
    never rebuilds the pentester/recon/exploit team, and a lone researcher writes
    a generic "simulation/playbook" instead of the requested real engagement.
    Classify with the FORMAT-based offensive-intent backstop (never keyword soup)
    so the fallback preserves a pentest's nature; benign/defensive requests stay
    "general" (no false-positive escalation — e.g. a counter-UAS sensor design).
    """
    from app.orchestration.ledger_extract import smart_truncate

    task_type = "general"
    try:
        from app.security.authorization_gate import detect_offensive_intent
        if detect_offensive_intent(message):
            task_type = "security"
    except Exception:
        pass

    return {
        "intent_analysis": smart_truncate(message, 300),
        "task_type": task_type,
        "complexity": "expert" if task_type == "security" else "medium",
        "selected_agents": [
            {"role": "researcher", "name": "Research Specialist", "primary_responsibility": "Research and gather information"},
        ],
        "execution_plan": [_build_fallback_turn(message)],
        "constraints": [],
        "language": "auto",
    }


def _build_fallback_turn(message: str) -> dict[str, Any]:
    """Create a single fallback turn."""
    from app.orchestration.ledger_extract import smart_truncate
    return {
        "turn": 1,
        "objective": "Research and answer the user's question",
        "milestone": "Provide a comprehensive answer",
        "execution_mode": "parallel",
        "assignments": [
            {
                "agent_role": "researcher",
                "task": smart_truncate(message, 600),
                "success_criteria": "Provide accurate, well-sourced answer",
                "dependencies": [],
                "tools_hint": ["web_search", "fetch_url"],
            }
        ],
    }


# E-R6 (tri-run audit 2026-07-02): multi-word phrases signalling a USER'S EXPLICIT
# strict-sequential constraint ("one phase at a time, never skip"). Anchored on
# whole phrases — the accepted pattern (mirrors termination._SAFETY_REFUSAL_SIGNALS)
# — never a bare "sequential"/"phase"/"step" keyword, so an ordinary request that
# merely mentions a "phase" or does work "in steps" is NOT misread as a hard
# ordering rule. EN + VI. Matched on a whitespace-normalised, lowercased message.
_SEQUENTIAL_CONSTRAINT_SIGNALS = (
    # English
    "one phase at a time",
    "one at a time",
    "strictly in order",
    "strict order",
    "in strict sequence",
    "do not skip phase",
    "don't skip phase",
    "never skip a phase",
    "never skip phase",
    "complete each phase before",
    "one step at a time",
    "do not jump ahead",
    # Vietnamese
    "từng phase một lần",
    "từng phase một",
    "không bao giờ nhảy phase",
    "không được nhảy phase",
    "không nhảy phase",
    "theo đúng thứ tự",
    "đúng thứ tự sau",
    "từng bước một",
    "không nhảy bước",
    "tuần tự từng phase",
)


def user_requires_sequential(user_message: str | None) -> bool:
    """Whether the user EXPLICITLY demanded strict phase-by-phase / sequential work.

    Multi-word-phrase match (never a bare keyword) so a request that just mentions a
    "phase" or does work "in steps" is not tripped — only a genuine ordering
    constraint ("one phase at a time, never skip a phase") returns True. When True,
    the orchestrator biases the turn toward sequential execution and tells the
    planner to honor the ordering (E-R6: the pln.co.id run ran phases in parallel
    despite this exact constraint).
    """
    if not user_message:
        return False
    norm = " ".join(user_message.lower().split())
    return any(sig in norm for sig in _SEQUENTIAL_CONSTRAINT_SIGNALS)


def _determine_execution_mode(
    assignments: list[AgentAssignment],
    sequential_required: bool = False,
) -> str:
    """Determine whether assignments should run parallel or sequential.

    ``sequential_required`` (E-R6): when the USER explicitly asked for strict
    phase-by-phase work, force sequential regardless of the technical independence
    checks below — the user's ordering constraint outranks the parallelism
    optimisation.

    Rules (sequential wins if ANY apply):
    1. Any assignment has explicit dependencies → sequential
    2. Only 1 assignment → sequential (trivial, no benefit from parallel)
    3. Assignments share write-capable tools (file_write, code_write, save_to_workspace)
       → they'll conflict in the same workspace → sequential
    4. One assignment's task_description references another's role
       → implicit dependency → sequential

    Only truly independent work (different domains, no shared resources)
    should run parallel.
    """
    # Rule 0 (E-R6): a user's explicit strict-sequential constraint wins outright.
    if sequential_required:
        return "sequential"

    if len(assignments) <= 1:
        return "sequential"

    # Rule 1: explicit dependencies
    if any(a.dependencies for a in assignments):
        return "sequential"

    # Rule 3: shared write tools indicate workspace conflict
    _WRITE_TOOLS = {
        "file_write", "code_write", "save_to_workspace", "write_file",
        "create_file", "edit_file", "task_doc_write", "task_doc_append",
    }
    writers = [
        a for a in assignments
        if set(a.tools_hint) & _WRITE_TOOLS
    ]
    if len(writers) > 1:
        return "sequential"

    # Rule 4: implicit reference between assignments (one mentions another's role)
    roles = {a.agent_role.lower() for a in assignments}
    for a in assignments:
        desc_lower = a.task_description.lower()
        other_roles = roles - {a.agent_role.lower()}
        if any(role in desc_lower for role in other_roles):
            return "sequential"

    # FIX B3: Rule 5 — overlapping tool hints indicate duplicate work risk
    # If 2+ agents hint at the SAME tools, they'll likely fetch similar data → sequential
    _OVERLAP_TOOLS = {"web_search", "http_request", "fetch_url", "run_command"}
    overlap_agents = [
        a for a in assignments
        if set(a.tools_hint) & _OVERLAP_TOOLS
    ]
    if len(overlap_agents) >= 2:
        # Check if they target similar domains (heuristic: shared keywords in task)
        task_words_sets = [set(a.task_description.lower().split()[:30]) for a in overlap_agents]
        if len(task_words_sets) >= 2:
            shared = task_words_sets[0] & task_words_sets[1]
            # If >20% word overlap → likely same target → sequential
            avg_size = max(1, (len(task_words_sets[0]) + len(task_words_sets[1])) / 2)
            if len(shared) / avg_size > 0.2:
                return "sequential"

    return "parallel"


def _convert_to_turn_plan(
    turn_data: dict[str, Any], turn_number: int, user_message: str = ""
) -> TurnPlan:
    """Convert raw plan dict to TurnPlan model."""
    from app.services.agent_spawner import AGENT_TEMPLATES

    assignments: list[AgentAssignment] = []
    for a in turn_data.get("assignments", []):
        _role = a.get("agent_role", "researcher")
        _raw_name = a.get("agent_name", "")
        _name = (
            _raw_name
            if _raw_name and _raw_name.lower() != _role.lower()
               and _raw_name.lower() != _role.replace("_", " ")
            else AGENT_TEMPLATES.get(_role, {}).get("default_name", _role.title().replace("_", " "))
        )
        assignments.append(AgentAssignment(
            agent_role=_role,
            agent_name=_name,
            task_description=a.get("task", ""),
            success_criteria=a.get("success_criteria", ""),
            dependencies=a.get("dependencies", []),
            tools_hint=a.get("tools_hint", []),
        ))

    # Validate execution_mode: even if LLM chose "parallel", enforce sequential
    # when assignments have conflicts (LLM often over-parallelizes).
    # E-R6: a user's explicit strict-sequential constraint forces sequential and
    # cannot be overridden by an LLM "parallel" choice.
    _seq_required = user_requires_sequential(user_message)
    llm_mode = turn_data.get("execution_mode", "parallel")
    if _seq_required:
        effective_mode = "sequential"
    else:
        effective_mode = llm_mode if llm_mode == "sequential" else _determine_execution_mode(assignments)

    return TurnPlan(
        turn_number=turn_number,
        objective=turn_data.get("objective", ""),
        assignments=assignments,
        execution_mode=effective_mode,
        milestone=turn_data.get("milestone", ""),
    )
