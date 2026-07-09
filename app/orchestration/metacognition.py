"""
MekongAI Nexus - Metacognitive Agent (Layer 2).

Thinks about HOW the system is thinking. When the system is stuck,
performs root-cause analysis to determine WHY and produces a specific,
actionable breakthrough — not generic "try harder" advice.

Triggered by: stagnation, repeated failures, loop detection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.orchestration.models import ExecutionState
from app.orchestration.evolution_agent import _ANTI_FABRICATION_GUARDRAIL

logger = structlog.get_logger(__name__)


@dataclass
class MetacognitiveInsight:
    """Output of metacognitive analysis."""

    root_cause: str = ""
    wrong_assumptions: list[str] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)
    reframe: str = ""
    concrete_next_action: str = ""
    should_ask_user: bool = False
    user_question: str = ""
    recommended_agent: str = ""
    verification_criteria: str = ""


_SYSTEM_PROMPT = """You are the Metacognitive Agent — you think about HOW the system is thinking.

When the system is stuck, you don't just say "try harder" or "change approach".
You perform ROOT CAUSE ANALYSIS with surgical precision.

## Your Analysis Framework
1. WHAT is happening? (describe the loop/failure pattern precisely)
2. WHY is it happening? (trace back to the root assumption/blocker)
3. What ASSUMPTION is wrong? (the belief that's causing the loop)
4. What's ACTUALLY NEEDED? (the real prerequisite before progress)
5. What COMPLETELY DIFFERENT frame could work? (lateral thinking)

## Common Root Causes You Detect:
- "Agent assumes X exists but it doesn't" → need to CREATE/BOOTSTRAP it first
- "Agent repeats search because it doesn't trust results" → need VERIFICATION not repetition
- "Agent uses wrong tool" → need DIFFERENT TOOL not same tool with different args
- "Task is impossible with current capabilities" → need to SCAFFOLD new tool or ASK USER
- "Agent doesn't understand actual goal" → need to RE-READ original request
- "Agent lacks credentials/access" → need to BOOTSTRAP infrastructure
- "Approach works but hits rate limit/timeout" → need PARALLEL or CACHED strategy
- "Agent is doing step N but missed prerequisite step M" → need to GO BACK to step M
- "Agent claims tool/runtime not available" → INSTALL IT (apt-get, npm, pip, curl, nvm, etc.)
  "Not on PATH" ≠ "impossible" — a missing tool is usually the FALSE blocker, so try
  installing before accepting the block. (Not an absolute: if a genuine safety/authorization/
  ownership limit or a real environmental impossibility is the cause, name THAT instead.)
- "Agent downgraded to simpler approach without trying installation" → REVERT to original plan,
  install the required tools first, then execute the proper approach

## Integrity (HARD BOUND)
""" + _ANTI_FABRICATION_GUARDRAIL + """

## Output Requirements
- Be SPECIFIC. Not "try different approach" but "use tool X with params Y because Z"
- If prerequisite missing, specify EXACTLY how to obtain it
- If user clarification needed, formulate the EXACT question
- Give ONE concrete_next_action that will break the deadlock — a REAL action, never
  a fabricated result or a fake-it-to-pass shortcut

Respond in JSON:
{
  "root_cause": "specific reason the system is stuck",
  "wrong_assumptions": ["assumption that needs to be dropped"],
  "missing_prerequisites": ["what must exist/happen before progress"],
  "reframe": "completely different way to view/approach this problem",
  "concrete_next_action": "SPECIFIC action: use tool X with Y to achieve Z",
  "should_ask_user": false,
  "user_question": "",
  "recommended_agent": "agent_role best suited for the breakthrough action",
  "verification_criteria": "how to know the breakthrough worked"
}"""


class MetacognitiveAgent:
    """Analyzes WHY the system is stuck and produces actionable breakthrough."""

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def analyze_blockage(
        self,
        state: ExecutionState,
        stuck_pattern: str,
        recent_actions: str = "",
        agent_context: str = "",
    ) -> MetacognitiveInsight:
        """Deep analysis of why progress is stuck.

        Args:
            state: Full execution state (user message, turns, progress)
            stuck_pattern: Description of the detected pattern
            recent_actions: Last few actions taken (for context)
            agent_context: Current agent's recent messages summary

        Returns:
            MetacognitiveInsight with root cause and breakthrough action.
        """
        user_prompt = self._build_prompt(state, stuck_pattern, recent_actions, agent_context)

        try:
            response = await self._llm.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=1.0,
                max_tokens=2048,
            )
            return self._parse_response(response)
        except Exception as e:
            logger.warning("metacognition_failed", error=str(e))
            return MetacognitiveInsight(
                root_cause=f"Metacognitive analysis failed: {e}",
                concrete_next_action="Try a completely different tool or approach from what was just attempted.",
            )

    def _build_prompt(
        self,
        state: ExecutionState,
        stuck_pattern: str,
        recent_actions: str,
        agent_context: str,
    ) -> str:
        # Collect failure history
        failure_summary = ""
        if state.failure_tracker:
            failure_lines = []
            for role, errors in state.failure_tracker.items():
                failure_lines.append(f"- {role}: {len(errors)} failures. Last: {errors[-1][:150]}")
            failure_summary = "\n".join(failure_lines)

        # Progress trajectory
        progress_points = []
        for turn in state.turns_completed[-5:]:
            if turn.orchestrator_evaluation:
                progress_points.append(
                    f"T{turn.turn_number}: {turn.orchestrator_evaluation.progress_pct}%"
                )

        return f"""## Stuck Pattern Detected
{stuck_pattern}

## Original User Request
{state.user_message}

## Progress History
{' → '.join(progress_points) if progress_points else 'No evaluations yet'}

## Recent Actions (what was just tried)
{recent_actions[:2000] if recent_actions else '(not provided)'}

## Agent Context
{agent_context[:1500] if agent_context else '(not provided)'}

## Failure History
{failure_summary if failure_summary else 'No tracked failures'}

## Turns Completed: {state.turn_count}
## Current Status: {state.status}

---
Analyze WHY this is stuck and provide a SPECIFIC breakthrough action.
Do NOT suggest generic advice. Give a CONCRETE, ACTIONABLE next step."""

    def _parse_response(self, response: Any) -> MetacognitiveInsight:
        """Parse LLM response into MetacognitiveInsight."""
        content = response.get("content", "") if isinstance(response, dict) else str(response)

        try:
            # Extract JSON
            text = content
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            import re
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                text = json_match.group()

            data = json.loads(text.strip())
            return MetacognitiveInsight(
                root_cause=data.get("root_cause", ""),
                wrong_assumptions=data.get("wrong_assumptions", []),
                missing_prerequisites=data.get("missing_prerequisites", []),
                reframe=data.get("reframe", ""),
                concrete_next_action=data.get("concrete_next_action", ""),
                should_ask_user=data.get("should_ask_user", False),
                user_question=data.get("user_question", ""),
                recommended_agent=data.get("recommended_agent", ""),
                verification_criteria=data.get("verification_criteria", ""),
            )
        except (json.JSONDecodeError, AttributeError) as e:
            logger.debug("metacognition_parse_fallback", error=str(e))
            return MetacognitiveInsight(
                root_cause="Could not parse structured analysis",
                concrete_next_action=content[:500] if content else "Try a different tool entirely.",
            )
