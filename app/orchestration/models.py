"""
MekongAI Nexus - Orchestration Data Models.

Pure data classes for the turn-based execution pipeline.
No business logic — just structure and serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class AgentAssignment:
    """Task assignment for one agent within a turn."""

    agent_role: str
    agent_name: str
    task_description: str
    dependencies: list[str] = field(default_factory=list)
    tools_hint: list[str] = field(default_factory=list)
    success_criteria: str = ""


@dataclass
class TurnPlan:
    """Plan issued by Orchestrator for one turn."""

    turn_number: int
    objective: str
    assignments: list[AgentAssignment] = field(default_factory=list)
    execution_mode: str = "parallel"  # "sequential" | "parallel"
    milestone: str = ""


@dataclass
class AgentThinking:
    """Agent's reasoning output during thinking phase."""

    agent_name: str
    agent_role: str
    thinking: str = ""
    challenges: list[str] = field(default_factory=list)
    supports: list[str] = field(default_factory=list)


@dataclass
class ToolCall:
    """Record of a single tool invocation."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    success: bool = True
    execution_time_ms: float = 0.0


@dataclass
class AgentResult:
    """Complete result from one agent's execution in a turn."""

    agent_name: str
    agent_role: str
    thinking: AgentThinking = field(default_factory=lambda: AgentThinking("", ""))
    tool_calls: list[ToolCall] = field(default_factory=list)
    output: str = ""
    success: bool = True
    error: str = ""
    # Ground-truth "did work reach disk?" signal (E3). True iff a PRODUCE tool
    # (write_file / execute_code / ...) ran successfully this turn. Distinct from
    # `success`: a prose-only analysis/wrap-up turn is legitimately success=True
    # but produced_deliverable=False. The loop gate (E7) consumes this to tell
    # "N turns of talk, nothing produced" from real progress.
    produced_deliverable: bool = False


@dataclass
class EvaluationResult:
    """Orchestrator's evaluation of a completed turn."""

    turn_number: int
    agent_evaluations: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    progress_pct: int = 0
    is_complete: bool = False
    next_turn_focus: str = ""
    next_assignments: list[dict[str, Any]] = field(default_factory=list)
    evaluation_text: str = ""
    # Objective-based tracking (each user objective → status)
    objective_tracking: list[dict[str, str]] = field(default_factory=list)
    # Look-ahead forecast (what turns N+1, N+2 will need)
    forecast: str = ""
    # Honest-termination marker (audit A1/A2). Set to a short reason ("eval_blind",
    # "loop_exhausted") when the loop is stopped because steering is impossible —
    # NOT because the work is genuinely done. update_todo uses it to write an honest
    # "HALTED" handoff instead of "✅ ALL TASKS COMPLETE".
    terminated_reason: str = ""


