"""
Nexus - Autonomous Goal-Directed Engine.

Transforms agent from "bảo gì làm nấy" → "tự biết cần làm gì, tự kiểm tra,
tự sửa lỗi, tự phát hiện vấn đề mới, đào sâu đến tận cốt lõi".

Core behaviors:
1. GoalDecomposer: Tự tách task thành sub-goals ON-THE-FLY (không chỉ trước khi bắt đầu)
2. ResultVerifier: Sau MỖI action → verify result → nếu sai → analyze → fix → retry
3. DiscoveryTracker: Gặp vấn đề/thông tin mới → dừng lại → evaluate impact
4. CausalChainBuilder: Tìm mối liên hệ giữa các vấn đề → suy luận ra vấn đề mới
5. CompletionChecker: Kiểm tra thực sự hoàn thành chưa — không dừng sớm

Design: Cung cấp prompt injections và runtime logic, KHÔNG replace
execution loop mà ENHANCE nó.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class SubGoalStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class SubGoal:
    """A decomposed sub-goal of the main task."""

    id: int
    description: str
    status: SubGoalStatus = SubGoalStatus.PENDING
    result: str = ""
    depends_on: list[int] = field(default_factory=list)
    verification: str = ""  # How to verify this sub-goal is done
    attempts: int = 0
    max_attempts: int = 3


@dataclass
class Discovery:
    """An unexpected finding during execution."""

    content: str
    source: str = ""  # Which tool/action produced this
    impact: str = ""  # How it affects the task
    should_investigate: bool = False
    investigated: bool = False
    round_found: int = 0


@dataclass
class CausalLink:
    """A causal relationship between findings."""

    cause: str
    effect: str
    evidence: str = ""
    confidence: float = 0.5


@dataclass
class VerificationResult:
    """Result of verifying a tool output or action result."""

    valid: bool = True
    issues: list[str] = field(default_factory=list)
    fix_suggestion: str = ""
    should_retry: bool = False
    root_cause: str = ""


@dataclass
class GoalState:
    """Current state of the autonomous goal engine."""

    original_task: str = ""
    sub_goals: list[SubGoal] = field(default_factory=list)
    discoveries: list[Discovery] = field(default_factory=list)
    causal_chains: list[CausalLink] = field(default_factory=list)
    completed_goals: int = 0
    total_verifications: int = 0
    total_fixes: int = 0
    is_complete: bool = False


# ---------------------------------------------------------------------------
# System Prompts for LLM calls
# ---------------------------------------------------------------------------

_DECOMPOSE_PROMPT = """\
You are a Goal Decomposer. Given a task and current progress, break it down \
into concrete, actionable sub-goals.

