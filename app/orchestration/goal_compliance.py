"""
Nexus - Goal Compliance Checker.

FIX GAP-2: Hard enforcement against goal drift.

Problem: GOAL_ANCHOR.txt is injected into context but LLM can still ignore it.
After many turns of tool usage, agents drift toward tangential work.

Solution: After each agent execution, score output relevance against the original
goal. If drift detected (score < threshold), inject corrective signal into next
turn's context AND reduce that agent's priority in subsequent planning.

Mechanism:
- Fast heuristic check first (keyword overlap, no LLM call)
- LLM-based deep check only when heuristic is ambiguous
- Drift history tracked per agent (patterns trigger stronger corrections)
- Corrective signal injected into TurnExecutor's previous_context
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_DRIFT_THRESHOLD_HEURISTIC = 0.3  # Below this → definite drift
_DRIFT_THRESHOLD_WARN = 0.5  # Below this → possible drift, LLM check
_MAX_DRIFT_HISTORY = 20
_CONSECUTIVE_DRIFT_LIMIT = 3  # 3 consecutive drifts → hard correction


@dataclass
class DriftSignal:
    """Result of a drift check for one agent execution."""

    agent_role: str
    turn: int
    relevance_score: float  # 0.0 (completely off-topic) to 1.0 (perfectly aligned)
    is_drifting: bool
    drift_description: str = ""
    corrective_action: str = ""


@dataclass
class GoalProfile:
    """Extracted features of the user's goal for fast comparison."""

    original_message: str
    keywords: set[str] = field(default_factory=set)
    intent_verbs: list[str] = field(default_factory=list)
    success_criteria: str = ""
    domain_terms: set[str] = field(default_factory=set)

    @classmethod
    def from_message(cls, message: str, success_criteria: str = "") -> "GoalProfile":
        """Extract goal features from user message."""
        # Extract meaningful keywords (3+ chars, not stopwords)
        _STOPWORDS = {
            "the", "and", "for", "that", "this", "with", "from", "have", "has",
            "are", "was", "were", "been", "being", "will", "would", "could",
            "should", "shall", "may", "might", "can", "not", "but", "all",
            "any", "each", "every", "some", "most", "more", "than", "then",
            "also", "just", "only", "very", "too", "how", "what", "when",
            "where", "which", "who", "why", "into", "about", "your", "our",
        }
        words = re.findall(r'\b[a-zA-Z_]\w{2,}\b', message.lower())
        keywords = {w for w in words if w not in _STOPWORDS}

        # Extract intent verbs (first few action words)
        _INTENT_VERBS = {
            "build", "create", "fix", "find", "search", "scan", "test",
            "implement", "add", "remove", "update", "deploy", "analyze",
            "investigate", "exploit", "enumerate", "discover", "write",
            "configure", "setup", "install", "migrate", "refactor",
            "optimize", "secure", "audit", "review", "check", "verify",
        }
        intent_verbs = [w for w in words if w in _INTENT_VERBS][:5]

        # Domain terms: longer words, technical terms, capitalized
        domain_terms = {w for w in re.findall(r'\b[A-Z][a-zA-Z]+\b', message)}
        domain_terms.update(w for w in keywords if len(w) >= 5)

        return cls(
            original_message=message,
            keywords=keywords,
            intent_verbs=intent_verbs,
            success_criteria=success_criteria,
            domain_terms=domain_terms,
        )


