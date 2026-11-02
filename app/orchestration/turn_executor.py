"""
Nexus - Turn Executor.

IMPROVEMENT: Let tasks run to completion - no artificial time limits.

Manages the execution of a single turn within the pipeline.
Coordinates: thinking phase → discussion phase → execution phase.
Emits detailed progress events for every micro-step.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

from app.config import get_settings
from app.orchestration.activity_logger import ActivityLogger
from app.orchestration.agent_worker import AgentWorker
from app.orchestration.context_ledger import ContextLedger
from app.orchestration.context_sharing import ContextSharing
from app.orchestration.cross_turn_registry import CrossTurnRegistry
from app.orchestration.data_vault import DataVault
from app.orchestration.goal_compliance import GoalComplianceChecker
from app.orchestration.models import (
    AgentAssignment,
    AgentResult,
    AgentThinking,
    ExecutionState,
    TurnPlan,
    TurnResult,
)
from app.orchestration.task_similarity import assignments_overlap

logger = structlog.get_logger(__name__)

# IMPROVEMENT: No artificial timeout on turn execution.
# Turns should run to completion. The only limit is the overall task
# max_turns in the orchestrator, not time-based limits on individual turns.
# We keep a very generous safety timeout only to prevent infinite hangs
# from bugs (not to limit legitimate work).
_TURN_SAFETY_TIMEOUT_SECONDS = 3600  # 1 hour — only for bug prevention, not flow control


# Roles whose job is to CONSUME/aggregate other agents' output — they structurally
# overlap with the whole team, so discussion must not be skipped when one is present
# (E-R9, tri-run audit 2026-07-02). Matched as whole roles (a substring guard covers
# planner-emitted variants like "report_writer" / "quality_critic" without tripping
# on unrelated roles). Kept as a small explicit set — no fuzzy keyword prose.
_SYNTHESIS_REVIEW_ROLES = (
    "writer", "critic", "security_reviewer", "reviewer", "report", "synthesizer",
    "editor", "qa", "auditor",
)


def _is_synthesis_role(role: str) -> bool:
    """Whether ``role`` aggregates/reviews peers' work (writer/critic/reviewer/...)."""
    r = (role or "").strip().lower()
    return any(token in r for token in _SYNTHESIS_REVIEW_ROLES)


def _skip_discussion_synthesis_text() -> str:
    """Synthesis text recorded when discussion is skipped (task 3c44a93f bug #6c).

    Discussion is skipped because the agents own DISTINCT sub-tasks (no overlap to
    cross-examine) — NOT because a consensus was reached. The old label
    ("Consensus detected — All agents are aligned") falsely implied agreement was
    debated and reached, misleading both a human reading the ledger and the
    decision layer. State the real reason instead.
    """
    return (
        "## Discussion Summary\n"
        "### Distinct sub-tasks — discussion skipped\n"
        "- Each agent owns a distinct sub-task with no overlapping work, so there "
        "is nothing to cross-examine — structured discussion was skipped to save "
        "execution capacity.\n"
        "- This is NOT a consensus on shared work; each agent proceeds directly to "
        "its own assignment."
    )


def _todo_context_slice(todo_text: str, max_chars: int) -> str:
    """Slice TODO.md for injection so the RECENT status survives, not the stale
    plan head (audit tri-model 2026-07-01, N1).

    TODO.md is rewritten plan-first with a ``## Current Status — Turn N`` block
    near the END; a raw ``[:max_chars]`` kept the plan head and dropped the newest
    completed-items / next-focus — the state the agent most needs, causing repeat
    work. When over budget, keep from the last Current-Status marker onward (its
    tail), prefixing as much plan head as still fits; fall back to keep_tail when
    the marker is absent. Returns the text unchanged when it already fits.
    """
    if len(todo_text) <= max_chars:
        return todo_text
    from app.orchestration.text_budget import keep_tail
    marker = todo_text.rfind("## Current Status")
    if marker > 0:
        tail = todo_text[marker:]
        if len(tail) <= max_chars:
            head_budget = max_chars - len(tail) - 1
            head = todo_text[:head_budget].rstrip() if head_budget > 200 else ""
            return (head + "\n" + tail) if head else tail
        return keep_tail(tail, max_chars)
    return keep_tail(todo_text, max_chars)


_CONTEXT_RECALL_TOOL_SCHEMA = {
    "name": "context_recall",
    "description": (
        "Query the task's structured memory for information from prior turns. "
        "Use when you need specific data from earlier work instead of guessing. "
        "Supports: 'turn N' for specific turn, role names (researcher/developer/etc), "
        "'key findings' for accumulated data, or free-text keyword search."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to recall. Examples: 'turn 3 researcher', 'key findings', 'API endpoints found'",
            }
        },
        "required": ["query"],
    },
}


