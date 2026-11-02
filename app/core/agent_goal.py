"""
Nexus - Agent Goal Tracker.

Provides agents with explicit awareness of their objectives, success criteria,
and progress toward completion. Prevents agents from wandering off-task.

Usage:
    goal = AgentGoal(
        final_objective="Analyze market trends for VN tech sector",
        success_criteria=["Include 2024-2025 data", "Compare with ASEAN peers"],
        my_role="researcher",
        definition_of_done="Comprehensive analysis with cited sources",
    )
    tracker = AgentGoalTracker(goal)
    reminder = tracker.get_goal_reminder()  # Inject into agent prompt
    progress = tracker.check_progress(current_output)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentGoal:
    """What this agent needs to achieve in the current task."""

    final_objective: str  # User's original request (full text)
    success_criteria: list[str] = field(default_factory=list)  # From deep analysis
    my_role: str = ""  # Agent's specific contribution
    my_expertise: str = ""  # What unique knowledge I bring
    definition_of_done: str = ""  # When can I say "I'm done"
    debate_role: str = ""  # Role in debate: advocate/critic/synthesizer

    # Progress tracking (mutable)
    criteria_met: list[bool] = field(default_factory=list)
    progress_notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize criteria_met tracking."""
        if not self.criteria_met and self.success_criteria:
            self.criteria_met = [False] * len(self.success_criteria)


@dataclass
class GoalProgress:
    """Assessment of progress toward goal completion."""

    criteria_met_count: int = 0
    criteria_total: int = 0
    completion_pct: float = 0.0
    unmet_criteria: list[str] = field(default_factory=list)
    is_complete: bool = False
    assessment: str = ""


class AgentGoalTracker:
    """Tracks agent progress toward goal completion.

    Integrates with debate system to give agents persistent awareness
    of what they need to achieve, preventing topic drift and ensuring
    comprehensive coverage of all success criteria.
    """

    def __init__(self, goal: AgentGoal) -> None:
        self._goal = goal

    @property
    def goal(self) -> AgentGoal:
        return self._goal

    def get_goal_reminder(self) -> str:
        """Generate goal context for injection into agent prompt.

        Returns formatted string reminding agent of their objective,
        criteria, and current progress.
        """
        parts = [
            "## 🎯 YOUR GOAL (Always keep this in mind)\n",
            f"**Final Objective**: {self._goal.final_objective}\n",
        ]

        if self._goal.my_role:
            parts.append(f"**Your Role**: {self._goal.my_role}\n")

        if self._goal.my_expertise:
            parts.append(f"**Your Expertise**: {self._goal.my_expertise}\n")

        if self._goal.debate_role:
            parts.append(f"**Debate Role**: {self._goal.debate_role}\n")

        if self._goal.definition_of_done:
            parts.append(f"**Definition of Done**: {self._goal.definition_of_done}\n")

        if self._goal.success_criteria:
            parts.append("\n**Success Criteria** (all must be met):")
            for i, criterion in enumerate(self._goal.success_criteria):
                met = self._goal.criteria_met[i] if i < len(self._goal.criteria_met) else False
                icon = "✅" if met else "⬜"
                parts.append(f"  {icon} {criterion}")
            parts.append("")

        # Progress summary
        met_count = sum(1 for m in self._goal.criteria_met if m)
        total = len(self._goal.success_criteria)
        if total > 0:
            parts.append(
                f"**Progress**: {met_count}/{total} criteria met "
                f"({met_count * 100 // total}%)\n"
            )

        return "\n".join(parts)

    def check_progress(self, current_output: str = "") -> GoalProgress:
        """Assess current progress toward goal completion.

        Args:
            current_output: Agent's current accumulated output for evaluation.

        Returns:
            GoalProgress with assessment of completion status.
        """
        met_count = sum(1 for m in self._goal.criteria_met if m)
        total = len(self._goal.success_criteria)
        pct = met_count / total if total > 0 else 0.0

        unmet = [
            criterion
            for criterion, met in zip(
                self._goal.success_criteria, self._goal.criteria_met
            )
            if not met
        ]

        is_complete = met_count == total and total > 0

        assessment = ""
        if is_complete:
            assessment = "All criteria met. Goal complete."
        elif pct >= 0.7:
            assessment = f"Good progress ({met_count}/{total}). Remaining: {', '.join(unmet[:3])}"
        elif pct >= 0.3:
            assessment = f"Partial progress ({met_count}/{total}). Focus on: {', '.join(unmet[:3])}"
        else:
            assessment = f"Early stage ({met_count}/{total}). Priority: {unmet[0] if unmet else 'N/A'}"

        return GoalProgress(
            criteria_met_count=met_count,
            criteria_total=total,
            completion_pct=pct,
            unmet_criteria=unmet,
            is_complete=is_complete,
            assessment=assessment,
        )

    def mark_criterion_met(self, index: int, evidence: str = "") -> None:
        """Mark a success criterion as satisfied.

        Args:
            index: Index of the criterion (0-based).
            evidence: Optional evidence/reason why it's met.
        """
        if 0 <= index < len(self._goal.criteria_met):
            self._goal.criteria_met[index] = True
            if evidence:
                self._goal.progress_notes.append(
                    f"Criterion {index} met: {evidence[:200]}"
                )

    def add_progress_note(self, note: str) -> None:
        """Add a progress note (agent's self-observation)."""
        self._goal.progress_notes.append(note[:500])

    def get_unmet_criteria_prompt(self) -> str:
        """Get prompt-friendly list of unmet criteria for agent focus."""
        unmet = [
            criterion
            for criterion, met in zip(
                self._goal.success_criteria, self._goal.criteria_met
            )
            if not met
        ]
        if not unmet:
            return ""
        return (
            "## ⚠️ UNMET CRITERIA (focus on these)\n"
            + "\n".join(f"- {c}" for c in unmet)
            + "\n"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "final_objective": self._goal.final_objective,
            "success_criteria": self._goal.success_criteria,
            "criteria_met": self._goal.criteria_met,
            "my_role": self._goal.my_role,
            "debate_role": self._goal.debate_role,
            "definition_of_done": self._goal.definition_of_done,
            "progress_notes": self._goal.progress_notes,
        }