Each sub-goal should be:
- Specific and measurable (not vague)
- Independently verifiable (how do we know it's done?)
- Ordered by dependencies

Return valid JSON (no markdown):
{
    "sub_goals": [
        {
            "id": 1,
            "description": "Specific action to take",
            "verification": "How to verify this is done correctly",
            "depends_on": []
        }
    ],
    "reasoning": "Why these sub-goals in this order"
}

RULES:
- 2-7 sub-goals (not too many, not too few)
- Each sub-goal should map to 1-3 tool calls
- Include verification criteria for EACH sub-goal
- If some sub-goals are already done (from progress), mark their IDs in reasoning
- Respond in the SAME LANGUAGE as the task
"""

_VERIFY_PROMPT = """\
You are a Result Verifier. Examine the output of a tool/action and determine \
if it actually succeeded and produced correct/useful results.

Think critically:
1. Did the action actually accomplish what was intended?
2. Is the output data correct and complete?
3. Are there errors hidden in the output (even if status shows "success")?
4. Is the output relevant to the original task?
5. What is the root cause if something went wrong?

Return valid JSON (no markdown):
{
    "valid": true/false,
    "issues": ["specific issue found"],
    "fix_suggestion": "how to fix (empty if valid)",
    "should_retry": true/false,
    "root_cause": "root cause of failure (empty if valid)"
}

Be CRITICAL. Empty search results, generic errors, or irrelevant data are NOT valid.
"""

_DISCOVERY_EVAL_PROMPT = """\
You are a Discovery Evaluator. During task execution, an unexpected finding \
was encountered. Evaluate its relevance and impact on the main task.

Return valid JSON (no markdown):
{
    "relevant": true/false,
    "impact": "how this affects the main task",
    "should_investigate": true/false,
    "investigation_plan": "what to do about it (empty if irrelevant)",
    "new_sub_goal": "new sub-goal to add if investigation needed (empty if not)"
}
"""

_CAUSAL_PROMPT = """\
You are a Causal Reasoner. Given a set of findings from an investigation, \
identify causal relationships between them.

Look for patterns:
1. A causes B (direct causation)
2. A correlates with B (evidence of connection)
3. A and B are both caused by C (common cause)
4. A prevents/blocks B (inhibition)

Return valid JSON (no markdown):
{
    "causal_links": [
        {
            "cause": "what causes",
            "effect": "what is caused",
            "evidence": "evidence supporting this link",
            "confidence": 0.0-1.0
        }
    ],
    "key_insight": "the most important insight from connecting these findings",
    "new_questions": ["question raised by these connections"]
}
"""

_COMPLETION_PROMPT = """\
You are a Completion Checker. Determine if the agent has TRULY completed \
the original task — not just done something related to it.

CRITICAL RULES:
- Set "complete": false if quality_score < 0.85 (even if some work was done)
- Set "complete": false if ANY important aspect is missing
- Be STRICT: partial completion is NOT completion

Check:
1. Does the result actually answer/solve what was asked?
2. Are there sub-goals still incomplete?
3. Is the quality sufficient (not superficial)?
4. Were all important aspects covered?
5. Would the user be satisfied with this result?

Quality Score Guidelines:
- 0.0-0.3: Barely started, mostly missing
- 0.4-0.6: Partial work done, major gaps remain
- 0.7-0.84: Good progress, but incomplete (set complete=false)
- 0.85-0.95: Nearly complete, minor gaps
- 0.96-1.0: Fully complete, excellent quality

Return valid JSON (no markdown):
{
    "complete": true/false,
    "quality_score": 0.0-1.0,
    "missing": ["what's still missing"],
    "next_steps": ["what to do next if not complete"],
    "user_would_be_satisfied": true/false
}
"""


# ---------------------------------------------------------------------------
# Autonomous Goal Engine
# ---------------------------------------------------------------------------


class GoalEngine:
    """Autonomous goal-directed execution engine.

    Wraps around the existing tool-calling loop to add:
    - Dynamic sub-goal decomposition
    - Auto-verification of every result
    - Auto-fix on errors (analyze root cause → fix → retry)
    - Discovery tracking for unexpected findings
    - Causal chain reasoning between findings
    - Completion checking against original goal

    Usage:
        engine = GoalEngine(llm)

        # At start: decompose task
        state = await engine.decompose_goals(task, context)

        # After each tool result: verify
        verification = await engine.verify_result(tool_name, result, task)
        if not verification.valid:
            # fix_prompt = verification.fix_suggestion → retry

        # When something unexpected found: evaluate
        await engine.evaluate_discovery(discovery, task, state)

        # Periodically: check completion
        check = await engine.check_completion(task, progress, state)
        if not check.complete:
            # keep going

        # After significant findings: build causal chain
        chains = await engine.build_causal_chain(findings, task)
    """

    def __init__(self, llm: Any) -> None:
        self._llm = llm
        self.state = GoalState()

    async def decompose_goals(
        self,
        task: str,
        context: str = "",
        progress: str = "",
    ) -> GoalState:
        """Decompose task into sub-goals, considering current progress.

        Can be called multiple times — each call may add new sub-goals
        based on what was learned so far.
        """
        self.state.original_task = task

        user_content = f"TASK:\n{task[:1000]}"
        if context:
            user_content += f"\n\nCONTEXT:\n{context[:500]}"
        if progress:
            user_content += f"\n\nPROGRESS SO FAR:\n{progress[:1000]}"
        if self.state.sub_goals:
            existing = "\n".join(
                f"- [{sg.status.value}] {sg.description}"
                for sg in self.state.sub_goals
            )
            user_content += f"\n\nEXISTING SUB-GOALS:\n{existing}"

        messages = [
            {"role": "system", "content": _DECOMPOSE_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.3)
        except Exception as e:
            logger.warning("goal_decompose_failed", error=str(e))
            # Fallback: single sub-goal = the task itself
            self.state.sub_goals = [SubGoal(id=1, description=task[:200], verification="Task completed")]
            return self.state

        new_goals = raw.get("sub_goals", [])
        next_id = max((sg.id for sg in self.state.sub_goals), default=0) + 1

        for sg_data in new_goals:
            if isinstance(sg_data, dict):
                sg = SubGoal(
                    id=sg_data.get("id", next_id),
                    description=sg_data.get("description", ""),
                    verification=sg_data.get("verification", ""),
                    depends_on=sg_data.get("depends_on", []),
                )
                # Avoid duplicates
                existing_descs = {g.description.lower()[:50] for g in self.state.sub_goals}
                if sg.description.lower()[:50] not in existing_descs:
                    self.state.sub_goals.append(sg)
                    next_id = max(next_id, sg.id) + 1

        logger.info(
            "goals_decomposed",
            total=len(self.state.sub_goals),
            new=len(new_goals),
        )
        return self.state

    async def verify_result(
        self,
        tool_name: str,
        tool_output: str,
        intended_action: str,
        task_context: str = "",
    ) -> VerificationResult:
        """Verify a tool result — did it actually work correctly?

        Called AFTER every significant tool call.
        """
        self.state.total_verifications += 1

        # Quick heuristic checks first (no LLM call needed)
        quick = self._quick_verify(tool_name, tool_output)
        if quick is not None:
            return quick

        # LLM-based deep verification for ambiguous cases
        user_content = (
            f"TOOL: {tool_name}\n"
            f"INTENDED ACTION: {intended_action[:300]}\n"
            f"OUTPUT:\n{tool_output[:2000]}\n"
        )
        if task_context:
            user_content += f"\nTASK CONTEXT: {task_context[:300]}"

        messages = [
            {"role": "system", "content": _VERIFY_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.2)
        except Exception as e:
            logger.debug("result_verification_failed", error=str(e))
            return VerificationResult(valid=True)  # Assume valid if can't verify

        result = VerificationResult(
            valid=raw.get("valid", True),
            issues=raw.get("issues", []),
            fix_suggestion=raw.get("fix_suggestion", ""),
            should_retry=raw.get("should_retry", False),
            root_cause=raw.get("root_cause", ""),
        )

        if not result.valid:
            self.state.total_fixes += 1
            logger.info(
                "result_verification_failed",
                tool=tool_name,
                issues=result.issues,
                root_cause=result.root_cause,
            )

        return result

    def _quick_verify(self, tool_name: str, output: str) -> Optional[VerificationResult]:
        """Fast heuristic verification — catches obvious failures without LLM."""
        output_lower = output.lower().strip()

        # Empty output
        if not output or len(output.strip()) < 5:
            return VerificationResult(
                valid=False,
                issues=["Empty or near-empty output"],
                fix_suggestion="Try a different query or tool",
                should_retry=True,
                root_cause="Tool returned no data",
            )

        # Explicit error patterns
        error_patterns = [
            "error:", "exception:", "traceback", "failed to",
            "connection refused", "timeout", "403 forbidden",
            "404 not found", "rate limit", "quota exceeded",
        ]
        for pattern in error_patterns:
            if pattern in output_lower and len(output) < 500:
                return VerificationResult(
                    valid=False,
                    issues=[f"Output contains error: {pattern}"],
                    fix_suggestion="Analyze the error and try a different approach",
                    should_retry=True,
                    root_cause=f"Tool returned error containing '{pattern}'",
                )

        # Search with no results
        if tool_name in ("web_search", "search_files") and any(
            phrase in output_lower for phrase in [
                "no results", "0 results", "nothing found",
                "could not find", "no matches",
            ]
        ):
            return VerificationResult(
                valid=False,
                issues=["Search returned no results"],
                fix_suggestion="Try different keywords: synonyms, broader terms, different language (EN/VI)",
                should_retry=True,
                root_cause="Search query too specific or wrong keywords",
            )

        # Code execution with error
        if tool_name in ("execute_code", "execute_command"):
            if "error" in output_lower and "traceback" in output_lower:
                return VerificationResult(
                    valid=False,
                    issues=["Code execution produced error"],
                    fix_suggestion="Analyze the traceback, identify the bug, fix the code, and re-run",
                    should_retry=True,
                    root_cause="Code has a bug — see traceback for details",
                )

        return None  # No quick verdict → need LLM verification

    @staticmethod
    def _quick_verify_static(tool_name: str, tool_output: str) -> Optional[VerificationResult]:
        """Static wrapper for _quick_verify — usable without GoalEngine instance.

        Fast heuristic checks on tool results without needing to instantiate GoalEngine.
        """
        # Reuse the instance method logic via a temporary dummy
        dummy = GoalEngine.__new__(GoalEngine)
        return dummy._quick_verify(tool_name, tool_output)

    async def evaluate_discovery(
        self,
        content: str,
        source: str,
        task: str,
        round_num: int = 0,
    ) -> Optional[Discovery]:
        """Evaluate an unexpected finding during execution.

        Returns Discovery if relevant, None if not worth tracking.
        """
        user_content = (
            f"MAIN TASK:\n{task[:300]}\n\n"
            f"UNEXPECTED FINDING (from {source}):\n{content[:1000]}"
        )

        messages = [
            {"role": "system", "content": _DISCOVERY_EVAL_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.2)
        except Exception as e:
            logger.debug("discovery_eval_failed", error=str(e))
            return None

        if not raw.get("relevant", False):
            return None

        discovery = Discovery(
            content=content[:500],
            source=source,
            impact=raw.get("impact", ""),
            should_investigate=raw.get("should_investigate", False),
            round_found=round_num,
        )
        self.state.discoveries.append(discovery)

        # If needs investigation, add as new sub-goal
        new_goal_desc = raw.get("new_sub_goal", "")
        if new_goal_desc and discovery.should_investigate:
            next_id = max((sg.id for sg in self.state.sub_goals), default=0) + 1
            self.state.sub_goals.append(SubGoal(
                id=next_id,
                description=f"[DISCOVERY] {new_goal_desc}",
                verification="Discovery investigated and impact assessed",
            ))
            logger.info(
                "discovery_added_sub_goal",
                discovery=content[:100],
                new_goal=new_goal_desc,
            )

        return discovery

    async def build_causal_chain(
        self,
        findings: list[str],
        task: str,
    ) -> list[CausalLink]:
        """Build causal relationships between findings.

        Helps agent see: "A causes B, B leads to C" connections.
        """
        if len(findings) < 2:
            return []

        findings_text = "\n".join(f"- {f[:300]}" for f in findings[:10])
        user_content = (
            f"TASK:\n{task[:300]}\n\n"
            f"FINDINGS:\n{findings_text}"
        )

        messages = [
            {"role": "system", "content": _CAUSAL_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.3)
        except Exception as e:
            logger.debug("causal_chain_failed", error=str(e))
            return []

        links = []
        for link_data in raw.get("causal_links", []):
            if isinstance(link_data, dict):
                link = CausalLink(
                    cause=link_data.get("cause", ""),
                    effect=link_data.get("effect", ""),
                    evidence=link_data.get("evidence", ""),
                    confidence=float(link_data.get("confidence", 0.5)),
                )
                links.append(link)
                self.state.causal_chains.append(link)

        logger.info("causal_chains_built", count=len(links))
        return links

    async def check_completion(
        self,
        task: str,
        current_result: str,
        progress_summary: str = "",
    ) -> dict[str, Any]:
        """Check if task is truly complete — not just partially done.

        Called before returning final answer to user.
        """
        # Build sub-goal status
        goals_text = ""
        if self.state.sub_goals:
            goals_text = "\n".join(
                f"- [{sg.status.value}] {sg.description} → {sg.verification}"
                for sg in self.state.sub_goals
            )

        discoveries_text = ""
        if self.state.discoveries:
            discoveries_text = "\n".join(
                f"- [{('investigated' if d.investigated else 'pending')}] {d.content[:200]}"
                for d in self.state.discoveries
            )

        user_content = (
            f"ORIGINAL TASK:\n{task[:500]}\n\n"
            f"CURRENT RESULT:\n{current_result[:2000]}\n\n"
            f"SUB-GOAL STATUS:\n{goals_text or 'None tracked'}\n\n"
            f"DISCOVERIES:\n{discoveries_text or 'None'}\n\n"
            f"PROGRESS:\n{progress_summary[:500]}"
        )

        messages = [
            {"role": "system", "content": _COMPLETION_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.2)
        except Exception as e:
            logger.debug("completion_check_failed", error=str(e))
            return {"complete": True, "quality_score": 0.5}

        self.state.is_complete = raw.get("complete", True)

        return {
            "complete": raw.get("complete", True),
            "quality_score": float(raw.get("quality_score", 0.5)),
            "missing": raw.get("missing", []),
            "next_steps": raw.get("next_steps", []),
            "user_would_be_satisfied": raw.get("user_would_be_satisfied", True),
        }

    async def verify_criteria(
        self,
        result: str,
        criteria: list[str],
        task: str = "",
    ) -> dict[str, Any]:
        """Verify result against explicit success criteria from task brief.

        Returns dict with met_count, total, unmet list, and met list.
        """
        if not criteria:
            return {"met_count": 0, "total": 0, "unmet": [], "met": []}

        criteria_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))
        user_content = (
            f"TASK:\n{task[:300]}\n\n"
            f"SUCCESS CRITERIA:\n{criteria_text}\n\n"
            f"CURRENT RESULT:\n{result[:3000]}\n\n"
            "For EACH criterion, determine if it is MET or NOT MET based on the result.\n"
            "A criterion is MET only if there is clear evidence in the result."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a Success Criteria Verifier. Check each criterion against the result.\n\n"
                    "Return valid JSON (no markdown):\n"
                    "{\n"
                    '    "assessments": [{"criterion": "...", "met": true/false, "evidence": "..."}],\n'
                    '    "met_count": <number of met criteria>,\n'
                    '    "total": <total criteria>,\n'
                    '    "met": ["criteria that are met"],\n'
                    '    "unmet": ["criteria that are NOT met"]\n'
                    "}\n\n"
                    "Be STRICT: a criterion is only MET if the result clearly satisfies it."
                ),
            },
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self._llm.chat_json(messages, temperature=0.2)
        except Exception as e:
            logger.debug("criteria_verification_failed", error=str(e))
            return {"met_count": 0, "total": len(criteria), "unmet": criteria, "met": []}

        return {
            "met_count": int(raw.get("met_count", 0)),
            "total": int(raw.get("total", len(criteria))),
            "unmet": raw.get("unmet", []),
            "met": raw.get("met", []),
        }

    def get_next_sub_goal(self) -> Optional[SubGoal]:
        """Get the next pending sub-goal to work on."""
        for sg in self.state.sub_goals:
            if sg.status == SubGoalStatus.PENDING:
                # Check dependencies
                deps_met = all(
                    any(g.id == dep and g.status == SubGoalStatus.COMPLETED
                        for g in self.state.sub_goals)
                    for dep in sg.depends_on
                )
                if deps_met:
                    return sg
        return None

    def mark_goal_complete(self, goal_id: int, result: str = "") -> None:
        """Mark a sub-goal as completed."""
        for sg in self.state.sub_goals:
            if sg.id == goal_id:
                sg.status = SubGoalStatus.COMPLETED
                sg.result = result
                self.state.completed_goals += 1
                break

    def mark_goal_failed(self, goal_id: int, reason: str = "") -> None:
        """Mark a sub-goal as failed."""
        for sg in self.state.sub_goals:
            if sg.id == goal_id:
                sg.attempts += 1
                if sg.attempts >= sg.max_attempts:
                    sg.status = SubGoalStatus.FAILED
                    sg.result = f"Failed after {sg.attempts} attempts: {reason}"
                else:
                    sg.status = SubGoalStatus.PENDING  # Will retry
                break

    def build_progress_prompt(self) -> str:
        """Build a progress summary prompt for injection into agent messages."""
        parts = []

        if self.state.sub_goals:
            parts.append("\n## 📋 SUB-GOAL PROGRESS\n")
            for sg in self.state.sub_goals:
                icon = {
                    SubGoalStatus.PENDING: "⬜",
                    SubGoalStatus.IN_PROGRESS: "🔄",
                    SubGoalStatus.COMPLETED: "✅",
                    SubGoalStatus.FAILED: "❌",
                    SubGoalStatus.BLOCKED: "⛔",
                }[sg.status]
                parts.append(f"{icon} **{sg.description}**")
                if sg.status == SubGoalStatus.COMPLETED and sg.result:
                    parts.append(f"   → {sg.result[:200]}")
                elif sg.status == SubGoalStatus.FAILED:
                    parts.append(f"   → FAILED: {sg.result[:200]}")

        pending = [sg for sg in self.state.sub_goals if sg.status == SubGoalStatus.PENDING]
        if pending:
            next_goal = pending[0]
            parts.append(f"\n**NEXT**: {next_goal.description}")
            parts.append(f"**VERIFY BY**: {next_goal.verification}")

        if self.state.discoveries:
            uninvestigated = [d for d in self.state.discoveries if not d.investigated and d.should_investigate]
            if uninvestigated:
                parts.append("\n## 🔍 DISCOVERIES TO INVESTIGATE")
                for d in uninvestigated[:3]:
                    parts.append(f"- ⚠️ {d.content[:200]}")
                    if d.impact:
                        parts.append(f"  Impact: {d.impact}")

        if self.state.causal_chains:
            parts.append("\n## 🔗 CAUSAL CONNECTIONS")
            for chain in self.state.causal_chains[-5:]:
                parts.append(f"- {chain.cause} → {chain.effect} (confidence: {chain.confidence:.0%})")

        if not parts:
            return ""

        parts.append(
            "\n**INSTRUCTIONS**: Work through sub-goals in order. "
            "VERIFY each result. Investigate discoveries. "
            "Do NOT stop until ALL sub-goals are ✅ or ❌.\n"
        )

        return "\n".join(parts)

    def build_verification_prompt(
        self,
        tool_name: str,
        tool_output: str,
    ) -> str:
        """Build a compact verification prompt for inline injection.

        This is injected as a system message AFTER tool results to force
        the agent to critically evaluate what it just received.
        """
        return (
            f"[AUTO-VERIFY] You just called `{tool_name}`. "
            "Before proceeding, critically evaluate:\n"
            "1. Did this actually work? Is the data correct and complete?\n"
            "2. If there are errors → identify ROOT CAUSE, fix, and retry\n"
            "3. If data is empty/irrelevant → try DIFFERENT approach (different keywords, different tool)\n"
            "4. If you found something UNEXPECTED → note it and assess impact on your task\n"
            "5. Does this move you closer to your sub-goal? If not, reassess.\n"
            "Do NOT accept failures silently. Do NOT move on without valid data."
        )

    def get_stats(self) -> dict[str, Any]:
        """Get goal engine statistics."""
        return {
            "total_sub_goals": len(self.state.sub_goals),
            "completed_goals": self.state.completed_goals,
            "pending_goals": sum(1 for sg in self.state.sub_goals if sg.status == SubGoalStatus.PENDING),
            "failed_goals": sum(1 for sg in self.state.sub_goals if sg.status == SubGoalStatus.FAILED),
            "discoveries": len(self.state.discoveries),
            "causal_chains": len(self.state.causal_chains),
            "total_verifications": self.state.total_verifications,
            "total_fixes": self.state.total_fixes,
            "is_complete": self.state.is_complete,
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def get_goal_engine(llm: Any) -> GoalEngine:
    """Factory for GoalEngine."""
    return GoalEngine(llm)