class TurnExecutor:
    """Executes a single turn: thinking → discussion → execution.

    IMPROVEMENT: Let tasks run to completion - no artificial time limits.
    Emits detailed progress events for every micro-step so the user
    always knows what's happening.

    Coordinates multiple AgentWorkers, manages context sharing,
    and logs all activity.
    """

    def __init__(
        self,
        workers: dict[str, AgentWorker],
        context_sharing: ContextSharing,
        activity_logger: ActivityLogger,
        context_ledger: Optional[ContextLedger] = None,
        event_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable] = None,
        total_turns_estimate: int = 5,
        memory_context: str = "",
        task_id: str = "",
        goal_anchor: str = "",
        data_vault: Optional[DataVault] = None,
        cross_turn_registry: Optional[CrossTurnRegistry] = None,
        goal_compliance: Optional[GoalComplianceChecker] = None,
        complexity: str = "medium",
    ) -> None:
        self._workers = workers  # role → AgentWorker
        self._context = context_sharing
        self._activity = activity_logger
        self._ledger = context_ledger
        self._event_callback = event_callback
        self._cancel_check = cancel_check
        self._total_turns_estimate = total_turns_estimate
        self._memory_context = memory_context
        self._task_id = task_id
        self._goal_anchor = goal_anchor
        self._data_vault = data_vault
        self._cross_turn_registry = cross_turn_registry
        self._goal_compliance = goal_compliance
        self._complexity = complexity

        # Load ROE once for all turns. The Deliverable Contract is prepended so every
        # agent, on every turn, sees WHAT artifact must be produced and HOW it is verified
        # — this is what steers agents toward building the real thing, not just documents.
        self._roe_context = self._load_contract() + self._load_roe()
        # Resolve workspace path for agents
        self._workspace_path = self._resolve_workspace()

        # Propagate cancel_check and gap-fix subsystems to all workers
        for worker in self._workers.values():
            worker.set_cancel_check(cancel_check)
            if data_vault:
                worker._data_vault = data_vault
            if cross_turn_registry:
                worker._cross_turn_registry = cross_turn_registry

    def _load_roe(self) -> str:
        """Load ROE.md content (brief summary for agent context)."""
        if not self._task_id:
            return ""
        from app.core import task_paths
        roe_path = task_paths.context_file(self._task_id, "ROE.md")
        if roe_path.exists():
            try:
                content = roe_path.read_text(encoding="utf-8")
                # Return condensed version (skip markdown headers for brevity)
                return content[:8000]
            except Exception:
                pass
        return ""

    def _load_contract(self) -> str:
        """Load CONTRACT.md (deliverable acceptance standard) for agent context.

        Returned with a trailing separator so it can be prepended to the ROE context.
        Empty string when no contract exists (e.g. resume, or planner produced none).
        """
        if not self._task_id:
            return ""
        from app.core import task_paths
        contract_path = task_paths.context_file(self._task_id, "CONTRACT.md")
        if contract_path.exists():
            try:
                content = contract_path.read_text(encoding="utf-8")
                return content[:4000] + "\n\n"
            except Exception:
                pass
        return ""

    def _resolve_workspace(self) -> str:
        """Resolve the _work/ path string for agent prompts."""
        if not self._task_id:
            return ""
        from app.core import task_paths
        return str(task_paths.work_dir(self._task_id))

    async def execute_turn(
        self,
        plan: TurnPlan,
        state: ExecutionState,
    ) -> TurnResult:
        """Execute one complete turn.

        IMPROVEMENT: No artificial timeout. Detailed progress events
        for every micro-step (phase start, agent start, agent complete).

        Flow:
        1. Reset context board for this turn
        2. Thinking phase — all agents analyze their assignment
        3. Discussion phase — agents react to each other's thinking
        4. Execution phase — agents use tools to complete tasks
        5. Log everything to ACTIVITY.md
        6. Return TurnResult
        """
        self._context.reset_for_turn(plan.turn_number)

        # Set execution context so tool handlers can resolve workspace paths
        if self._task_id:
            from app.core.execution_context import current_task_id
            current_task_id.set(self._task_id)

        # Log turn start
        await self._activity.log_turn_start(plan.turn_number, plan)

        # IMPROVEMENT: Detailed progress event — turn starting
        await self._emit("step", {
            "step": plan.turn_number,
            "total_steps": self._total_turns_estimate,
            "icon": "🔄",
            "title": f"Turn {plan.turn_number}: {plan.objective[:50]}",
            "description": f"{len(plan.assignments)} agents, mode: {plan.execution_mode}",
            "status": "active",
        })

        # Check cancellation
        if self._is_cancelled():
            return TurnResult(turn_number=plan.turn_number)

        # CONTEXT MANAGEMENT: Ledger-first strategy.
        # Ledger is the primary structured context source (role-specific, queryable).
        # Flat summary ONLY used as fallback when ledger has no entries for this task.
        # This prevents duplicate context that confuses agents.
        # CONTEXT BUDGET: total previous_context must not exceed 12K chars.
        # This leaves ~48K for actual work within 60K compression threshold.
        # X-2 (arch review 2026-07-03): the two new high-priority sections (FILES ON
        # DISK + SAVED FACTS, ~5K) were starving the low-priority tail — goal-drift
        # correction (GAP-2), crashed-agent recovery, recent_facts — which never
        # reached the agent. Raise the pool so the tail survives (the real assembled
        # context was already ~21K; this 12K→16K only widens the pre-ledger portion).
        _PREVIOUS_CONTEXT_BUDGET = 16000
        _ctx_remaining = _PREVIOUS_CONTEXT_BUDGET
        previous_context = ""

        # Goal Anchor: ALWAYS first, NEVER compressed, NEVER truncated
        if self._goal_anchor:
            previous_context = self._goal_anchor + "\n\n"
            # Don't deduct from _ctx_remaining — goal anchor is sacred

        # R-2/R-4 (context-arch audit): the agent could only INFER "where am I" from
        # ledger labels — no explicit position/objective header. A one-line "YOU ARE
        # HERE" anchors turn number + this turn's objective + its done-signal, so the
        # agent always knows where it is and what THIS turn must achieve. Cheap, never
        # truncated (it's the map).
        _pos_bits = [f"Turn {plan.turn_number}"]
        if getattr(self, "_total_turns_estimate", 0):
            _pos_bits[0] += f" of ~{self._total_turns_estimate} planned"
        _here = f"## 📍 YOU ARE HERE — {' | '.join(_pos_bits)}\n"
        if (plan.objective or "").strip():
            _here += f"**This turn's objective:** {plan.objective.strip()}\n"
        if (plan.milestone or "").strip():
            _here += f"**Done when (this turn):** {plan.milestone.strip()}\n"
        previous_context += _here + "\n"

        # TODO.md — the single source of truth for task progress (priority 1).
        if self._task_id:
            from app.core import task_paths
            _todo_path = task_paths.context_file(self._task_id, "TODO.md")
            if _todo_path.exists():
                try:
                    _todo_cap = min(3000, _ctx_remaining)
                    _todo_content = _todo_context_slice(
                        _todo_path.read_text(encoding="utf-8"), _todo_cap
                    )
                    # APPEND (not assign): assigning here CLOBBERED the goal anchor
                    # set just above, so from turn 2 on (once TODO.md exists) the
                    # agent lost "USER ASKED" entirely — contradicting the "goal
                    # anchor ALWAYS first, never truncated" invariant (Bug A).
                    _todo_block = (
                        f"## TASK PROGRESS (DO NOT REPEAT COMPLETED ITEMS)\n"
                        f"{_todo_content}\n\n"
                    )
                    previous_context += _todo_block
                    _ctx_remaining -= len(_todo_block)
                except Exception:
                    pass

        # A-1/R-1 (context-arch audit 2026-07-03): the AGENT that does the work was
        # blind to what already exists on disk — only the orchestrator got the
        # workspace tree. So the agent re-produced files or skipped ones it thought
        # missing ("trôi vô định, không biết đã có gì"). Inject the _work/ inventory
        # (file names + sizes, empty-scaffold dirs flagged) so the agent KNOWS what
        # is already delivered before it acts. High priority — placed right after
        # TASK PROGRESS, ahead of softer context.
        if self._task_id and _ctx_remaining > 500:
            try:
                from app.orchestration.workspace_reader import WorkspaceReader
                _inv_cap = min(3000, _ctx_remaining)
                _inventory = WorkspaceReader(self._task_id).build_work_inventory(_inv_cap)
                if _inventory:
                    _inv_block = (
                        f"## FILES ALREADY ON DISK (build on these — do NOT recreate)\n"
                        f"{_inventory}\n\n"
                    )
                    previous_context += _inv_block
                    _ctx_remaining -= len(_inv_block)
            except Exception:
                pass

        # W-7/C-9 (context-arch audit): the vault holds full-fidelity records
        # (verdicts, exact numbers, error locations) but was pull-only — a weak model
        # never called retrieve, so this high-value memory slept. Auto-inject the
        # freshest few so authoritative facts (do-NOT-recompute) are always present.
        if self._data_vault is not None and _ctx_remaining > 500:
            try:
                _vault_cap = min(2000, _ctx_remaining)
                _vault_digest = self._data_vault.build_recent_digest(n=3, max_chars=_vault_cap)
                if _vault_digest:
                    previous_context += _vault_digest + "\n\n"
                    _ctx_remaining -= len(_vault_digest) + 2
            except Exception:
                pass

        if self._memory_context and _ctx_remaining > 1000:
            _mem_cap = min(2000, _ctx_remaining)
            _mem = self._memory_context[:_mem_cap]
            previous_context += f"## Memory Context\n{_mem}\n\n"
            _ctx_remaining -= len(_mem) + 30

        # GAP-5 FIX: Inject cross-turn action registry (prevent duplicate work)
        if self._cross_turn_registry and _ctx_remaining > 500:
            _registry_cap = min(2000, _ctx_remaining)
            _registry_ctx = self._cross_turn_registry.build_context_for_agent(
                agent_role="", max_chars=_registry_cap,
            )
            if _registry_ctx:
                previous_context += _registry_ctx + "\n\n"
                _ctx_remaining -= len(_registry_ctx) + 2

        # GAP-2 FIX: Inject goal drift corrections if any
        if self._goal_compliance and _ctx_remaining > 200:
            _drift_ctx = self._goal_compliance.corrective_context
            if _drift_ctx:
                _drift_cap = min(1500, _ctx_remaining)
                previous_context += _drift_ctx[:_drift_cap] + "\n\n"
                _ctx_remaining -= min(len(_drift_ctx), _drift_cap) + 2

        # Only use flat summary when ledger is empty (first turn)
        ledger_has_data = self._ledger and self._ledger.get_entry_count() > 0
        if not ledger_has_data and _ctx_remaining > 1000:
            flat_summary = state.get_previous_context_summary(max_chars=min(4000, _ctx_remaining))
            if flat_summary:
                previous_context += flat_summary
                _ctx_remaining -= len(flat_summary)

        # FIX B2: Build ledger context for thinking phase with scaled budget.
        # Without this, agents in thinking phase have NO turn history when ledger is active,
        # causing them to plan blindly and potentially redo completed work.
        # Budget scales with task complexity (inferred from turn count and agent count).
        _is_complex_task = len(plan.assignments) >= 3 or plan.turn_number >= 5
        _thinking_ledger_budget = 6000 if _is_complex_task else 3000
        thinking_ledger_hint = ""
        if ledger_has_data and self._ledger:
            thinking_ledger_hint = self._ledger.build_context_for_agent(
                agent_role="__thinking_phase__",
                current_turn=plan.turn_number,
                max_chars=_thinking_ledger_budget,
            )

        # FIX B4: Inject collected data from previous crashed agents.
        # Increased budget: 4 items × 1500 chars (was 2 × 800).
        _MAX_INJECT_COUNT = 3
        if _ctx_remaining > 500:
            available = [
                item for item in state.collected_data_cache
                if item.get("_inject_count", 0) < _MAX_INJECT_COUNT
            ]
            if available:
                collected_ctx = "\n## DATA ALREADY FETCHED (use directly, do NOT refetch):\n"
                _data_cap = min(6000, _ctx_remaining)
                for item in available[-4:]:
                    entry = f"- T{item['turn']} ({item['agent']}): {item['data_summary'][:1500]}\n"
                    if len(collected_ctx) + len(entry) > _data_cap:
                        break
                    collected_ctx += entry
                    item["_inject_count"] = item.get("_inject_count", 0) + 1
                previous_context += collected_ctx
                _ctx_remaining -= len(collected_ctx)

        # GAP-6 FIX: Eager inline facts from recent turns.
        # Memory Layers pipeline has latency (L1 every 5 turns + 90s L2 delay).
        # Extract key facts directly from recent turn results for immediate use.
        if state.turns_completed and _ctx_remaining > 500:
            _recent_facts: list[str] = []
            for tr in state.turns_completed[-3:]:
                for ar in tr.agent_results:
                    if ar.success and ar.output:
                        for line in ar.output.split("\n"):
                            line = line.strip()
                            if (
                                len(line) > 30
                                and (line.startswith(("-", "•", "*")) or ": " in line)
                                and not line.startswith(("I ", "Let ", "Now ", "I'll"))
                            ):
                                _recent_facts.append(line[:200])
                                if len(_recent_facts) >= 10:
                                    break
                        if len(_recent_facts) >= 10:
                            break
                if len(_recent_facts) >= 10:
                    break

            # X-4/SELF-2 (arch review): "Recent Facts" grep-scrapes prior-turn output
            # that the structured ledger (Your Prior Work / Teammates' Results) already
            # carries — the same number reached the agent 3-4×, wasting budget. Keep
            # only facts NOT already present in the context assembled above, so this is
            # a genuine SUPPLEMENT (covers the ledger-empty turn-1 edge) not a duplicate.
            if _recent_facts:
                _ctx_lower = previous_context.lower()
                _novel = [
                    f for f in _recent_facts
                    if f[:60].lower() not in _ctx_lower
                ]
                if _novel:
                    _facts_cap = min(2000, _ctx_remaining)
                    facts_section = "## Recent Facts (from prior turns, use directly):\n"
                    for fact in _novel[:8]:
                        entry = f"  {fact}\n"
                        if len(facts_section) + len(entry) > _facts_cap:
                            break
                        facts_section += entry
                    previous_context += facts_section
                    _ctx_remaining -= len(facts_section)

        # === Phase A: Thinking (parallel) ===
        # IMPROVEMENT: Emit phase-level progress
        await self._emit("micro_step", {
            "turn": plan.turn_number,
            "phase": "thinking",
            "description": f"Agents analyzing their assignments ({len(plan.assignments)} agents)",
        })
        try:
            thinkings = await asyncio.wait_for(
                self._thinking_phase(plan, previous_context, thinking_ledger_hint, _thinking_ledger_budget),
                timeout=300,  # 5 min safety timeout for thinking phase
            )
        except asyncio.TimeoutError:
            logger.warning("thinking_phase_timeout", turn=plan.turn_number)
            return TurnResult(
                turn_number=plan.turn_number,
                error=f"Turn {plan.turn_number} thinking phase timed out after 300s",
            )

        # Emit actual thinking content for frontend
        for thinking in thinkings:
            await self._emit("agent_thinking", {
                "agent_name": thinking.agent_name if hasattr(thinking, 'agent_name') else "",
                "agent_role": thinking.agent_role if hasattr(thinking, 'agent_role') else "",
                "action": "thinking_complete",
                "content": (thinking.thinking or "")[:2000],
                "turn": plan.turn_number,
            })

        if self._is_cancelled():
            return TurnResult(turn_number=plan.turn_number)

        # === Phase B: Discussion (parallel, quick) ===
        # IMPROVEMENT: Emit phase-level progress
        await self._emit("micro_step", {
            "turn": plan.turn_number,
            "phase": "discussion",
            "description": "Agents discussing and challenging each other's plans",
        })
        try:
            await asyncio.wait_for(
                self._discussion_phase(plan, thinkings),
                timeout=3000,  # 50 min safety timeout for discussion phase
            )
        except asyncio.TimeoutError:
            logger.warning("discussion_phase_timeout", turn=plan.turn_number)
            # Discussion is non-critical — continue to execution even if it times out

        if self._is_cancelled():
            return TurnResult(turn_number=plan.turn_number)

        # === Phase C: Execution (parallel or sequential) ===
        # IMPROVEMENT: Emit phase-level progress
        await self._emit("micro_step", {
            "turn": plan.turn_number,
            "phase": "execution",
            "description": f"Agents executing with tools ({plan.execution_mode} mode)",
        })
        try:
            results = await asyncio.wait_for(
                self._execution_phase(plan, previous_context),
                timeout=10800,  # 3 hour safety timeout (pentest tasks can run 100+ tools)
            )
        except asyncio.TimeoutError:
            logger.warning("execution_phase_timeout", turn=plan.turn_number)
            # Collect any partial results from context sharing (sequential agents
            # that completed before timeout are stored there)
            partial_results = []
            for assignment in plan.assignments:
                cached = self._context._results.get(assignment.agent_role)
                if cached:
                    partial_results.append(cached)
            if partial_results:
                logger.info(
                    "execution_timeout_partial_results_recovered",
                    turn=plan.turn_number,
                    recovered_agents=len(partial_results),
                )
                return TurnResult(
                    turn_number=plan.turn_number,
                    agent_results=partial_results,
                    error=f"Turn {plan.turn_number} execution timed out but {len(partial_results)} agents completed",
                )
            return TurnResult(
                turn_number=plan.turn_number,
                error=f"Turn {plan.turn_number} execution phase timed out after 72000s",
            )

        # IMPROVEMENT: Detailed completion event
        await self._emit("step", {
            "step": plan.turn_number,
            "total_steps": self._total_turns_estimate,
            "icon": "✅",
            "title": f"Turn {plan.turn_number} complete",
            "description": f"{len(results)} agents finished, {sum(len(r.tool_calls) for r in results)} total tool calls",
            "status": "done",
        })

        return TurnResult(
            turn_number=plan.turn_number,
            agent_results=results,
        )

    async def _thinking_phase(
        self,
        plan: TurnPlan,
        previous_context: str,
        thinking_ledger_hint: str = "",
        _thinking_ledger_budget: int = 3000,
    ) -> list[AgentThinking]:
        """All agents produce thinking in parallel."""
        thinkings: list[AgentThinking] = []

        async def _think_one(assignment: AgentAssignment) -> AgentThinking:
            worker = self._workers.get(assignment.agent_role)
            if not worker:
                return AgentThinking(
                    agent_name=assignment.agent_name,
                    agent_role=assignment.agent_role,
                    thinking="(No worker available for this role)",
                )
            shared = self._context.get_shared_context_with_budget(
                assignment.agent_role, max_chars=10000
            )
            # FIX B2: Inject role-specific ledger context into thinking phase.
            # Uses actual agent_role (not "__thinking_phase__") and scaled budget.
            thinking_ctx = previous_context
            if thinking_ledger_hint:
                role_hint = ""
                if self._ledger:
                    role_hint = self._ledger.build_context_for_agent(
                        agent_role=assignment.agent_role,
                        current_turn=plan.turn_number,
                        max_chars=_thinking_ledger_budget,
                    )
                hint = role_hint or thinking_ledger_hint
                thinking_ctx = f"{hint}\n\n{previous_context}"
            return await worker.think(assignment, shared, thinking_ctx, roe_context=self._roe_context)

        # Run all thinking in parallel
        tasks = [_think_one(a) for a in plan.assignments]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                thinking = AgentThinking(
                    agent_name=plan.assignments[i].agent_name,
                    agent_role=plan.assignments[i].agent_role,
                    thinking=f"(Thinking failed: {result})",
                )
            else:
                thinking = result

            thinkings.append(thinking)
            self._context.add_thinking(thinking.agent_role, thinking)
            await self._activity.log_agent_thinking(thinking.agent_name, thinking)

            # IMPROVEMENT: Per-agent progress event
            await self._emit("micro_step", {
                "turn": plan.turn_number,
                "phase": "thinking",
                "agent": thinking.agent_name,
                "description": f"{thinking.agent_name} finished thinking",
            })

        return thinkings

    async def _discussion_phase(
        self,
        plan: TurnPlan,
        thinkings: list[AgentThinking],
    ) -> None:
        """Structured multi-agent discussion: challenge, rebuttal, synthesis.

        Protocol (max 2 rounds):
          Round 1 — Each agent produces CHALLENGES (weaknesses in others' proposals)
                    and SUPPORTS (strengths they endorse).
          Round 2 — Any agent that received challenges gets a REBUTTAL opportunity
                    (one shot to defend/adjust their approach).
          Synthesis — Results are compiled into agreed approaches, unresolved
                      disagreements, and key insights for the execution phase.

        BUG-1 FIX: Discussion results (challenges/supports) are added back
        to ContextSharing so the execution phase can see peer feedback.

        OPTIMIZATION-M1: If all agents agree on all topics (check by comparing
        thinking outputs for shared conclusions), skip discussion entirely.
        Discussion consumes N+1 agent slots but produces no value when aligned.
        """
        if len(thinkings) <= 1:
            return  # No discussion needed with single agent

        # Skip discussion entirely for simple tasks: adversarial cross-examination earns
        # its cost on complex/multi-faceted work, but on a simple task it just burns an
        # extra LLM call per agent for marginal benefit. The execution phase still runs.
        if self._complexity == "simple":
            logger.info("discussion_skipped_simple_task", agents=len(thinkings))
            await self._emit("micro_step", {
                "turn": plan.turn_number,
                "phase": "discussion",
                "description": "Simple task — discussion skipped (optimization)",
            })
            return

        # Skip discussion if most agents crashed (their thinking is just error text)
        surviving_thinkings = [
            t for t in thinkings
            if t.thinking and not t.thinking.startswith("(Thinking failed:")
            and not t.thinking.startswith("(No worker available")
        ]
        if len(surviving_thinkings) <= 1:
            logger.info("discussion_skipped_insufficient_surviving_agents",
                       total=len(thinkings), surviving=len(surviving_thinkings))
            return

        # OPTIMIZATION-M1 / audit §5 P4: structural consensus check. Discussion earns
        # its per-agent LLM cost only when ≥2 agents target the SAME work (their plans
        # can conflict and cross-examination adds value). When every agent owns a
        # DISTINCT sub-task there is nothing to debate → skip. Reads assignment overlap,
        # not disagreement keywords (the old brittle M2 heuristic).
        if self._check_consensus_skip(plan.assignments):
            summary = _skip_discussion_synthesis_text()
            self._context.add_thinking("_discussion_synthesis", AgentThinking(
                agent_name="[SYSTEM] Discussion Synthesis",
                agent_role="_discussion_synthesis",
                thinking=f"[Distinct sub-tasks — discussion skipped]\n{summary}",
            ))
            await self._emit("micro_step", {
                "turn": plan.turn_number,
                "phase": "discussion",
                "description": "Distinct sub-tasks — discussion skipped (optimization)",
            })
            return

        # === Round 1: Challenge + Support ===
        async def _discuss_one(assignment: AgentAssignment) -> Optional[AgentThinking]:
            worker = self._workers.get(assignment.agent_role)
            if not worker:
                return None
            return await worker.discuss(thinkings, assignment)

        tasks = [_discuss_one(a) for a in plan.assignments]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        discussion_results: list[AgentThinking] = []
        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            discussion_results.append(result)
            await self._activity.log_agent_discussion(result.agent_name, result)

            await self._emit("micro_step", {
                "title": f"💬 {result.agent_name} discussed",
                "detail": f"Challenges: {len(result.challenges)}, Supports: {len(result.supports)}",
            })

        # Emit discussion content for frontend (formatted as readable text)
        for disc_result in discussion_results:
            content_parts = []
            if hasattr(disc_result, 'challenges') and disc_result.challenges:
                content_parts.append("Challenges:\n" + "\n".join(f"  ⚡ {c[:200]}" for c in disc_result.challenges[:3]))
            if hasattr(disc_result, 'supports') and disc_result.supports:
                content_parts.append("Supports:\n" + "\n".join(f"  ✅ {s[:200]}" for s in disc_result.supports[:3]))
            if content_parts:
                await self._emit("agent_thinking", {
                    "agent_name": getattr(disc_result, 'agent_name', ''),
                    "agent_role": getattr(disc_result, 'agent_role', ''),
                    "action": "discussion",
                    "content": "\n".join(content_parts),
                    "turn": plan.turn_number,
                    "challenges": getattr(disc_result, 'challenges', [])[:10],
                    "supports": getattr(disc_result, 'supports', [])[:10],
                    "target_agents": [
                        t.agent_name for t in thinkings
                        if t.agent_name != getattr(disc_result, 'agent_name', '')
                    ],
                })

        # === Round 2: Rebuttal ===
        # Identify agents that received challenges and give them one rebuttal round.
        challenges_by_target = self._collect_challenges_by_target(
            discussion_results, thinkings
        )
        rebuttal_results = await self._rebuttal_round(
            plan, thinkings, challenges_by_target
        )

        # === BUG-1 FIX: Merge all discussion feedback into ContextSharing ===
        for result in discussion_results:
            existing = self._context.get_thinking(result.agent_role)
            if existing:
                if result.challenges:
                    existing.challenges = list(existing.challenges) + result.challenges
                if result.supports:
                    existing.supports = list(existing.supports) + result.supports

        for rebuttal in rebuttal_results:
            existing = self._context.get_thinking(rebuttal.agent_role)
            if existing:
                # Rebuttals are stored as supports (defending own position)
                if rebuttal.supports:
                    existing.supports = list(existing.supports) + rebuttal.supports

        # === Synthesis: produce structured summary for execution phase ===
        summary = self._synthesize_discussion(
            thinkings, discussion_results, rebuttal_results
        )
        # Publish as a synthetic entry visible to all agents
        # Prefixed with [SYSTEM] to make clear this is NOT a peer agent
        self._context.add_thinking("_discussion_synthesis", AgentThinking(
            agent_name="[SYSTEM] Discussion Synthesis",
            agent_role="_discussion_synthesis",
            thinking=f"[This is an automated summary of the team discussion, not an agent.]\n{summary}",
        ))

        await self._emit("micro_step", {
            "turn": plan.turn_number,
            "phase": "discussion",
            "description": "Discussion synthesis complete",
        })

    def _collect_challenges_by_target(
        self,
        discussion_results: list[AgentThinking],
        thinkings: list[AgentThinking],
    ) -> dict[str, list[str]]:
        """Map each challenged agent_role to the challenges directed at them.

        Heuristic: if a challenge mentions an agent's name or role, it targets
        that agent. If no specific target is identified, it targets all others.
        """
        challenges_by_target: dict[str, list[str]] = {}

        for disc in discussion_results:
            if not disc.challenges:
                continue
            for challenge in disc.challenges:
                challenge_lower = challenge.lower()
                matched = False
                for t in thinkings:
                    if t.agent_role == disc.agent_role:
                        continue  # Skip self
                    # Check if challenge mentions this agent by name or role
                    if (
                        t.agent_name.lower() in challenge_lower
                        or t.agent_role.lower() in challenge_lower
                    ):
                        challenges_by_target.setdefault(t.agent_role, []).append(
                            f"[from {disc.agent_name}] {challenge}"
                        )
                        matched = True
                if not matched:
                    # Untargeted challenge — assign to all others
                    for t in thinkings:
                        if t.agent_role != disc.agent_role:
                            challenges_by_target.setdefault(t.agent_role, []).append(
                                f"[from {disc.agent_name}] {challenge}"
                            )

        return challenges_by_target

    async def _rebuttal_round(
        self,
        plan: TurnPlan,
        thinkings: list[AgentThinking],
        challenges_by_target: dict[str, list[str]],
    ) -> list[AgentThinking]:
        """Give challenged agents one rebuttal opportunity.

        Only agents that received challenges participate. Each gets a single
        LLM call to defend or adjust their approach.
        """
        if not challenges_by_target:
            return []

        async def _rebut_one(
            assignment: AgentAssignment, challenges: list[str]
        ) -> Optional[AgentThinking]:
            worker = self._workers.get(assignment.agent_role)
            if not worker:
                return None

            # Find this agent's original thinking
            own_thinking = ""
            for t in thinkings:
                if t.agent_role == assignment.agent_role:
                    own_thinking = t.thinking
                    break

            challenges_text = "\n".join(f"- {c}" for c in challenges[:5])

            messages = [
                {"role": "system", "content": (
                    f"You are {worker.agent_name} ({worker.agent_role}). "
                    f"Your expertise: {', '.join(worker.expertise)}.\n"
                    f"Your original plan: {own_thinking[:1500]}\n\n"
                    "Other agents have CHALLENGED aspects of your plan (below). "
                    "You have ONE rebuttal. Either:\n"
                    "1. DEFEND your approach with reasoning, OR\n"
                    "2. ADJUST your plan based on valid criticism.\n\n"
                    "Respond with JSON: {\"rebuttals\": [\"...\"], \"adjustments\": [\"...\"]}\n"
                    "rebuttals = points you defend. adjustments = changes you accept."
                )},
                {"role": "user", "content": f"Challenges to your plan:\n{challenges_text}"},
            ]

            try:
                from app.orchestration.json_parse import parse_json_response
                result = await worker._llm.chat(
                    messages=messages, temperature=0.3, max_tokens=1024
                )
                content = result.get("content", "")
                parsed = parse_json_response(content)
                rebuttals = []
                adjustments = []
                if parsed:
                    rebuttals = parsed.get("rebuttals", [])
                    adjustments = parsed.get("adjustments", [])

                # Pack rebuttals as supports (defending position) and
                # adjustments as challenges to self (accepted criticism)
                supports = [f"[REBUTTAL] {r}" for r in rebuttals[:3] if r]
                own_challenges = [f"[ADJUSTED] {a}" for a in adjustments[:3] if a]

                if supports or own_challenges:
                    await self._emit("agent_thinking", {
                        "agent_name": worker.agent_name,
                        "agent_role": worker.agent_role,
                        "action": "rebuttal",
                        "content": (
                            "Rebuttals: " + "; ".join(str(r)[:150] for r in rebuttals[:3])
                            + "\nAdjustments: " + "; ".join(str(a)[:150] for a in adjustments[:3])
                        ),
                        "turn": plan.turn_number,
                    })

                return AgentThinking(
                    agent_name=worker.agent_name,
                    agent_role=worker.agent_role,
                    supports=supports,
                    challenges=own_challenges,
                )
            except Exception as e:
                logger.debug("rebuttal_failed", agent=assignment.agent_role, error=str(e))
                return None

        # Run rebuttals in parallel for challenged agents
        rebuttal_tasks = []
        for assignment in plan.assignments:
            if assignment.agent_role in challenges_by_target:
                rebuttal_tasks.append(
                    _rebut_one(assignment, challenges_by_target[assignment.agent_role])
                )

        if not rebuttal_tasks:
            return []

        results = await asyncio.gather(*rebuttal_tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, AgentThinking)]

    def _check_consensus_skip(self, assignments: list[AgentAssignment]) -> bool:
        """Decide whether to skip the discussion phase, from a STRUCTURAL signal.

        FIX E1 / audit §5 P4: Discussion (challenge/rebuttal) only earns its
        per-agent LLM cost when two or more agents work toward the SAME target —
        that is when their plans can actually conflict and cross-examination adds
        value. When every agent owns a DISTINCT sub-task, there is nothing to
        debate, so skip.

        Previously this sniffed the thinking text for disagreement keywords
        ("disagree", "concern", ...) — brittle wording-dependent matching (audit
        M2). The replacement reads only assignment overlap (reused jaccard
        similarity), independent of how agents phrase their reasoning.

        E-R9 (tri-run audit 2026-07-02): a SYNTHESIS/REVIEW role (writer, critic,
        security_reviewer, ...) CONSUMES every peer's work, so it structurally
        overlaps with all of them even when the task descriptions look disjoint.
        On the Opus run a writer fabricated "SQL Injection CVSS 9.8" on the exact
        turn discussion was skipped as "distinct sub-tasks" — that is the turn
        cross-examination is MOST valuable. Keep discussion whenever such a role is
        on the turn (alongside ≥1 other agent), regardless of jaccard overlap.

        Returns True to SKIP discussion (no overlapping work), False to KEEP it.
        """
        if len(assignments) >= 2 and any(
            _is_synthesis_role(a.agent_role) for a in assignments
        ):
            return False
        return not assignments_overlap(assignments)

    def _synthesize_discussion(
        self,
        thinkings: list[AgentThinking],
        discussion_results: list[AgentThinking],
        rebuttal_results: list[AgentThinking],
    ) -> str:
        """Synthesize discussion into structured summary for execution phase.

        Produces:
        - Agreed approaches (supports that multiple agents endorse)
        - Unresolved disagreements (challenges without accepted adjustments)
        - Key insights from debate
        """
        # Collect all supports across agents
        all_supports: list[str] = []
        for d in discussion_results:
            all_supports.extend(d.supports)
        for r in rebuttal_results:
            all_supports.extend(s for s in r.supports if not s.startswith("[REBUTTAL]"))

        # Collect unresolved challenges (challenges not addressed by adjustments)
        adjusted_topics: set[str] = set()
        for r in rebuttal_results:
            for c in r.challenges:  # These are [ADJUSTED] entries
                adjusted_topics.add(c.replace("[ADJUSTED] ", "").lower()[:50])

        unresolved: list[str] = []
        for d in discussion_results:
            for c in d.challenges:
                # Check if this challenge was addressed by an adjustment
                c_lower = c.lower()[:50]
                if not any(adj in c_lower or c_lower in adj for adj in adjusted_topics):
                    unresolved.append(c)

        # Rebuttals that defend position = key insights
        key_insights: list[str] = []
        for r in rebuttal_results:
            for s in r.supports:
                if s.startswith("[REBUTTAL]"):
                    key_insights.append(s.replace("[REBUTTAL] ", ""))

        # Build summary text
        parts: list[str] = ["## Discussion Summary"]

        if all_supports:
            parts.append("### Agreed Approaches")
            for s in all_supports[:5]:
                parts.append(f"- {s[:200]}")

        if unresolved:
            parts.append("### Unresolved Disagreements")
            for u in unresolved[:5]:
                parts.append(f"- {u[:200]}")

        if key_insights:
            parts.append("### Key Insights from Debate")
            for k in key_insights[:5]:
                parts.append(f"- {k[:200]}")

        if not all_supports and not unresolved and not key_insights:
            parts.append("No significant disagreements — agents are aligned.")

        # Extract availability/reachability risks and convert to execution constraints
        _availability_keywords = (
            "unavailable", "unreachable", "not resolve", "503", "502",
            "connection refused", "down", "not running", "service unavailable",
            "may not be accessible", "might not resolve",
        )
        all_challenges_text = " ".join(
            c.lower() for d in discussion_results for c in d.challenges
        )
        if any(kw in all_challenges_text for kw in _availability_keywords):
            parts.append("### HARD RULE: Target Availability — MAX 3 RETRIES")
            parts.append(
                "- Team discussion identified that the target may be unreachable.\n"
                "- **RULE**: If you receive the SAME error (503, connection refused, timeout) "
                "3 times in a row → STOP IMMEDIATELY. Do NOT sleep, wait, or poll.\n"
                "- **DO**: Document what you confirmed (IP, headers, cert info) and conclude.\n"
                "- **DO NOT**: Use sleep/wait hoping the service comes up. It won't.\n"
                "- **DO NOT**: Try alternative ports, protocols, or IPs beyond initial 3 attempts.\n"
                "- A clear report of 'target unreachable + evidence' is the CORRECT output."
            )

        return "\n".join(parts)

    async def _execution_phase(
        self,
        plan: TurnPlan,
        previous_context: str,
    ) -> list[AgentResult]:
        """Agents execute their assignments with tools."""
        results: list[AgentResult] = []

        async def _execute_one(assignment: AgentAssignment) -> AgentResult:
            # IMPROVEMENT: Per-agent execution start event
            await self._emit("micro_step", {
                "turn": plan.turn_number,
                "phase": "execution",
                "agent": assignment.agent_name,
                "description": f"{assignment.agent_name} starting execution",
            })

            worker = self._workers.get(assignment.agent_role)
            if not worker:
                return AgentResult(
                    agent_name=assignment.agent_name,
                    agent_role=assignment.agent_role,
                    output="(No worker available)",
                    success=False,
                    error="Worker not found for role",
                )

            shared = self._context.get_shared_context_with_budget(
                assignment.agent_role, max_chars=6000
            )
            own_thinking = self._context.get_own_thinking(assignment.agent_role)
            synthesis = self._context.get_thinking("_discussion_synthesis")
            if synthesis and synthesis.thinking:
                own_thinking = (
                    f"{own_thinking}\n\n"
                    f"## BINDING CONSTRAINTS FROM TEAM DISCUSSION\n"
                    f"The following were agreed during team discussion. "
                    f"You MUST follow these — violation will result in force-stop:\n\n"
                    f"{synthesis.thinking[:4000]}"
                )

            # Inject ledger-based context per agent (role-specific, structured)
            # Cap at 5K (was 8K) — combined with previous_context (12K) must stay under budget
            agent_previous = previous_context
            if self._ledger:
                ledger_ctx = self._ledger.build_context_for_agent(
                    agent_role=assignment.agent_role,
                    current_turn=plan.turn_number,
                    max_chars=5000,
                )
                if ledger_ctx:
                    agent_previous = f"{ledger_ctx}\n\n{previous_context}"

                # Build compact "ALREADY DONE" list from ledger.
                # CONSTRAINTS: max 500 chars total to avoid context bloat.
                # Only include items with REAL findings (not TLS noise, not generic).
                completed_tools = self._ledger.query(
                    since_turn=1,
                    until_turn=plan.turn_number - 1,
                    success_only=True,
                    limit=20,
                )
                if completed_tools:
                    _NOISE = ("TLS", "handshake", "cipher", "CAfile", "Trying ")
                    done_actions = set()
                    for entry in completed_tools:
                        if entry.key_data:
                            for kd in entry.key_data[:2]:
                                if len(kd) > 20 and not any(n in kd for n in _NOISE):
                                    done_actions.add(kd[:80])
                    if done_actions:
                        # Hard cap: max 8 items. R-5 (context-arch audit): TRIM to
                        # 500 chars instead of dropping the whole block when it's
                        # longer — an all-or-nothing >500 cliff made the agent lose
                        # its entire "already confirmed" list and re-test done work.
                        items = list(done_actions)[:8]
                        done_list = "\n".join(f"  - {a}" for a in items)
                        if len(done_list) > 500:
                            from app.orchestration.text_budget import smart_truncate
                            done_list = smart_truncate(done_list, 500)
                        agent_previous = (
                            f"## ALREADY CONFIRMED (use directly, do NOT re-test):\n"
                            f"{done_list}\n\n{agent_previous}"
                        )

            # Deconfliction: show what other parallel agents are doing
            if plan.execution_mode == "parallel":
                deconfliction = self._context.get_active_actions_summary(
                    exclude_role=assignment.agent_role
                )
                if deconfliction:
                    shared = f"{deconfliction}\n\n{shared}"

            # Wire context_sharing for deconfliction during execution
            worker._context_sharing = self._context

            # GAP-1: Set current turn for DataVault store operations
            worker._current_turn = plan.turn_number

            # Attach ledger + context_recall tool for on-demand queries
            worker._context_ledger = self._ledger
            if self._ledger and _CONTEXT_RECALL_TOOL_SCHEMA["name"] not in [
                t.get("name") for t in (worker._tool_schemas or [])
            ]:
                worker._tool_schemas = list(worker._tool_schemas or []) + [_CONTEXT_RECALL_TOOL_SCHEMA]

            try:
                # Tag this agent's log lines (audit C4). Scoped so it reverts
                # cleanly in sequential mode and stays isolated per-Task in
                # parallel mode (gather copies the contextvars).
                from app.core import trace_context
                with trace_context.agent_scope(assignment.agent_role, turn=plan.turn_number):
                    result = await worker.execute(
                        assignment=assignment,
                        shared_context=shared,
                        own_thinking=own_thinking,
                        previous_turns=agent_previous,
                        current_turn=plan.turn_number,
                        roe_context=self._roe_context,
                        workspace_path=self._workspace_path,
                    )
            except Exception as e:
                # Agent crashed mid-execution — recover partial work from worker
                logger.warning(
                    "agent_execution_crashed_recovering",
                    agent=assignment.agent_name,
                    error=str(e)[:200],
                )
                partial_tool_calls = getattr(worker, '_recent_tool_calls_raw', [])
                partial_output = f"(Agent crashed after {len(partial_tool_calls)} tool calls: {e})"
                result = AgentResult(
                    agent_name=assignment.agent_name,
                    agent_role=assignment.agent_role,
                    output=partial_output,
                    success=False,
                    error=str(e)[:500],
                    tool_calls=partial_tool_calls[-50:] if partial_tool_calls else [],
                )

            # Share result for sequential dependencies
            self._context.add_result(assignment.agent_role, result)

            # Write interim ledger entry immediately so subsequent agents
            # (in the same turn, sequential mode) can query structured context
            # instead of waiting for the entire turn to complete.
            if self._ledger:
                from app.orchestration.context_ledger import LedgerEntry
                tools_used = list(dict.fromkeys(
                    tc.tool_name for tc in result.tool_calls if tc.tool_name
                ))[:10]
                from app.orchestration.ledger_extract import (
                    extract_did_what,
                    extract_key_data,
                    extract_still_missing,
                )
                key_data = extract_key_data(result.output or "", result.tool_calls)
                # W-1/W-2: route the agent's STILL-MISSING handoff into blockers so
                # "còn nợ / cần làm gì tiếp" survives to the next turn (previously only
                # a crash error string reached blockers; the handoff was dropped).
                _still_missing = extract_still_missing(result.output or "")
                _err = result.error[:150] if result.error else ""
                _blockers = "; ".join(p for p in (_err, _still_missing) if p)
                # Summary-aware extraction (word-boundary, picks up ## Summary)
                # instead of a raw output tail that always began mid-word and
                # dropped the agent's own conclusion. The end-of-turn write
                # upgrades this draft with orchestrator notes when available.
                self._ledger.append(LedgerEntry(
                    turn=plan.turn_number,
                    agent_role=result.agent_role,
                    agent_name=result.agent_name,
                    did_what=extract_did_what(result.output or ""),
                    key_data=key_data,
                    tools_used=tools_used,
                    success=result.success,
                    produced_deliverable=getattr(result, "produced_deliverable", False),
                    output_chars=len(result.output) if result.output else 0,
                    blockers=_blockers,
                ))

            # IMPROVEMENT: Per-agent execution complete event
            await self._emit("micro_step", {
                "turn": plan.turn_number,
                "phase": "execution",
                "agent": assignment.agent_name,
                "description": f"{assignment.agent_name} finished ({len(result.tool_calls)} tools used)",
            })

            return result

        async def _run_sequential_batch(batch: list[AgentAssignment]) -> None:
            """Execute a batch one by one in dependency order, logging each."""
            for assignment in _order_by_dependencies(batch):
                if self._is_cancelled():
                    break
                result = await _execute_one(assignment)
                results.append(result)
                await self._activity.log_agent_execution(
                    result.agent_name,
                    result.agent_role,
                    result.tool_calls,
                    result.output,
                )

        async def _run_parallel_batch(batch: list[AgentAssignment]) -> None:
            """Execute a batch concurrently — each Task gets its own contextvars
            copy (asyncio.gather wraps each coroutine in a Task which copies
            context, so current_agent_name.set() is isolated per-Task). Concurrency
            is bounded to avoid overwhelming the provider."""
            _settings = get_settings()
            max_parallel = getattr(_settings, 'max_parallel_llm_agents', 3)
            sem = asyncio.Semaphore(max_parallel)

            async def _throttled_execute(assignment: AgentAssignment) -> AgentResult:
                async with sem:
                    return await _execute_one(assignment)

            coros = [_throttled_execute(a) for a in batch]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)
            for i, r in enumerate(batch_results):
                if isinstance(r, Exception):
                    agent_result = AgentResult(
                        agent_name=batch[i].agent_name,
                        agent_role=batch[i].agent_role,
                        output="",
                        success=False,
                        error=str(r),
                    )
                else:
                    agent_result = r
                results.append(agent_result)
                await self._activity.log_agent_execution(
                    agent_result.agent_name,
                    agent_result.agent_role,
                    agent_result.tool_calls,
                    agent_result.output,
                )

        # Auto-downgrade to sequential if agents would conflict.
        # Parallel agents cannot see each other's tool results within the same turn,
        # and if they write to the same workspace they will produce conflicts.
        if plan.execution_mode == "sequential":
            await _run_sequential_batch(plan.assignments)
        elif len(plan.assignments) <= 1:
            await _run_parallel_batch(plan.assignments)
        else:
            downgrade_reason = _should_downgrade_to_sequential(plan.assignments)
            if not downgrade_reason:
                await _run_parallel_batch(plan.assignments)
            else:
                # P2: instead of serializing the WHOLE turn, run the read-only agents
                # (which triggered no conflict) concurrently first, then the
                # conflicting writers/dependents sequentially so they still see the
                # readers' ledger entries. Falls back to fully-sequential when no
                # agent is parallel-safe.
                parallel_batch, sequential_batch = partition_assignments(plan.assignments)
                logger.info(
                    "parallel_downgraded_to_sequential",
                    turn=plan.turn_number,
                    reason=downgrade_reason,
                    parallel_safe=[a.agent_role for a in parallel_batch],
                    sequential=[a.agent_role for a in sequential_batch],
                )
                if parallel_batch:
                    await _run_parallel_batch(parallel_batch)
                if sequential_batch and not self._is_cancelled():
                    await _run_sequential_batch(sequential_batch)

        return results

    def _is_cancelled(self) -> bool:
        """Check if execution should be cancelled."""
        if self._cancel_check is None:
            return False
        return self._cancel_check()

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event via callback."""
        if self._event_callback is None:
            return
        try:
            await self._event_callback({"event_type": event_type, **data})
        except Exception:
            pass


def _order_by_dependencies(assignments: list[AgentAssignment]) -> list[AgentAssignment]:
    """Topological sort of assignments by dependencies.

    Simple implementation: assignments with no dependencies first,
    then those that depend on already-ordered assignments.
    Falls back to original order if dependencies can't be resolved.
    """
    if not assignments:
        return []

    ordered: list[AgentAssignment] = []
    remaining = list(assignments)
    resolved_roles: set[str] = set()
    max_iterations = len(assignments) + 1

    for _ in range(max_iterations):
        if not remaining:
            break

        # Find assignments whose dependencies are all resolved
        ready = [
            a for a in remaining
            if all(dep in resolved_roles for dep in a.dependencies)
        ]

        if not ready:
            # Circular dependency or unresolvable — just add remaining
            ordered.extend(remaining)
            break

        for a in ready:
            ordered.append(a)
            resolved_roles.add(a.agent_role)
            remaining.remove(a)

    return ordered


# Tools that mutate shared workspace state — two agents using these concurrently
# can clobber each other, so a turn with >1 writer must run sequentially.
_WRITE_TOOLS = {
    "file_write", "code_write", "save_to_workspace", "write_file",
    "create_file", "edit_file", "task_doc_write", "task_doc_append",
}
# Read-side tools whose results become order-dependent ONLY in the presence of a
# concurrent writer. Used by the full-parallel safety check, not by the partition
# (pure readers of an unchanging workspace are safe to run concurrently).
_SHARED_STATE_TOOLS = {
    "fetch_url", "read_file", "task_doc_read",
}


def _is_parallel_safe(assignment: AgentAssignment, peer_roles: set[str]) -> bool:
    """True if `assignment` can run concurrently with arbitrary peers.

    Safe ⇔ it neither writes shared state, nor has an explicit dependency, nor
    implicitly depends on a peer by naming that peer's role in its description.
    A pure reader meeting all three is order-independent: with no concurrent
    writer there is no read-after-write hazard.
    """
    if set(assignment.tools_hint) & _WRITE_TOOLS:
        return False
    if assignment.dependencies:
        return False
    desc_lower = assignment.task_description.lower()
    for role in peer_roles - {assignment.agent_role.lower()}:
        if role in desc_lower:
            return False
    return True


def partition_assignments(
    assignments: list[AgentAssignment],
) -> tuple[list[AgentAssignment], list[AgentAssignment]]:
    """Split a (sequential-downgraded) turn into a parallel-safe batch + the rest.

    Audit §5 P2: when a turn is downgraded to sequential because ≥2 agents write,
    the read-only agents that triggered no conflict need not be serialized too.
    Returns (parallel, sequential): the parallel batch runs concurrently first,
    then the sequential batch runs in dependency order (so writers see the
    readers' ledger entries). Input order is preserved within each batch.
    """
    peer_roles = {a.agent_role.lower() for a in assignments}
    parallel: list[AgentAssignment] = []
    sequential: list[AgentAssignment] = []
    for a in assignments:
        if _is_parallel_safe(a, peer_roles):
            parallel.append(a)
        else:
            sequential.append(a)
    return parallel, sequential


def _should_downgrade_to_sequential(assignments: list[AgentAssignment]) -> str:
    """Determine if parallel assignments should be downgraded to sequential.

    Returns empty string if parallel is safe, otherwise returns the reason.

    Sequential is REQUIRED when:
    1. Any assignment has explicit dependencies
    2. Multiple agents use write tools (workspace conflict)
    3. Agents reference each other's roles in task descriptions (implicit dependency)
    4. Agents share state-mutating tools (task_doc_write, save_to_workspace, etc.)
    """
    # Rule 1: explicit dependencies
    if any(a.dependencies for a in assignments):
        return "assignments have inter-agent dependencies"

    # Rule 2+4: shared write/mutation tools
    writers = []
    for a in assignments:
        hint_set = set(a.tools_hint)
        if hint_set & _WRITE_TOOLS:
            writers.append(a.agent_role)
    if len(writers) > 1:
        return f"multiple agents write to workspace: {', '.join(writers[:3])}"

    # Rule 3: implicit reference (one task mentions another agent's role)
    roles = {a.agent_role.lower() for a in assignments}
    for a in assignments:
        desc_lower = a.task_description.lower()
        other_roles = roles - {a.agent_role.lower()}
        for role in other_roles:
            if role in desc_lower:
                return f"{a.agent_role} references {role} in task description"

    # Rule 4: shared state-reading tools that produce order-dependent results
    all_hints = [set(a.tools_hint) for a in assignments]
    for i in range(len(all_hints)):
        for j in range(i + 1, len(all_hints)):
            shared = all_hints[i] & all_hints[j] & _SHARED_STATE_TOOLS
            if shared:
                return (
                    f"{assignments[i].agent_role} and {assignments[j].agent_role} "
                    f"share state tools: {', '.join(list(shared)[:3])}"
                )

    return ""
