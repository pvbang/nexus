"""
MekongAI Nexus - Agent Activity Report.

Structured report of everything an agent did during task execution.
Captures tools called, arguments made, evidence collected, reasoning chain,
and final conclusions — enabling full transparency and audit trail.

Usage:
    report = AgentActivityReport(agent_name="Researcher", agent_role="researcher")
    report.record_tool_call("web_search", {"query": "..."}, "Found X", 1200)
    report.record_argument("Market is growing", ["GDP data shows 8%"], 0.85)
    markdown = report.to_markdown()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ToolCallRecord:
    """Record of a single tool call."""

    tool_name: str
    params: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    success: bool = True
    duration_ms: float = 0.0
    turn: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class ArgumentRecord:
    """Record of an argument/claim made by the agent."""

    claim: str
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5
    challenged_by: list[str] = field(default_factory=list)  # Agent names
    defended: bool = False
    turn: int = 0


@dataclass
class ChallengeRecord:
    """Record of a challenge received from another agent."""

    from_agent: str
    challenge_content: str
    my_response: str = ""
    responded: bool = False
    turn_received: int = 0
    turn_responded: int = 0


@dataclass
class EvidenceRecord:
    """Record of evidence collected."""

    content: str
    source_tool: str = ""
    source_url: str = ""
    relevance: float = 0.5
    turn: int = 0


class AgentActivityReport:
    """Complete report of what an agent did during task execution.

    Accumulates data throughout the debate/task lifecycle and can
    generate a comprehensive markdown report at the end.
    """

    def __init__(self, agent_name: str, agent_role: str) -> None:
        self.agent_name = agent_name
        self.agent_role = agent_role

        # Timeline records
        self.tools_called: list[ToolCallRecord] = []
        self.arguments_made: list[ArgumentRecord] = []
        self.questions_asked: list[str] = []
        self.challenges_received: list[ChallengeRecord] = []
        self.evidence_collected: list[EvidenceRecord] = []

        # Reasoning
        self.reasoning_chain: list[str] = []
        self.key_findings: list[str] = []
        self.position_evolution: list[str] = []  # How position changed over time

        # Self-assessment
        self.confidence_level: float = 0.5
        self.areas_of_uncertainty: list[str] = []
        self.final_position: str = ""

        # Metrics
        self.total_turns_participated: int = 0
        self.messages_posted: int = 0
        self.start_time: str = datetime.now(timezone.utc).isoformat()
        self.end_time: str = ""

    # ─── Recording Methods ───────────────────────────────────────────

    def record_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
        result_summary: str = "",
        duration_ms: float = 0.0,
        success: bool = True,
        turn: int = 0,
    ) -> None:
        """Record a tool call."""
        self.tools_called.append(ToolCallRecord(
            tool_name=tool_name,
            params=params or {},
            result_summary=result_summary[:500],
            success=success,
            duration_ms=duration_ms,
            turn=turn,
        ))

    def record_argument(
        self,
        claim: str,
        evidence: list[str] | None = None,
        confidence: float = 0.5,
        turn: int = 0,
    ) -> None:
        """Record an argument/claim made."""
        self.arguments_made.append(ArgumentRecord(
            claim=claim[:300],
            evidence=evidence or [],
            confidence=confidence,
            turn=turn,
        ))

    def record_challenge_received(
        self,
        from_agent: str,
        challenge_content: str,
        turn: int = 0,
    ) -> None:
        """Record a challenge received from another agent."""
        self.challenges_received.append(ChallengeRecord(
            from_agent=from_agent,
            challenge_content=challenge_content[:500],
            turn_received=turn,
        ))

    def record_challenge_response(
        self,
        from_agent: str,
        response: str,
        turn: int = 0,
    ) -> None:
        """Record response to a challenge."""
        for challenge in reversed(self.challenges_received):
            if challenge.from_agent == from_agent and not challenge.responded:
                challenge.my_response = response[:500]
                challenge.responded = True
                challenge.turn_responded = turn
                break

    def record_evidence(
        self,
        content: str,
        source_tool: str = "",
        source_url: str = "",
        relevance: float = 0.5,
        turn: int = 0,
    ) -> None:
        """Record evidence collected."""
        self.evidence_collected.append(EvidenceRecord(
            content=content[:500],
            source_tool=source_tool,
            source_url=source_url,
            relevance=relevance,
            turn=turn,
        ))

    def record_reasoning_step(self, step: str) -> None:
        """Record a step in the reasoning chain."""
        self.reasoning_chain.append(step[:300])

    def record_key_finding(self, finding: str) -> None:
        """Record a key finding/discovery."""
        self.key_findings.append(finding[:300])

    def record_position_change(self, new_position: str) -> None:
        """Record evolution of position."""
        self.position_evolution.append(new_position[:300])

    def finalize(
        self,
        final_position: str = "",
        confidence: float = 0.5,
        uncertainties: list[str] | None = None,
    ) -> None:
        """Finalize the report with final assessment."""
        self.final_position = final_position
        self.confidence_level = confidence
        self.areas_of_uncertainty = uncertainties or []
        self.end_time = datetime.now(timezone.utc).isoformat()

    # ─── Output Methods ──────────────────────────────────────────────

    def to_markdown(self) -> str:
        """Generate readable markdown report."""
        parts = [
            f"# Agent Report: {self.agent_name} ({self.agent_role})\n",
            f"**Turns participated**: {self.total_turns_participated} | "
            f"**Messages**: {self.messages_posted} | "
            f"**Tools used**: {len(self.tools_called)} | "
            f"**Confidence**: {self.confidence_level:.0%}\n",
        ]

        # Tools used
        if self.tools_called:
            parts.append("\n## 🔧 Tools Used\n")
            for tc in self.tools_called:
                status = "✅" if tc.success else "❌"
                parts.append(
                    f"- {status} `{tc.tool_name}` (Turn {tc.turn}, {tc.duration_ms:.0f}ms): "
                    f"{tc.result_summary[:100]}"
                )

        # Key Findings
        if self.key_findings:
            parts.append("\n## 📊 Key Findings\n")
            for f in self.key_findings:
                parts.append(f"- {f}")

        # Arguments Made
        if self.arguments_made:
            parts.append("\n## 💬 Arguments Made\n")
            for arg in self.arguments_made:
                confidence_bar = "🟢" if arg.confidence > 0.7 else "🟡" if arg.confidence > 0.4 else "🔴"
                parts.append(f"- {confidence_bar} **{arg.claim}**")
                if arg.evidence:
                    for e in arg.evidence[:3]:
                        parts.append(f"  - Evidence: {e[:100]}")
                if arg.challenged_by:
                    parts.append(f"  - ⚔️ Challenged by: {', '.join(arg.challenged_by)}")

        # Challenges Received & Responses
        if self.challenges_received:
            parts.append("\n## ⚔️ Challenges Received\n")
            for ch in self.challenges_received:
                status = "✅ Responded" if ch.responded else "❌ Unanswered"
                parts.append(f"- From **{ch.from_agent}** (Turn {ch.turn_received}): {status}")
                parts.append(f"  Challenge: {ch.challenge_content[:150]}")
                if ch.my_response:
                    parts.append(f"  Response: {ch.my_response[:150]}")

        # Reasoning Chain
        if self.reasoning_chain:
            parts.append("\n## 🧠 Reasoning Chain\n")
            for i, step in enumerate(self.reasoning_chain, 1):
                parts.append(f"{i}. {step}")

        # Position Evolution
        if self.position_evolution:
            parts.append("\n## 📈 Position Evolution\n")
            for i, pos in enumerate(self.position_evolution, 1):
                parts.append(f"**Stage {i}**: {pos}")

        # Final Position
        if self.final_position:
            parts.append(f"\n## 🏁 Final Position\n\n{self.final_position}\n")

        # Uncertainties
        if self.areas_of_uncertainty:
            parts.append("\n## ❓ Areas of Uncertainty\n")
            for u in self.areas_of_uncertainty:
                parts.append(f"- {u}")

        return "\n".join(parts)

    def to_summary(self, max_chars: int = 1000) -> str:
        """Generate short summary for inclusion in synthesis."""
        tools_summary = ", ".join(set(tc.tool_name for tc in self.tools_called[:5]))
        findings_summary = "; ".join(self.key_findings[:3])

        summary = (
            f"**{self.agent_name}** ({self.agent_role}): "
            f"Used {len(self.tools_called)} tools ({tools_summary}). "
            f"Key findings: {findings_summary}. "
            f"Final position: {self.final_position[:500]}. "
            f"Confidence: {self.confidence_level:.0%}."
        )
        return summary[:max_chars]

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence/events."""
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "total_turns": self.total_turns_participated,
            "messages_posted": self.messages_posted,
            "tools_called": [
                {"tool": tc.tool_name, "success": tc.success, "turn": tc.turn}
                for tc in self.tools_called
            ],
            "arguments_count": len(self.arguments_made),
            "challenges_received": len(self.challenges_received),
            "challenges_responded": sum(1 for c in self.challenges_received if c.responded),
            "key_findings": self.key_findings[:10],
            "final_position": self.final_position[:500],
            "confidence": self.confidence_level,
            "areas_of_uncertainty": self.areas_of_uncertainty[:5],
            "evidence_count": len(self.evidence_collected),
        }
