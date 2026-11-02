"""
Nexus - Context Sharing Board.

Manages context visibility between agents within a turn.
Each agent can see other agents' thinking and results through this board.

CONTEXT MANAGEMENT STRATEGY:
- Agent thinking: shared in full (usually short, < 2KB each)
- Agent tool results (sequential mode): capped per agent to control budget
- Orchestrator evaluation view: summaries only, full data in ACTIVITY.md
"""

from __future__ import annotations

from typing import Any

import structlog

from app.orchestration.models import AgentResult, AgentThinking, _extract_conclusion

logger = structlog.get_logger(__name__)

# Context sharing caps
_AGENT_RESULT_CAP_CHARS = 8000  # Cap other agents' output in shared context
_ORCHESTRATOR_AGENT_CAP_CHARS = 12000  # Cap per-agent output for orchestrator view


class ContextSharing:
    """Context board for inter-agent communication within a turn.

    Agents post their thinking and results here.
    Other agents can read the board to see what their peers produced.

    CONTEXT MANAGEMENT: Different consumers get different detail levels:
    - Agents see peers' thinking (full) + peers' results (conclusion-extracted, 8KB each)
    - Orchestrator sees conclusions (12KB/agent) + tool call summaries (500 char hints)
    - ACTIVITY.md has everything in full (separate concern, see activity_logger.py)

    All output extraction uses _extract_conclusion() which prioritizes the TAIL
    of agent output (where conclusions/summaries live) over raw prefix.
    """

    def __init__(self) -> None:
        self._thinkings: dict[str, AgentThinking] = {}
        self._results: dict[str, AgentResult] = {}
        self._turn_number: int = 0
        self._active_actions: dict[str, str] = {}  # action_key → agent_role

    def reset_for_turn(self, turn_number: int) -> None:
        """Clear board for a new turn."""
        self._thinkings.clear()
        self._results.clear()
        self._active_actions.clear()
        self._turn_number = turn_number

    def add_thinking(self, agent_role: str, thinking: AgentThinking) -> None:
        """Register an agent's thinking for others to see."""
        self._thinkings[agent_role] = thinking

    def add_result(self, agent_role: str, result: AgentResult) -> None:
        """Register an agent's execution result for others to see.

        Two agents sharing a role within a turn must not silently clobber each
        other — the second result is stored under a uniquified key so no agent's
        output is lost from the shared board.
        """
        from app.orchestration.state_reducers import merge_keyed
        merge_keyed(self._results, agent_role, result, on_conflict="suffix")

    def register_action(self, agent_role: str, action_key: str) -> str | None:
        """Register an action being taken by an agent.

        Returns None if action is free, or the role of the agent already doing it.
        """
        normalized = action_key.lower().strip()[:200]
        if normalized in self._active_actions:
            owner = self._active_actions[normalized]
            if owner != agent_role:
                return owner
        self._active_actions[normalized] = agent_role
        return None

    def get_active_actions_summary(self, exclude_role: str = "") -> str:
        """Get summary of what other agents are currently working on."""
        actions = [
            f"- {role}: {action}"
            for action, role in self._active_actions.items()
            if role != exclude_role
        ]
        if not actions:
            return ""
        return "## In-Progress By Other Agents (avoid duplicating):\n" + "\n".join(actions[:10])

    def get_shared_context(self, for_agent_role: str) -> str:
        """Get context visible to a specific agent (excludes own thinking).

        CONTEXT MANAGEMENT:
        - Other agents' thinking: full (usually short)
        - Other agents' tool results (sequential mode): capped at 5KB each
        """
        parts: list[str] = []

        # Other agents' thinking — full (usually short)
        for role, thinking in self._thinkings.items():
            if role == for_agent_role:
                continue
            section = f"### {thinking.agent_name} ({role})\n"
            section += f"**Thinking**: {thinking.thinking}\n"
            if thinking.challenges:
                section += "**Challenges**: " + "; ".join(str(c) for c in thinking.challenges) + "\n"
            if thinking.supports:
                section += "**Supports**: " + "; ".join(str(s) for s in thinking.supports) + "\n"
            parts.append(section)

        # Other agents' results (if available — sequential execution)
        # Uses _extract_conclusion to prioritize findings/conclusions over raw prefix
        for role, result in self._results.items():
            if role == for_agent_role:
                continue
            section = f"### {result.agent_name} ({role}) — Completed\n"
            if result.output:
                output_text = _extract_conclusion(result.output, _AGENT_RESULT_CAP_CHARS)
                if len(result.output) > _AGENT_RESULT_CAP_CHARS:
                    output_text += (
                        f"\n[Full output: {len(result.output)} chars — "
                        f"use activity_search(keyword=\"{result.agent_name}\", turn={self._turn_number})]"
                    )
                section += f"**Output**: {output_text}\n"
            if result.tool_calls:
                tools_used = [tc.tool_name for tc in result.tool_calls[:5]]
                section += f"**Tools used**: {', '.join(tools_used)}\n"
            parts.append(section)

        return "\n".join(parts) if parts else ""

    def get_shared_context_with_budget(
        self,
        for_agent_role: str,
        max_chars: int = 15000,
    ) -> str:
        """Get context visible to a specific agent with total budget control.

        Like get_shared_context but enforces a hard total character budget.
        Prioritizes: thinking first (usually short), then results (capped).
        """
        parts: list[str] = []
        remaining = max_chars

        # Phase 1: Other agents' thinking — full (priority)
        for role, thinking in self._thinkings.items():
            if role == for_agent_role:
                continue
            section = f"### {thinking.agent_name} ({role})\n"
            section += f"**Thinking**: {thinking.thinking}\n"
            if thinking.challenges:
                section += "**Challenges**: " + "; ".join(str(c) for c in thinking.challenges) + "\n"
            if thinking.supports:
                section += "**Supports**: " + "; ".join(str(s) for s in thinking.supports) + "\n"
            if len(section) > remaining:
                break
            parts.append(section)
            remaining -= len(section)

        # Phase 2: Other agents' results — budget-aware with conclusion extraction
        if remaining > 200:
            per_agent_budget = max(1000, remaining // max(1, len(self._results)))
            for role, result in self._results.items():
                if role == for_agent_role:
                    continue
                if remaining < 200:
                    break
                cap = min(per_agent_budget, remaining, _AGENT_RESULT_CAP_CHARS)
                section = f"### {result.agent_name} ({role}) — Completed\n"
                if result.output:
                    output_text = _extract_conclusion(result.output, cap)
                    if len(result.output) > cap:
                        output_text += (
                            f"\n[Full output: {len(result.output)} chars — "
                            f"use activity_search(keyword=\"{result.agent_name}\", turn={self._turn_number})]"
                        )
                    section += f"**Output**: {output_text}\n"
                if result.tool_calls:
                    tools_used = [tc.tool_name for tc in result.tool_calls[:5]]
                    section += f"**Tools used**: {', '.join(tools_used)}\n"
                parts.append(section)
                remaining -= len(section)

        return "\n".join(parts) if parts else ""

    def get_full_turn_context(self) -> str:
        """Get context for Orchestrator evaluation.

        CONTEXT MANAGEMENT:
        - Orchestrator sees summaries, ACTIVITY.md has full details.
        - Agent outputs: capped at 8KB each
        - Tool calls: summary only (tool name + success/fail), NOT full output
        """
        parts: list[str] = [f"# Turn {self._turn_number} Results\n"]

        for role in sorted(set(list(self._thinkings.keys()) + list(self._results.keys()))):
            thinking = self._thinkings.get(role)
            result = self._results.get(role)

            agent_name = ""
            if thinking:
                agent_name = thinking.agent_name
            elif result:
                agent_name = result.agent_name

            section = f"## {agent_name} ({role})\n"

            if thinking:
                section += f"### Thinking\n{thinking.thinking}\n"
                if thinking.challenges:
                    section += "**Challenges**: " + "; ".join(str(c) for c in thinking.challenges) + "\n"
                if thinking.supports:
                    section += "**Supports**: " + "; ".join(str(s) for s in thinking.supports) + "\n"

            if result:
                section += "### Execution\n"
                if result.tool_calls:
                    section += f"**Tools called**: {len(result.tool_calls)}\n"
                    for tc in result.tool_calls[:15]:
                        status = "✅" if tc.success else "❌"
                        hint = tc.output[:400].replace("\n", " ") if tc.output else ""
                        section += f"- {status} `{tc.tool_name}` ({tc.execution_time_ms:.0f}ms) {hint}\n"
                    if len(result.tool_calls) > 15:
                        remaining_tcs = result.tool_calls[15:]
                        ok = sum(1 for t in remaining_tcs if t.success)
                        names = list(dict.fromkeys(t.tool_name for t in remaining_tcs))
                        section += f"- ... +{len(remaining_tcs)} more ({ok} ok): {', '.join(names[:6])}\n"

                # Agent output: use conclusion extraction for better evaluation
                section += "### Output\n"
                if result.output:
                    output_text = _extract_conclusion(result.output, _ORCHESTRATOR_AGENT_CAP_CHARS)
                    if len(result.output) > _ORCHESTRATOR_AGENT_CAP_CHARS:
                        output_text += (
                            f"\n[Full output: {len(result.output)} chars — "
                            f"use activity_search(keyword=\"{agent_name}\", turn={self._turn_number})]"
                        )
                    section += f"{output_text}\n"

                if result.error:
                    section += f"### Error\n{result.error}\n"

            parts.append(section)

        return "\n".join(parts)

    def get_thinking(self, role: str) -> "AgentThinking | None":
        """Get thinking for a specific agent role."""
        return self._thinkings.get(role)

    def get_own_thinking(self, agent_role: str) -> str:
        """Get agent's own thinking (for execution phase context)."""
        thinking = self._thinkings.get(agent_role)
        if not thinking:
            return ""
        return thinking.thinking

    def get_all_results_summary(self) -> str:
        """Get a brief summary of all results posted this turn.

        Used by orchestrator to see what happened in the current turn
        without waiting for full evaluation.
        """
        if not self._results:
            return ""
        parts: list[str] = []
        for role, result in self._results.items():
            status = "✓" if result.success else "✗"
            output_preview = ""
            if result.output:
                output_preview = result.output.strip()[-200:]
            parts.append(f"{status} {result.agent_name} ({role}): {output_preview}")
        return "\n".join(parts)
