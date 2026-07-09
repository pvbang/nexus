"""
MekongAI Nexus - Self-Reflection Engine.

Checkpoint mechanism that forces agents to PAUSE and evaluate their own work
during execution, not just after.

Key behaviors:
- After every N tool calls: "Am I on the right track?"
- After each debate round: "Is my approach optimal?"
- When results seem thin: "What am I missing?"
- Before final answer: "Does this meet the success criteria?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ReflectionContext:
    """Input context for a reflection checkpoint."""

    task: str
    strategy: str = ""
    tools_used: list[str] = field(default_factory=list)
    tool_results_summary: str = ""
    current_output: str = ""
    round_num: int = 0
    total_rounds: int = 0
    success_criteria: list[str] = field(default_factory=list)
    agent_name: str = "Nexus Orchestrator"
    agent_role: str = "orchestrator"
    tool_call_history: str = ""  # Summary of ALL tools called (not just recent)
    findings_content: str = ""  # Content from FINDINGS.md (accumulated results)


@dataclass
class ReflectionResult:
    """Result of a self-reflection checkpoint."""

    on_track: bool = True
    quality_score: float = 0.5  # 0.0 to 1.0
    answering_right_question: bool = True
    approach_optimal: bool = True
    missing_aspects: list[str] = field(default_factory=list)
    strategy_change: str = ""
    should_stop: bool = False
    stop_reason: str = ""
    needs_specialist: bool = False
    specialist_type: str = ""
    self_critique: str = ""
    improvement_suggestions: list[str] = field(default_factory=list)

    @property
    def needs_intervention(self) -> bool:
        """True if reflection detected issues requiring action.

        BUG-3 FIX: Tightened threshold from 0.6 to 0.75.
        Previously agents with quality 0.65-0.74 would pass silently,
        allowing shallow/repetitive work to continue unchecked.
        Now intervention triggers earlier to prevent stagnation loops.
        """
        return (
            not self.on_track
            or not self.answering_right_question
            or not self.approach_optimal
            or self.should_stop
            or self.needs_specialist
            or len(self.missing_aspects) > 2
            or self.quality_score < 0.75
        )

    def to_prompt_injection(self) -> str:
        """Format reflection as prompt injection for agent.

        BUG-3 FIX: Always generate injection text regardless of needs_intervention.
        Previously, passing reflections were silently skipped — now agents always
        see their quality score and any minor suggestions, maintaining awareness.
        """

        parts = ["\n## 🪞 SELF-REFLECTION CHECKPOINT\n"]

        if not self.answering_right_question:
            parts.append(
                "⚠️ **WARNING**: You may not be answering the RIGHT question. "
                "Re-read the original task carefully.\n"
            )

        if not self.approach_optimal:
            parts.append(
                "⚠️ **SUBOPTIMAL APPROACH**: Your current strategy is not the best. "
                "Consider alternatives.\n"
            )

        if self.self_critique:
            parts.append(f"### Self-Critique\n{self.self_critique}\n")

        if self.missing_aspects:
            parts.append("### Missing Aspects (must address)")
            for m in self.missing_aspects:
                parts.append(f"- ❌ {m}")
            parts.append("")

        if self.strategy_change:
            parts.append(f"### 🔄 Strategy Change\n{self.strategy_change}\n")

        if self.improvement_suggestions:
            parts.append("### Improvement Suggestions")
            for s in self.improvement_suggestions:
                parts.append(f"- 💡 {s}")
            parts.append("")

        if self.needs_specialist:
            parts.append(
                f"### 🧑‍🔬 Specialist Needed\n"
                f"Consider spawning a **{self.specialist_type}** agent for deeper analysis.\n"
            )

        if self.should_stop:
            parts.append(f"### 🛑 Should Stop\n{self.stop_reason}\n")

        parts.append(
            f"**Quality Score**: {self.quality_score:.0%}\n"
            "Address these issues before continuing.\n"
        )

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Domain-Specific Quality Thresholds

QUALITY_THRESHOLDS: dict[str, float] = {
    "security": 0.80,
    "audit": 0.80,
    "research": 0.70,
    "analysis": 0.65,
    "code_review": 0.70,
    "general": 0.55,
}

_TASK_DEPTH_CRITERIA = """\