class GoalComplianceChecker:
    """Enforces goal alignment across turns.

    Tracks drift per agent and per turn. When drift is detected:
    1. Immediate: corrective message injected into next turn context
    2. Sustained: agent gets deprioritized in planning
    3. Severe: force-redirect the entire task focus

    Uses fast heuristic first, LLM check only when ambiguous.
    """

    def __init__(self, goal_profile: GoalProfile, llm: Any = None) -> None:
        self._goal = goal_profile
        self._llm = llm
        self._drift_history: list[DriftSignal] = []
        self._agent_drift_counts: dict[str, int] = {}  # role → consecutive drift count
        self._corrective_context: str = ""  # Injected into next turn

    @property
    def corrective_context(self) -> str:
        """Get the drift-correction to inject this turn (idempotent PEEK, A12).

        Was consume-once (read → cleared), but TWO builders read it each turn — the
        orchestrator eval-context (pipeline) and the agent-context (turn_executor).
        The first read consumed it, so the AGENT — the one that most needs steering
        back on goal — usually saw "". Reading no longer clears; clearing is the
        explicit end-of-turn ``clear_corrective_context()``.
        """
        return self._corrective_context

    def clear_corrective_context(self) -> None:
        """Clear the corrective context — call ONCE at end of turn, after both the
        eval-context and agent-context builders have read it."""
        self._corrective_context = ""

    @property
    def drifting_agents(self) -> list[str]:
        """Agents currently in sustained drift (3+ consecutive)."""
        return [
            role for role, count in self._agent_drift_counts.items()
            if count >= _CONSECUTIVE_DRIFT_LIMIT
        ]

    def check_output(self, agent_role: str, output: str, turn: int) -> DriftSignal:
        """Check if agent output aligns with goal.

        Fast path: heuristic keyword overlap (no LLM call).
        Returns DriftSignal with relevance score and corrective action.
        """
        if not output or len(output) < 50:
            # Too short to judge
            return DriftSignal(
                agent_role=agent_role, turn=turn,
                relevance_score=0.5, is_drifting=False,
            )

        score = self._heuristic_score(output)

        is_drifting = score < _DRIFT_THRESHOLD_HEURISTIC
        drift_desc = ""
        corrective = ""

        if is_drifting:
            drift_desc = self._describe_drift(output, score)
            corrective = self._build_corrective(agent_role, drift_desc, turn)
            self._agent_drift_counts[agent_role] = self._agent_drift_counts.get(agent_role, 0) + 1
        elif score < _DRIFT_THRESHOLD_WARN:
            # Borderline — soft warning but don't count as drift
            drift_desc = "Borderline relevance — agent may be going off-track"
            is_drifting = False
            self._agent_drift_counts[agent_role] = max(0, self._agent_drift_counts.get(agent_role, 0) - 1)
        else:
            # On track — reset drift counter
            self._agent_drift_counts[agent_role] = 0

        signal = DriftSignal(
            agent_role=agent_role,
            turn=turn,
            relevance_score=score,
            is_drifting=is_drifting,
            drift_description=drift_desc,
            corrective_action=corrective,
        )

        self._drift_history.append(signal)
        if len(self._drift_history) > _MAX_DRIFT_HISTORY:
            self._drift_history = self._drift_history[-_MAX_DRIFT_HISTORY:]

        # Build corrective context for next turn
        if is_drifting:
            self._update_corrective_context(signal)

        return signal

    def _goal_criteria_terms(self) -> set[str]:
        """Meaningful terms from the goal's success_criteria (cached).

        Same stopword/length filter as keyword extraction, so it composes with the
        existing overlap math. Empty set when no criteria — callers then keep the
        original weighting (no behaviour change for criteria-less tasks).
        """
        cached = getattr(self, "_criteria_terms_cache", None)
        if cached is not None:
            return cached
        crit = (self._goal.success_criteria or "").lower()
        if not crit:
            terms: set[str] = set()
        else:
            words = re.findall(r'\b[a-zA-Z_]\w{2,}\b', crit)
            # Reuse the prompt's keyword set's stopword spirit: drop very common
            # scaffolding words that the criteria template adds ("done", "when",
            # "verify") so they don't dilute the signal.
            _CRIT_STOP = {"done", "when", "verify", "the", "and", "for", "with",
                          "that", "this", "are", "its", "via", "per"}
            terms = {w for w in words if w not in _CRIT_STOP and len(w) >= 3}
        self._criteria_terms_cache: set[str] = terms
        return terms

    def _heuristic_score(self, output: str) -> float:
        """Fast relevance score using keyword/term overlap."""
        output_lower = output.lower()
        output_words = set(re.findall(r'\b[a-zA-Z_]\w{2,}\b', output_lower))

        if not self._goal.keywords:
            return 0.7  # Can't judge without keywords

        # Keyword overlap (weighted)
        keyword_hits = len(self._goal.keywords & output_words)
        keyword_ratio = keyword_hits / max(len(self._goal.keywords), 1)

        # Domain term overlap (higher weight)
        domain_hits = sum(1 for t in self._goal.domain_terms if t.lower() in output_lower)
        domain_ratio = domain_hits / max(len(self._goal.domain_terms), 1)

        # Intent verb presence
        intent_hits = sum(1 for v in self._goal.intent_verbs if v in output_lower)
        intent_ratio = intent_hits / max(len(self._goal.intent_verbs), 1)

        # Base relevance from the prompt's own vocabulary (unchanged).
        score = (keyword_ratio * 0.4) + (domain_ratio * 0.35) + (intent_ratio * 0.25)

        # Success-criteria overlap (A7): the measurable definition_of_done / verify
        # terms are the bar the work must hit. Add it as a BONUS (never a penalty) —
        # criteria vocabulary is largely disjoint from the prompt's, so redistributing
        # weight would push a prompt-aligned output that doesn't echo the verify words
        # BELOW the drift threshold (false drift). An additive bonus guarantees adding
        # criteria can only raise the score, never lower it. Capped by min(1.0) below.
        criteria_terms = self._goal_criteria_terms()
        if criteria_terms:
            criteria_hits = len(criteria_terms & output_words)
            criteria_ratio = criteria_hits / max(len(criteria_terms), 1)
            score += criteria_ratio * 0.25

        # Bonus: if output references original message fragments
        msg_fragments = [
            self._goal.original_message[i:i+20].lower()
            for i in range(0, min(len(self._goal.original_message), 200), 40)
        ]
        fragment_hits = sum(1 for f in msg_fragments if f in output_lower)
        if fragment_hits:
            score = min(1.0, score + 0.1 * fragment_hits)

        return min(1.0, score)

    def _describe_drift(self, output: str, score: float) -> str:
        """Describe what the agent drifted toward."""
        output_words = set(re.findall(r'\b[a-zA-Z_]\w{3,}\b', output.lower()))
        off_topic_words = output_words - self._goal.keywords - self._goal.domain_terms
        # Find the most frequent off-topic words
        if off_topic_words:
            sample = sorted(off_topic_words, key=lambda w: output.lower().count(w), reverse=True)[:5]
            return f"Agent focused on [{', '.join(sample)}] instead of goal keywords"
        return f"Low relevance score ({score:.2f}) — output doesn't address the original request"

    def _build_corrective(self, agent_role: str, drift_desc: str, turn: int) -> str:
        """Build corrective message for the drifting agent."""
        consecutive = self._agent_drift_counts.get(agent_role, 0)

        if consecutive >= _CONSECUTIVE_DRIFT_LIMIT:
            # Severe: hard redirect
            return (
                f"⚠️ CRITICAL DRIFT ALERT (turn {turn}): {agent_role} has drifted "
                f"{consecutive} consecutive turns. {drift_desc}. "
                f"MANDATORY: Re-read the GOAL ANCHOR and align ALL work to it. "
                f"The original request was: {self._goal.original_message[:200]}"
            )
        else:
            return (
                f"⚠️ DRIFT WARNING (turn {turn}): {drift_desc}. "
                f"Refocus on: {self._goal.original_message[:150]}"
            )

    def _update_corrective_context(self, signal: DriftSignal) -> None:
        """Accumulate corrective context for next turn injection."""
        if signal.corrective_action:
            if self._corrective_context:
                self._corrective_context += "\n" + signal.corrective_action
            else:
                self._corrective_context = (
                    "## ⚠️ GOAL DRIFT DETECTED — CORRECTIONS REQUIRED\n"
                    + signal.corrective_action
                )

    def get_drift_summary(self) -> dict[str, Any]:
        """Get summary of drift state across all agents."""
        return {
            "total_drift_events": sum(1 for s in self._drift_history if s.is_drifting),
            "total_checks": len(self._drift_history),
            "drifting_agents": self.drifting_agents,
            "agent_drift_counts": dict(self._agent_drift_counts),
            "avg_relevance": (
                sum(s.relevance_score for s in self._drift_history) / len(self._drift_history)
                if self._drift_history else 0.0
            ),
        }