@dataclass
class TurnResult:
    """Complete result of one turn execution."""

    turn_number: int
    agent_results: list[AgentResult] = field(default_factory=list)
    orchestrator_evaluation: Optional[EvaluationResult] = None
    evolution_insight: Optional["EvolutionInsight"] = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ExecutionState:
    """Mutable state tracking the entire task execution."""

    task_id: str
    user_message: str = ""
    turns_completed: list[TurnResult] = field(default_factory=list)
    current_turn: int = 0
    total_agents: list[str] = field(default_factory=list)
    status: str = "planning"  # planning | executing | evaluating | complete | failed
    is_complete: bool = False
    error: Optional[str] = None
    # Task complexity from the orchestrator's plan (simple | medium | complex | expert).
    # Used to scale effort-bearing loops (e.g. orchestrator exploration rounds) to the task.
    complexity: str = "medium"
    # Task type from the orchestrator's plan (general | security | intelligence).
    # Persisted so the authorization gate (audit C2) can re-run on resume, not just
    # on first launch.
    task_type: str = "general"
    # Measurable success bar derived from the planner's deliverable_contract (audit
    # A7). Persisted so resume + downstream (completion/report) read one source of
    # truth instead of re-echoing the prompt.
    success_criteria: str = ""

    # --- Intelligence fields ---
    # Partial data recovered from agents that crashed after successful tool calls
    collected_data_cache: list[dict[str, Any]] = field(default_factory=list)
    # Track consecutive failures per agent: {agent_role: [error_summary, ...]}
    failure_tracker: dict[str, list[str]] = field(default_factory=dict)
    # Set when resuming from checkpoint — indicates the turn we resume from
    _resumed_from_turn: int = 0
    # M4 FIX: Persist objective tracking across turns for stable progress calculation
    objective_tracking: list[dict[str, str]] = field(default_factory=list)
    # M4 FIX: Progress history per turn for monotonic/stable display
    progress_history: dict[int, int] = field(default_factory=dict)
    # E-R5: the LLM's SELF-graded progress per turn, recorded BEFORE the
    # ground-truth caps (artifact/self-verify). Stagnation detection reads THIS so a
    # cap that pins delivered-% low (gasifier 10% bug) can't fake a stuck task; the
    # capped ``progress_history`` above still drives UI + the completion gate.
    self_progress_history: dict[int, int] = field(default_factory=dict)

    @property
    def turn_count(self) -> int:
        return len(self.turns_completed)

    def get_previous_context_summary(self, max_chars: int = 12000) -> str:
        """Build summary of previous turns for worker agent context.

        Structure (priority order):
        1. Original user request (always included, never truncated)
        2. "What has been accomplished" running list with conclusions
        3. Recent turns detailed (last 3: agent CONCLUSIONS + evaluation)
        4. Older turns: agent conclusions + progress %

        Total budget: max_chars (default 12000).
        NOTE: This is now a FALLBACK — primary context comes from ContextLedger.
        When ledger is active, this budget is reduced to 6000 chars.

        KEY DESIGN: Extracts agent CONCLUSIONS (tail of output) rather than
        just the head, because conclusions contain the actionable findings
        that subsequent turns need to build upon.
        """
        if not self.turns_completed:
            return ""

        parts: list[str] = []
        remaining = max_chars

        # ANCHOR: Original user message — agent must always know the goal
        goal_section = f"## ORIGINAL GOAL\n{self.user_message}\n"
        parts.append(goal_section)
        remaining -= len(goal_section)

        # Accomplished items with conclusions (prevents re-work AND carries findings)
        accomplished: list[str] = []
        for turn in self.turns_completed:
            ev = turn.orchestrator_evaluation
            if ev:
                for ae in ev.agent_evaluations:
                    if ae.get("completed"):
                        accomplished.append(
                            f"T{turn.turn_number} [{ae.get('agent', '')}]: "
                            f"{ae.get('notes', '')[:300]}"
                        )

        if accomplished and remaining > 500:
            acc_section = "\n## ALREADY COMPLETED (do NOT redo)\n"
            for item in accomplished[-10:]:
                line = f"- {item}\n"
                if len(line) > remaining - 200:
                    break
                acc_section += line
            parts.append(acc_section)
            remaining -= len(acc_section)

        # Turn details: older turns get conclusions, recent turns get full detail
        turn_parts: list[str] = []
        total_turns = len(self.turns_completed)
        for idx, turn in enumerate(self.turns_completed):
            if remaining < 300:
                break

            is_recent = idx >= total_turns - 3  # Last 3 turns get full detail
            if is_recent:
                turn_summary = f"\n## Turn {turn.turn_number}\n"
                for result in turn.agent_results:
                    per_agent_budget = min(3000, remaining // max(1, len(turn.agent_results)))
                    agent_line = f"- **{result.agent_name}** ({result.agent_role}):\n"
                    if result.output:
                        conclusion = _extract_conclusion(result.output, per_agent_budget)
                        agent_line += f"  {conclusion}\n"
                    else:
                        agent_line += "  (no output)\n"
                    turn_summary += agent_line

                if turn.orchestrator_evaluation:
                    eval_text = turn.orchestrator_evaluation.evaluation_text[:300]
                    turn_summary += (
                        f"- **Evaluation** ({turn.orchestrator_evaluation.progress_pct}%): "
                        f"{eval_text}\n"
                    )
            else:
                # Older turns: still extract conclusions but more concise
                turn_summary = f"\n## Turn {turn.turn_number}\n"
                for result in turn.agent_results:
                    if result.output and len(result.output) > 50:
                        per_agent_budget = min(1500, remaining // max(1, len(turn.agent_results) + 1))
                        conclusion = _extract_conclusion(result.output, per_agent_budget)
                        turn_summary += f"- **{result.agent_name}**: {conclusion}\n"
                if turn.orchestrator_evaluation:
                    ev = turn.orchestrator_evaluation
                    turn_summary += (
                        f"- Progress: {ev.progress_pct}% | "
                        f"{'COMPLETE' if ev.is_complete else 'CONTINUE'} | "
                        f"{ev.next_turn_focus[:150] if ev.next_turn_focus else ''}\n"
                    )

            if len(turn_summary) > remaining:
                # Try to fit at least a truncated version
                if remaining > 500:
                    turn_summary = turn_summary[:remaining - 50] + "\n[...truncated]\n"
                else:
                    break
            turn_parts.append(turn_summary)
            remaining -= len(turn_summary)

        parts.extend(turn_parts)
        return "\n".join(parts)

    def get_accumulated_briefing(self, max_chars: int = 20000) -> str:
        """Build accumulated briefing for Orchestrator and Evolution Agent.

        Unlike get_previous_context_summary() which is for worker agents,
        this provides a COMPREHENSIVE view for meta-agents that need full context
        to make correct decisions about what to do next.

        Includes:
        - Original user message (FULL — never truncated)
        - Per-turn: agent conclusions (tail-priority), evaluation, missing items
        - Accumulated "what has been accomplished" with detailed notes
        - Current gaps / outstanding items

        Uses _extract_conclusion() to prioritize agent final answers over
        raw tool output prefixes.
        """
        if not self.turns_completed:
            return f"## Original Request\n{self.user_message}\n\n(No turns completed yet)"

        parts: list[str] = []
        remaining = max_chars

        # Section 1: Original user request — ALWAYS full, never truncated
        header = f"## Original User Request\n{self.user_message}\n"
        parts.append(header)
        remaining -= len(header)

        # Section 2: Accumulated accomplishments + gaps (running summary)
        accomplished: list[str] = []
        all_missing: list[str] = []
        progress_timeline: list[str] = []

        for turn in self.turns_completed:
            ev = turn.orchestrator_evaluation
            if ev:
                progress_timeline.append(f"T{turn.turn_number}:{ev.progress_pct}%")
                for ae in ev.agent_evaluations:
                    if ae.get("completed"):
                        notes = ae.get("notes", "")[:300]
                        accomplished.append(
                            f"T{turn.turn_number} [{ae.get('agent', '')}]: {notes}"
                        )
                all_missing = ev.missing_items or []
            else:
                for result in turn.agent_results:
                    if result.success and result.output and len(result.output) > 100:
                        conclusion = _extract_conclusion(result.output, 400)
                        accomplished.append(
                            f"T{turn.turn_number} [{result.agent_name}]: {conclusion}"
                        )

        # Progress timeline
        progress_section = f"\n## Progress: {' → '.join(progress_timeline)}\n"
        parts.append(progress_section)
        remaining -= len(progress_section)

        # Accomplished items (budget-aware: most recent first)
        if accomplished:
            acc_section = "\n## What Has Been Accomplished\n"
            for item in reversed(accomplished[-15:]):
                line = f"- {item}\n"
                if len(line) > remaining - 500:
                    break
                acc_section += line
                remaining -= len(line)
            parts.append(acc_section)

        # Outstanding gaps
        if all_missing:
            gap_section = "\n## Outstanding Gaps (from last evaluation)\n"
            for item in all_missing[:8]:
                line = f"- {item}\n"
                if len(line) > remaining:
                    break
                gap_section += line
                remaining -= len(line)
            parts.append(gap_section)

        # Section 3: Per-turn detail with agent conclusions (recent first)
        # FIX MODERATE #7: Skip turn details when budget is tight (<=8000),
        # because ledger_context already provides structured per-turn data.
        # This prevents the orchestrator from seeing the same info twice.
        if remaining > 800 and max_chars > 8000:
            detail_section = "\n## Turn Details\n"
            for turn in reversed(self.turns_completed[-5:]):
                turn_block = f"### Turn {turn.turn_number}\n"
                for result in turn.agent_results:
                    per_agent_cap = min(2000, remaining // max(1, len(turn.agent_results) + 1))
                    if result.output:
                        conclusion = _extract_conclusion(result.output, per_agent_cap)
                    else:
                        conclusion = "(no output)"
                    turn_block += f"- **{result.agent_name}**: {conclusion}\n"
                if turn.orchestrator_evaluation:
                    ev = turn.orchestrator_evaluation
                    turn_block += (
                        f"- Eval: {ev.progress_pct}% | "
                        f"Focus: {ev.next_turn_focus[:200] if ev.next_turn_focus else 'N/A'}\n"
                    )
                if len(turn_block) > remaining:
                    break
                detail_section += turn_block
                remaining -= len(turn_block)
            parts.append(detail_section)

        return "\n".join(parts)


def _extract_conclusion(output: str, max_chars: int) -> str:
    """Extract the most informative portion of an agent's output.

    Strategy: prioritize explicit conclusion sections, then fall back to
    3-part extraction — head (context), middle (structured data), tail (conclusions).

    Budget allocation:
    - If explicit summary section found: allocate 60% to it, 40% head
    - Otherwise: Head 20%, Middle structured 30%, Tail 50%
    """
    if not output:
        return ""
    output = output.strip()
    if len(output) <= max_chars:
        return output

    # Priority: look for explicit conclusion/summary section
    import re
    summary_match = re.search(
        r"(?:^|\n)##?\s*(?:Summary|Conclusion|Final|Result)[^\n]*\n([\s\S]+?)(?:\n##|\Z)",
        output,
        re.IGNORECASE,
    )
    if summary_match and len(summary_match.group(1).strip()) > 50:
        conclusion_text = summary_match.group(1).strip()
        conclusion_budget = int(max_chars * 0.6)
        head_budget = max_chars - conclusion_budget - 20
        head = output[:head_budget].rstrip()
        conclusion = conclusion_text[:conclusion_budget]
        return f"{head}\n[...]\n## Conclusion\n{conclusion}"

    head_budget = int(max_chars * 0.20)
    middle_budget = int(max_chars * 0.30)
    tail_budget = max_chars - head_budget - middle_budget - 50

    head = output[:head_budget].rstrip()
    tail = output[-tail_budget:].lstrip()

    # Extract structured data from the middle (between head and tail regions)
    middle_region = output[head_budget:-tail_budget] if tail_budget > 0 else ""
    middle_extracted = _extract_structured_from_middle(middle_region, middle_budget)

    if middle_extracted:
        return f"{head}\n[...extracted key data from middle...]\n{middle_extracted}\n[...]\n{tail}"
    return f"{head}\n[...middle omitted ({len(middle_region)} chars)...]\n{tail}"


def _extract_structured_from_middle(text: str, budget: int) -> str:
    """Extract structured/important lines from the middle of agent output.

    Captures: bullet points, URLs, key-value data, numbered findings.
    Skips: plain prose, tool call boilerplate, repetitive content.
    """
    if not text or budget < 100:
        return ""

    important_lines: list[str] = []
    seen: set[str] = set()

    for line in text.split("\n"):
        stripped = line.strip()
        if len(stripped) < 10:
            continue
        # Skip duplicates (by first 50 chars)
        key = stripped[:50]
        if key in seen:
            continue

        is_important = (
            stripped.startswith(("-", "•", "*", "✓", "✅", "→", "►"))
            or stripped.startswith(tuple(f"{i}." for i in range(1, 20)))
            or "http" in stripped
            or "://" in stripped
            or ": " in stripped[:40]
            or stripped.startswith("**")
        )
        if is_important:
            seen.add(key)
            important_lines.append(stripped[:200])

    if not important_lines:
        return ""

    result = "\n".join(important_lines)
    if len(result) > budget:
        result = result[:budget]
        last_newline = result.rfind("\n")
        if last_newline > budget * 0.5:
            result = result[:last_newline]
    return result