DOMAIN-AWARE DEPTH EVALUATION (apply whichever domain fits the task):

For tasks that involve TESTING/VERIFYING against a live system (security, integration, deployment):
- Agent must show ACTUAL interaction with the target (real requests sent, real responses received)
- Theoretical analysis without live testing → quality_score ≤ 0.40
- Agent only doing research/reading without executing against target → quality_score ≤ 0.30
- Claims must be backed by EVIDENCE (response bodies, command output, logs)
- Multiple approaches must be attempted if the first one fails

For tasks that involve BUILDING/CREATING (code, infrastructure, design):
- Agent must VERIFY output works (run it, test it, check it renders)
- Writing code without executing it → quality_score ≤ 0.50
- Claims of "it works" without showing output → quality_score ≤ 0.40

For tasks that involve RESEARCH/ANALYSIS:
- Agent must produce WRITTEN deliverables (not just tool calls with no synthesis)
- Multiple sources must be cross-referenced for key claims
- 10+ tool calls with no written output file → quality_score ≤ 0.40

UNIVERSAL RULE: If the agent has spent 8+ tool calls ONLY reading/searching
without producing ANY output (no writes, no executions, no deliverables),
the agent is in analysis paralysis → quality_score ≤ 0.35, approach_optimal = false
"""


# ---------------------------------------------------------------------------
# Self-Reflection System Prompts
# ---------------------------------------------------------------------------

_REFLECTION_SYSTEM_PROMPT = """\
You are a Self-Reflection Analyst. Your job is to critically evaluate an \
AI agent's work-in-progress and determine if it's on the right track.

Think like a demanding CTO reviewing an engineer's work:
- Is the agent actually answering what was ASKED?
- Is the approach efficient or wasteful?
- What critical perspectives are MISSING?
- Is the quality good enough or superficial?
- Should the agent stop, change approach, or continue?
- Has the agent WRITTEN findings to output files (FINDINGS.md, task docs)?
- Has the agent been ONLY searching/reading without producing written output?

META-COGNITIVE EVALUATION (critical — always apply):
- Is the agent choosing the OPTIMAL approach or just the easiest one?
- Is the agent thinking at PRODUCTION QUALITY or demo/prototype level?
- Is the agent self-improving: getting better with each step, or plateauing?
- Is the agent asking itself "WHY this approach?" or just blindly executing?
- Would a world-class expert approve of this methodology and output?
- Is the agent being intellectually LAZY (choosing simple over excellent)?
- Has the agent considered alternative approaches and chosen the BEST one?
- Is the agent operating at maximum capability or coasting at minimum effort?

Return valid JSON (no markdown code blocks):
{
    "on_track": true/false,
    "quality_score": 0.0-1.0,
    "answering_right_question": true/false,
    "approach_optimal": true/false,
    "missing_aspects": ["aspect 1 not covered", "aspect 2 overlooked"],
    "strategy_change": "description of how to change strategy (empty if on track)",
    "should_stop": true/false,
    "stop_reason": "reason to stop (empty if should continue)",
    "needs_specialist": true/false,
    "specialist_type": "researcher|developer|analyst|writer|critic (if needed)",
    "self_critique": "Honest critique of the work so far — what is weak?",
    "improvement_suggestions": ["specific suggestion 1", "specific suggestion 2"]
}

RULES:
- Be BRUTALLY HONEST — don't say "on track" if work is superficial
- quality_score < 0.3 means "completely inadequate"
- quality_score 0.3-0.6 means "needs significant improvement"
- quality_score 0.6-0.8 means "adequate but can be better"
- quality_score > 0.8 means "good quality"
- If agent is just repeating searches without synthesis → approach_optimal = false
- If agent hasn't addressed the CORE of the question → answering_right_question = false
- missing_aspects should list SPECIFIC things, not vague "more research needed"
- If agent has NOT written any findings/output to files after 5+ tool calls → quality_score <= 0.4
- An agent that only READS but never WRITES is NOT on track
- IMPORTANT: If TOOL CALL HISTORY shows a tool was already called (e.g., "http_request x5"), \
do NOT list that as "missing". The agent already tested it.
- If ACCUMULATED FINDINGS shows results for a topic, do NOT say it's "not covered"
- Score should reflect ACTUAL work done (visible in tool history + findings), not just recent context
- Respond in the SAME LANGUAGE as the task

CRITICAL — FAILURE LOOP DETECTION:
If you see ANY of these patterns in TOOL CALL HISTORY, set should_stop=true immediately:
- Same HTTP endpoint returning 503/502/timeout repeatedly (3+ times) → agent is polling a dead service
- Multiple sleep/wait commands followed by the same failing request → agent is hoping service will recover
- Same tool called 5+ times with substantially similar arguments and same error output
- Agent retrying after errors without changing approach (same URL, same method, same target)

**EXCEPTION — ITERATIVE EXPLORATION (not a failure loop):**
Some tasks REQUIRE multiple attempts with DIFFERENT inputs against the SAME target.
This is NOT a failure loop — it is iterative problem-solving. DO NOT trigger should_stop when:
- Agent is trying DIFFERENT variations against the same endpoint (different payloads, parameters, encodings, methods)
- Agent is testing different services/ports on the same system
- Agent is trying different authentication approaches
- The responses VARY between attempts (different error messages, different status codes, different behavior)

Only trigger should_stop when:
- EXACT same request (same tool + same args + same input) repeated 5+ times with IDENTICAL response
- Agent has NOT changed anything between attempts (no variation in approach whatsoever)
- Agent is stuck reading files/analyzing instead of EXECUTING for 10+ tool calls without any productive output

When you detect a failure loop:
- should_stop = true
- stop_reason = "Agent is stuck in a retry loop against [unreachable target/failing endpoint]. \
After N attempts with same error, further retries will not succeed. Agent should conclude with \
partial results and report the blocker."
- quality_score = 0.2 (retrying a dead endpoint produces ZERO value)
- approach_optimal = false
"""

_QUICK_REFLECTION_PROMPT = """\
Quick self-check on this agent's work. Return JSON:
{
    "on_track": true/false,
    "quality_score": 0.0-1.0,
    "missing_aspects": ["what's missing"],
    "strategy_change": "adjustment needed or empty",
    "self_critique": "brief honest critique"
}
"""


# ---------------------------------------------------------------------------
# Self-Reflection Engine
# ---------------------------------------------------------------------------


class SelfReflectionEngine:
    """Self-reflection checkpoint system for agents.

    Periodically evaluates agent work and injects corrective guidance.

    Usage:
        reflection = SelfReflectionEngine(llm)

        # Check if it's time to reflect
        if reflection.should_reflect(round_num=3, tool_calls=6):
            ctx = ReflectionContext(task=task, tools_used=[...], ...)
            result = await reflection.reflect(ctx)
            if result.needs_intervention:
                inject_reflection(messages, result)
    """

    def __init__(
        self,
        llm: Any,
        reflection_interval: int = 3,
        min_tool_calls_before_reflect: int = 2,
    ) -> None:
        self._llm = llm
        self._interval = reflection_interval
        self._min_tools = min_tool_calls_before_reflect
        self._reflection_count = 0
        self._last_quality_score = 0.5

    @staticmethod
    def _get_configured_interval() -> int:
        """Get reflection interval from config."""
        try:
            from app.config import get_settings
            return get_settings().self_reflection_interval
        except Exception:
            return 3

    def should_reflect(
        self,
        round_num: int,
        tool_calls: int = 0,
        force: bool = False,
    ) -> bool:
        """Determine if it's time for a reflection checkpoint.

        Args:
            round_num: Current round number (1-based).
            tool_calls: Total tool calls so far.
            force: Force reflection regardless of interval.

        Returns:
            True if agent should pause and reflect.
        """
        if force:
            return True

        interval = self._get_configured_interval()

        # Reflect every N rounds
        if round_num > 0 and round_num % interval == 0:
            return True

        # Reflect after significant tool usage
        if tool_calls >= self._min_tools and tool_calls % (interval * 2) == 0:
            return True

        # Reflect if last quality was poor
        if self._last_quality_score < 0.4 and round_num > 1:
            return True

        return False

    async def reflect(
        self,
        context: ReflectionContext,
        quick: bool = False,
        event_callback: Optional[Callable] = None,
    ) -> ReflectionResult:
        """Perform a self-reflection checkpoint.

        Args:
            context: Current execution context.
            quick: If True, use shorter prompt for faster reflection.
            event_callback: Optional async callback for real-time event emission.

        Returns:
            ReflectionResult with assessment and guidance.
        """
        self._reflection_count += 1

        criteria_text = (
            "\n".join(f"- {c}" for c in context.success_criteria)
            if context.success_criteria
            else "Not defined"
        )

        history_section = ""
        if context.tool_call_history:
            history_section = (
                f"TOOL CALL HISTORY (all tools called so far):\n"
                f"{context.tool_call_history}\n\n"
            )

        findings_section = ""
        if context.findings_content:
            findings_section = (
                f"ACCUMULATED FINDINGS (written to FINDINGS.md):\n"
                f"{context.findings_content[:3000]}\n\n"
            )

        user_content = (
            f"ORIGINAL TASK:\n{context.task[:500]}\n\n"
            f"STRATEGY:\n{context.strategy[:300]}\n\n"
            f"ROUND: {context.round_num}/{context.total_rounds}\n"
            f"TOOLS USED (recent): {', '.join(context.tools_used) if context.tools_used else 'None'}\n\n"
            f"{history_section}"
            f"TOOL RESULTS SUMMARY:\n{context.tool_results_summary[:1500]}\n\n"
            f"{findings_section}"
            f"CURRENT OUTPUT/PROGRESS:\n{context.current_output[:1500]}\n\n"
            f"SUCCESS CRITERIA:\n{criteria_text}"
        )

        system_prompt = _QUICK_REFLECTION_PROMPT if quick else _REFLECTION_SYSTEM_PROMPT

        # Always include domain-aware depth criteria — the LLM decides which
        # domain rules apply based on the task context (no keyword matching).
        if not quick:
            system_prompt = system_prompt + "\n\n" + _TASK_DEPTH_CRITERIA

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.2)
        except Exception as e:
            logger.warning(
                "self_reflection_failed",
                error=str(e),
                agent=context.agent_name,
                round=context.round_num,
            )
            return ReflectionResult(on_track=True, quality_score=0.5)

        result = ReflectionResult(
            on_track=raw.get("on_track", True),
            quality_score=float(raw.get("quality_score", 0.5)),
            answering_right_question=raw.get("answering_right_question", True),
            approach_optimal=raw.get("approach_optimal", True),
            missing_aspects=raw.get("missing_aspects", []),
            strategy_change=raw.get("strategy_change", ""),
            should_stop=raw.get("should_stop", False),
            stop_reason=raw.get("stop_reason", ""),
            needs_specialist=raw.get("needs_specialist", False),
            specialist_type=raw.get("specialist_type", ""),
            self_critique=raw.get("self_critique", ""),
            improvement_suggestions=raw.get("improvement_suggestions", []),
        )

        self._last_quality_score = result.quality_score

        # --- BUG-3 FIX: Stagnation detection ---
        # Track quality history to detect when agent is stuck (quality not improving)
        if not hasattr(self, '_quality_history'):
            self._quality_history: list[float] = []
        self._quality_history.append(result.quality_score)

        # Stagnation: quality not improving after 3 reflections (<5% variance = stagnant)
        if len(self._quality_history) >= 3:
            _recent = self._quality_history[-3:]
            if max(_recent) - min(_recent) < 0.05:
                # Force intervention — agent is stuck in a quality plateau
                result.on_track = False
                if not result.strategy_change:
                    result.strategy_change = (
                        "Quality has stagnated across 3 reflections "
                        f"({_recent[0]:.0%} → {_recent[-1]:.0%}). "
                        "You MUST change your approach fundamentally."
                    )
                logger.warning(
                    "stagnation_detected",
                    quality_history=_recent,
                    agent=context.agent_name,
                    round=context.round_num,
                )

        logger.info(
            "self_reflection_complete",
            agent=context.agent_name,
            round=context.round_num,
            on_track=result.on_track,
            quality=result.quality_score,
            needs_intervention=result.needs_intervention,
            stagnant=len(self._quality_history) >= 3 and (
                max(self._quality_history[-3:]) - min(self._quality_history[-3:]) < 0.05
            ),
            missing=len(result.missing_aspects),
        )

        # ── Emit self_reflection event (fire-and-forget) ──
        if event_callback is not None:
            try:
                await event_callback({
                    "event_type": "self_reflection",
                    "type": "self_reflection",
                    "round": context.round_num,
                    "quality_score": result.quality_score,
                    "on_track": result.on_track,
                    "missing_aspects": result.missing_aspects,
                    "strategy_change": result.strategy_change,
                    "improvement_suggestions": result.improvement_suggestions,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            except Exception:
                pass  # Fire-and-forget — never block execution

        return result

    async def reflect_on_final_output(
        self,
        task: str,
        final_output: str,
        success_criteria: list[str],
    ) -> ReflectionResult:
        """Final quality check before returning to user.

        Args:
            task: Original task description.
            final_output: The agent's final response.
            success_criteria: Criteria from initial analysis.

        Returns:
            ReflectionResult. If quality_score < threshold, agent should retry.
        """
        criteria_text = "\n".join(f"- {c}" for c in success_criteria) if success_criteria else "None"

        user_content = (
            f"ORIGINAL TASK:\n{task[:500]}\n\n"
            f"FINAL OUTPUT:\n{final_output[:3000]}\n\n"
            f"SUCCESS CRITERIA:\n{criteria_text}\n\n"
            "Evaluate: Does this output FULLY satisfy the task? "
            "Be critical — would a demanding user be satisfied?"
        )

        messages = [
            {"role": "system", "content": _REFLECTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.2)
        except Exception as e:
            logger.warning("final_reflection_failed", error=str(e))
            return ReflectionResult(on_track=True, quality_score=0.7)

        result = ReflectionResult(
            on_track=raw.get("on_track", True),
            quality_score=float(raw.get("quality_score", 0.7)),
            missing_aspects=raw.get("missing_aspects", []),
            self_critique=raw.get("self_critique", ""),
            improvement_suggestions=raw.get("improvement_suggestions", []),
        )

        logger.info(
            "final_reflection_complete",
            quality=result.quality_score,
            on_track=result.on_track,
            missing=len(result.missing_aspects),
        )

        return result

    def get_stats(self) -> dict[str, Any]:
        """Get reflection statistics."""
        return {
            "reflection_count": self._reflection_count,
            "last_quality_score": self._last_quality_score,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def inject_reflection(
    messages: list[dict[str, str]],
    result: ReflectionResult,
) -> bool:
    """Inject a reflection result into the messages list.

    BUG-3 FIX: Always inject reflection results, not just when needs_intervention.
    This ensures agents always see their quality score and maintain awareness
    of their progress, preventing silent stagnation loops.

    Args:
        messages: The conversation messages list (mutated in-place).
        result: The :class:`ReflectionResult` from a reflection checkpoint.

    Returns:
        True if a reflection message was injected, False otherwise.
    """
    injection_text = result.to_prompt_injection()
    if not injection_text:
        logger.debug("inject_reflection_skipped", reason="empty_injection_text")
        return False

    messages.append({"role": "system", "content": injection_text})
    logger.info(
        "reflection_injected",
        quality_score=result.quality_score,
        on_track=result.on_track,
        needs_intervention=result.needs_intervention,
        missing_aspects=len(result.missing_aspects),
    )
    return True


def is_self_reflection_enabled() -> bool:
    """Check if self-reflection is enabled via config."""
    try:
        from app.config import get_settings
        return get_settings().self_reflection_enabled
    except Exception:
        return True  # Default: enabled


def get_self_reflection_engine(llm: Any) -> SelfReflectionEngine:
    """Factory for SelfReflectionEngine."""
    return SelfReflectionEngine(llm)
