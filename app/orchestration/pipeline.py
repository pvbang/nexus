"""
Nexus - Unified Execution Pipeline.

IMPROVEMENT: Every task deserves full treatment - consistency over shortcuts.

Single entry point for the entire turn-based orchestration system.
Replaces the fragmented debate/swarm/routing pipeline.

Flow: User Query → Memory → Orchestrator Plan → Turn Loop → Report → Answer
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Optional
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background_metrics import get_background_metrics
from app.core.smart_context import SmartContextAggregator
from app.orchestration.activity_logger import ActivityLogger
from app.orchestration.adaptive_concurrency import get_concurrency_limiter, reset_concurrency_limiter
from app.orchestration.agent_worker import AgentWorker
from app.orchestration.context_index import ContextIndex
from app.orchestration.context_ledger import ContextLedger
from app.orchestration.context_sharing import ContextSharing
from app.orchestration.cross_turn_registry import CrossTurnRegistry
from app.orchestration.data_vault import DataVault
from app.orchestration.evolution_agent import EvolutionAgent, EvolutionInsight
from app.orchestration.final_report import FinalReportGenerator
from app.orchestration.goal_compliance import GoalComplianceChecker, GoalProfile
from app.orchestration.ledger_verifier import LedgerVerifier
from app.orchestration.models import EvaluationResult, ExecutionState, TurnPlan, TurnResult
from app.orchestration.nexus_orchestrator import NexusOrchestrator, _MAX_TURNS, _WORKSPACE_BASE
from app.orchestration.prompts import build_final_summary_prompt
from app.orchestration.turn_executor import TurnExecutor
from app.orchestration.workspace_snapshot import WorkspaceSnapshot

logger = structlog.get_logger(__name__)


# Terminal reasons that mean the loop STOPPED without genuinely completing the
# Definition of Done — a HALT, not a success. G2-7 (tri-run audit round-2): the
# gasifier run burned 267% of its token budget → is_complete=True with reason
# ``token_budget_exhausted``, but DECISIONS.md rendered "Decision: complete", making
# a halt-at-13%-with-5/8-missing read as a 100% success. These render as
# "halted (<reason>)". ``lawful_refusal`` is NOT here — a correct safety refusal IS
# the Definition of Done for its branch, so it legitimately reads "complete".
_HALT_TERMINAL_REASONS = frozenset({
    "token_budget_exhausted",
    "evolution_terminate",
    "barren_no_production",
    "stagnation",
    "eval_blind",
    "loop_exhausted",
    "semantic_loop",
    "max_turns",
})


def format_decision_label(is_complete: bool, terminated_reason: str | None) -> str:
    """Render the DECISIONS.md 'Decision' label from completion + terminal reason.

    A genuine completion (or a lawful refusal, which is DoD for its branch) reads
    "complete". A completion forced by a HALT ceiling (budget/stagnation/barren/
    evolution/max-turns) reads "halted (<reason>)" so an operator is never told a
    stalled-at-N% run "completed" (G2-7). Not complete → "continue".
    """
    if not is_complete:
        return "continue"
    reason = (terminated_reason or "").strip()
    if reason and reason in _HALT_TERMINAL_REASONS:
        return f"halted ({reason})"
    return "complete"



class NexusExecutionPipeline:
    """Unified execution pipeline — the single path for ALL requests.

    IMPROVEMENT: Every task deserves full treatment - consistency over shortcuts.
    No fast-paths that skip report generation. Every request goes through
    the complete pipeline to ensure consistent quality and traceability.

    Every request goes through:
    1. Orchestrator plans (selects agents, creates turn plan)
    2. Turn loop executes (think → discuss → execute per turn)
    3. Final report generated (ALWAYS — even for simple tasks)
    4. Answer summarized for user (reading FULL report, not truncated)
    """

    def __init__(
        self,
        llm: Any,
        tool_executor: Any,
        tool_schemas: list[dict[str, Any]] | None = None,
        event_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable] = None,
        task_id: str = "",
        db: Optional[AsyncSession] = None,
    ) -> None:
        self._llm = llm
        self._tool_executor = tool_executor
        self._tool_schemas = tool_schemas or []
        self._event_callback = event_callback
        self._cancel_check = cancel_check
        self._task_id = task_id or str(uuid.uuid4())
        self._db = db
        self._infra_fail_streak = 0

    async def run(
        self,
        message: str,
        memory_context: str = "",
        agent_catalog: str = "",
        resume_from_checkpoint: dict | None = None,
    ) -> dict[str, Any]:
        """Execute the full pipeline for a user request.

        IMPROVEMENT: No fast-path for simple tasks. Every task gets
        TASK_BRIEF, ROE, ACTIVITY, and FINAL_REPORT for consistency
        and traceability.

        Args:
            message: User's original message
            memory_context: Enriched context from memory systems (KG, recall, etc.)
            agent_catalog: Available agent templates formatted as text
            resume_from_checkpoint: If provided, dict from CheckpointPersistence.load_latest_checkpoint().
                Pipeline will reconstruct state and skip completed turns.

        Returns:
            Dict with 'content' (final answer), 'task_id', 'metadata'
        """
        state = self._restore_or_create_state(message, resume_from_checkpoint)

        # Set execution context so all tool handlers resolve workspace correctly
        from app.core.execution_context import current_task_id
        current_task_id.set(self._task_id)

        # Bind trace correlation (audit C4): trace_id + task_id now appear on every
        # log line in this task's async context, and child agent/tool coroutines
        # inherit them automatically via contextvars.
        from app.core import trace_context
        trace_context.bind_trace(task_id=self._task_id)

        try:
            # ═══ Phase 0: Evolution Agent (lazy, config-driven) ═══
            from app.config import get_settings
            _settings = get_settings()
            self._evolution_agent = EvolutionAgent(llm_client=self._llm, config=_settings)

            # ═══ Phase 0.5: Smart Context Aggregation ═══
            memory_context = await self._aggregate_context(
                message, memory_context, _settings
            )

            _is_resume = state._resumed_from_turn > 0

            # ═══ Phase 0.7: Initialize Gap-Fix Subsystems ═══
            data_vault = DataVault(self._task_id)
            cross_turn_registry = CrossTurnRegistry(self._task_id)
            workspace_snapshot = WorkspaceSnapshot(self._task_id)
            ledger_verifier = LedgerVerifier(self._task_id)
            goal_profile = GoalProfile.from_message(
                message,
                # success_criteria lives inside the checkpoint's state_json (A7),
                # not at the top level — read the correct tier so resume keeps it.
                success_criteria=str(
                    (resume_from_checkpoint or {}).get("state_json", {}).get("success_criteria", "")
                ),
            )
            goal_compliance = GoalComplianceChecker(goal_profile, llm=self._llm)
            reset_concurrency_limiter()
            concurrency_limiter = get_concurrency_limiter()

            # ═══ Phase 1: Orchestrator Planning ═══
            orchestrator = NexusOrchestrator(
                llm=self._llm,
                agent_catalog=agent_catalog,
                event_callback=self._event_callback,
            )

            if _is_resume:
                # RESUME PATH: Skip planning, use checkpoint state.
                # Ask orchestrator to plan the NEXT turn based on existing progress.
                # Rebuild the orchestrator's progress history from the restored
                # checkpoint so stagnation/completion logic keeps its context
                # across restart (audit C2 — reunify the two progress stores).
                orchestrator._progress.hydrate_from_turn_map(
                    self._task_id, state.progress_history
                )
                from app.services.agent_spawner import AGENT_TEMPLATES as _RESUME_TEMPLATES
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "task_resumed",
                        "resumed_from_turn": state._resumed_from_turn,
                        "progress_context": f"Resuming from turn {state._resumed_from_turn}",
                    })
                # Build a minimal plan_data from existing state
                plan_data = {
                    "complexity": "medium",
                    "selected_agents": [
                        {"role": role, "name": _RESUME_TEMPLATES.get(role, {}).get("default_name", role.title())}
                        for role in (state.total_agents or ["researcher"])
                    ],
                    "execution_plan": [],
                }
                complexity = "medium"
                _deliverable_mode = ""  # resume: unknown mode → production-grade default

                # Load existing Goal Anchor on resume. If it's missing/unreadable,
                # rebuild it from the success_criteria restored from the checkpoint
                # (A7) — keeping the measurable bar across resume — instead of
                # echoing the raw prompt.
                from app.core import task_paths
                from app.orchestration.nexus_orchestrator import render_goal_anchor
                _anchor_path = task_paths.context_file(self._task_id, "GOAL_ANCHOR.txt")
                _goal_anchor = ""
                if _anchor_path.exists():
                    try:
                        _goal_anchor = _anchor_path.read_text(encoding="utf-8")
                    except Exception:
                        _goal_anchor = ""
                if not _goal_anchor:
                    _goal_anchor = render_goal_anchor(
                        message, {}, success_criteria=state.success_criteria
                    )
            else:
                first_plan, plan_data = await orchestrator.plan_task(
                    message=message,
                    memory_context=memory_context,
                )

                complexity = plan_data.get("complexity", "medium")
                _, _deliverable_mode = self._scope_signals(plan_data)

                # ═══ Phase 1.5: Security Plan Enforcement ═══
                task_type = plan_data.get("task_type", "general")
                is_security_task = task_type in ("security", "intelligence")
                # Persist task_type so the authorization gate can re-run on resume.
                state.task_type = task_type

                if is_security_task:
                    plan_data, first_plan = _enforce_security_plan(
                        plan_data, first_plan, message
                    )

                # ═══ Phase 1.6: Divergent Thinking (complex/expert tasks only) ═══
                if complexity in ("complex", "expert") and not is_security_task:
                    try:
                        from app.orchestration.divergent_thinker import DivergentThinker
                        divergent = DivergentThinker(llm=self._llm)
                        selected_strategy = await divergent.generate_and_select(
                            user_message=message,
                            plan_data=plan_data,
                            memory_context=memory_context,
                        )
                        if selected_strategy and selected_strategy.strategy:
                            strategy_context = (
                                f"\n[STRATEGIC INSPIRATION — subordinate to DESIGN CONSTRAINTS above]\n"
                                f"Consider: {selected_strategy.strategy}\n"
                                f"Insights: {'; '.join(selected_strategy.key_insights[:3])}\n"
                                f"NOTE: This is a SUGGESTION only. If it conflicts with task constraints "
                                f"(tech stack, framework requirements), follow the CONSTRAINTS, not this.\n"
                            )
                            for assignment in first_plan.assignments:
                                assignment.task_description = (
                                    assignment.task_description + strategy_context
                                )
                            if self._event_callback:
                                await self._event_callback({
                                    "event_type": "divergent_thinking_done",
                                    "strategy": selected_strategy.strategy[:500],
                                    "source_frames": selected_strategy.source_frames,
                                    "key_insights": selected_strategy.key_insights[:3],
                                })
                    except Exception as e:
                        logger.debug("divergent_thinking_skipped", error=str(e))

                # Write context files (ALWAYS — even for simple tasks)
                await orchestrator.write_task_brief(self._task_id, message, plan_data)
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "document_created",
                        "document": "TASK_BRIEF",
                        "description": "Task brief document created with objectives and scope",
                    })

                await orchestrator.write_roe(self._task_id, plan_data, user_query=message)
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "document_created",
                        "document": "ROE",
                        "description": "Rules of Engagement document created",
                    })

                await orchestrator.write_todo(self._task_id, plan_data)
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "document_created",
                        "document": "TODO",
                        "description": "Initial TODO checklist created from plan",
                    })

                # Deliverable Contract — the acceptance standard (what the user receives
                # + how it is verified). Drives artifact-first execution and completion.
                await orchestrator.write_contract(self._task_id, plan_data)
                await orchestrator.load_contract_skills(plan_data)
                if self._event_callback and isinstance(
                    plan_data.get("deliverable_contract"), dict
                ) and plan_data["deliverable_contract"].get("artifacts"):
                    _contract = plan_data["deliverable_contract"]
                    await self._event_callback({
                        "event_type": "document_created",
                        "document": "CONTRACT",
                        "description": (
                            f"Deliverable contract [{_contract.get('mode', 'MIXED')}]: "
                            f"{len(_contract.get('artifacts', []))} artifact(s) to deliver"
                        ),
                    })

                # Generate Goal Anchor — persistent compact reminder of user intent.
                # A7: the "SUCCESS WHEN" bar comes from the planner's measurable
                # deliverable_contract (definition_of_done + artifact verify), NOT a
                # re-echo of the prompt. If nothing measurable was derived, say so
                # honestly instead of pasting the message back as a fake criterion.
                from app.orchestration.nexus_orchestrator import (
                    derive_success_criteria,
                    render_goal_anchor,
                )
                state.success_criteria = derive_success_criteria(plan_data)
                _goal_anchor = render_goal_anchor(
                    message, plan_data, success_criteria=state.success_criteria
                )
                from app.core import task_paths
                _anchor_path = task_paths.context_file(self._task_id, "GOAL_ANCHOR.txt")
                _anchor_path.parent.mkdir(parents=True, exist_ok=True)
                _anchor_path.write_text(_goal_anchor, encoding="utf-8")

                # Track all agent roles
                state.total_agents = [
                    a.get("role", "researcher")
                    for a in plan_data.get("selected_agents", [])
                ]

            # Persist complexity on state so effort-bearing loops downstream
            # (e.g. orchestrator exploration rounds) can scale to the task.
            state.complexity = complexity

            # ═══ Phase 2: Create Workers ═══
            workers = self._create_workers(plan_data)

            # ═══ Phase 3: Turn-Based Execution Loop ═══
            context_sharing = ContextSharing()
            activity_logger = ActivityLogger(self._task_id)
            context_ledger = ContextLedger(self._task_id)
            context_index = ContextIndex(self._task_id, context_ledger)

            # Estimate total turns from execution plan
            execution_plan = plan_data.get("execution_plan", [])
            total_turns_estimate = max(len(execution_plan), 1)

            turn_executor = TurnExecutor(
                workers=workers,
                context_sharing=context_sharing,
                activity_logger=activity_logger,
                context_ledger=context_ledger,
                event_callback=self._event_callback,
                cancel_check=self._cancel_check,
                total_turns_estimate=total_turns_estimate,
                memory_context=memory_context,
                task_id=self._task_id,
                goal_anchor=_goal_anchor,
                data_vault=data_vault,
                cross_turn_registry=cross_turn_registry,
                goal_compliance=goal_compliance,
                complexity=complexity,
            )

            if _is_resume:
                # Resume: orchestrator plans the continuation turn
                # Build a synthetic evaluation from checkpoint state
                _resume_eval = EvaluationResult(
                    turn_number=state._resumed_from_turn,
                    progress_pct=max(10, state._resumed_from_turn * 20),
                    is_complete=False,
                    next_turn_focus=f"Continue task from turn {state._resumed_from_turn + 1}. "
                                    f"Read ACTIVITY.md and LEDGER.jsonl for context on prior work.",
                )
                current_plan = await orchestrator.plan_next_turn(state, _resume_eval)
                if current_plan is None:
                    # Nothing more to do — task was likely near completion
                    state.is_complete = True
                    state.status = "complete"
            else:
                current_plan = first_plan

            # ═══ Authorization gate (audit C2) — runs for BOTH fresh and resumed
            # tasks, before any agent spawns. Blocks LIVE OFFENSIVE ops against REAL
            # public infrastructure unless the owner explicitly opted in. Lab/CTF/
            # private targets, passive OSINT, and non-security tasks pass through.
            # Placed after planning/restore so state.task_type is authoritative on
            # both paths (resume restores it from the checkpoint). ═══
            _auth_block = await self._check_authorization(message, state.task_type)
            if _auth_block is not None:
                return _auth_block

            state.status = "executing"
            _MEMORY_REFRESH_INTERVAL = 3  # Re-fetch memory every 3 turns (was 5)

            # Last turn's evaluation — read after the loop to stamp the REAL terminal
            # state into ACTIVITY.md (audit S4). None if the loop never ran (e.g. a
            # resume that immediately had nothing left to do).
            evaluation = None
            while not state.is_complete:
                # Check cancellation — in-memory token first, durable DB flag as
                # backstop (covers missed pub/sub message or unregistered token).
                # Raise (not break) so this routes into TaskRunner's existing
                # CancelledError handler → fast, correctly-labelled task_cancelled.
                # Falling through would run final-report + summarize (2 LLM calls)
                # and return a dict the orchestrator hard-codes to "completed".
                if await self._should_cancel():
                    state.status = "cancelled"
                    state.error = "Cancelled by user"
                    raise asyncio.CancelledError("Task cancelled by user")

                # GAP-1 FIX: Re-fetch memory context periodically so agents
                # benefit from KG/RAG knowledge discovered mid-task.
                if (
                    state.turn_count > 0
                    and state.turn_count % _MEMORY_REFRESH_INTERVAL == 0
                ):
                    refreshed = await self._aggregate_context(
                        message, memory_context, _settings
                    )
                    if refreshed and len(refreshed) > len(memory_context):
                        memory_context = refreshed
                        turn_executor._memory_context = memory_context

                # BUG-NEW-1 FIX: Expand workers for any new roles in this turn's plan.
                # Orchestrator's plan_next_turn() can assign roles not in the initial plan.
                # Without this, those assignments get "(No worker available)" responses.
                self._ensure_workers_for_plan(
                    current_plan, workers, complexity, _deliverable_mode
                )

                # GAP-6 FIX: Snapshot workspace before execution (for rollback)
                pre_turn_snapshot = workspace_snapshot.take_snapshot(current_plan.turn_number)

                # Execute turn
                turn_result = await turn_executor.execute_turn(current_plan, state)
                state.current_turn = current_plan.turn_number

                # Cancel may have landed WHILE the turn ran (long LLM/tool calls).
                # Raise now so we skip evaluate/plan and finalise task_cancelled fast.
                if await self._should_cancel():
                    state.status = "cancelled"
                    state.error = "Cancelled by user"
                    raise asyncio.CancelledError("Task cancelled by user")

                # Tag every log line in this turn with the turn number (audit C4).
                trace_context.bind_turn(current_plan.turn_number)

                # GAP-6 FIX: Check for workspace regression after turn
                if pre_turn_snapshot:
                    regression = workspace_snapshot.detect_regression(pre_turn_snapshot)
                    if regression.get("deleted_files") or regression.get("size_regression"):
                        logger.warning(
                            "workspace_regression_detected",
                            turn=current_plan.turn_number,
                            deleted=regression.get("deleted_files", [])[:3],
                            size_regression=regression.get("size_regression"),
                        )
                        # Auto-rollback on severe regression (>50% size loss)
                        if regression.get("size_regression", {}).get("ratio", 1.0) < 0.5:
                            rollback_result = workspace_snapshot.rollback_to(pre_turn_snapshot)
                            if rollback_result.get("success"):
                                logger.info("workspace_rollback_executed", turn=current_plan.turn_number)
                                if self._event_callback:
                                    await self._event_callback({
                                        "event_type": "workspace_rollback",
                                        "turn": current_plan.turn_number,
                                        "restored_files": rollback_result.get("restored", [])[:5],
                                    })

                # GAP-2 FIX: Check goal compliance for each agent output.
                # A12: clear the PRIOR turn's correction first — the agent (this
                # turn) and the eval builder (last turn) have both read it by now.
                # _update_corrective_context ACCUMULATES (+=), so without this the
                # correction would grow unbounded and replay stale drift every turn.
                goal_compliance.clear_corrective_context()
                for agent_result in turn_result.agent_results:
                    if agent_result.output and agent_result.success:
                        drift_signal = goal_compliance.check_output(
                            agent_role=agent_result.agent_role,
                            output=agent_result.output,
                            turn=current_plan.turn_number,
                        )
                        if drift_signal.is_drifting:
                            logger.info(
                                "goal_drift_detected",
                                agent=agent_result.agent_role,
                                turn=current_plan.turn_number,
                                score=drift_signal.relevance_score,
                            )

                # GAP-4 FIX: Verify ledger claims against workspace
                agent_outputs_for_verify = [
                    {
                        "agent_role": ar.agent_role,
                        "output": ar.output or "",
                        # Include audit_log tool output so the verifier can see an
                        # UNSUBSTANTIATED finding (A6); other tools keep the lean
                        # {tool_name, success} shape to bound payload size.
                        "tool_calls": [
                            {
                                "tool_name": tc.tool_name,
                                "success": tc.success,
                                **({"output": tc.output} if tc.tool_name == "audit_log" else {}),
                            }
                            for tc in ar.tool_calls
                        ],
                    }
                    for ar in turn_result.agent_results
                ]
                turn_verification = ledger_verifier.verify_turn_claims(
                    turn=current_plan.turn_number,
                    agent_outputs=agent_outputs_for_verify,
                )

                # GAP-5 FIX: Register completed actions in cross-turn registry
                for agent_result in turn_result.agent_results:
                    for tc in agent_result.tool_calls:
                        if tc.tool_name and tc.tool_name not in ("context_recall", "data_vault_store", "data_vault_retrieve"):
                            action_key = f"{tc.tool_name}:{str(tc.arguments)[:100]}"
                            cross_turn_registry.register_completed(
                                action_key=action_key,
                                agent_role=agent_result.agent_role,
                                turn=current_plan.turn_number,
                                success=tc.success,
                                outcome_summary=(tc.output or "")[:200],
                            )

                # ═══ Track infrastructure health ═══
                # When LLM provider is down, NO agent can run, and the Orchestrator
                # evaluation itself will also fail. In this case no LLM can "decide"
                # anything, so we must handle it at code level: wait and retry, or stop.
                all_failed = all(
                    not getattr(ar, "success", True)
                    or "All providers failed" in str(getattr(ar, "output", ""))
                    for ar in turn_result.agent_results
                ) if turn_result.agent_results else False

                if all_failed:
                    self._infra_fail_streak += 1
                    if self._infra_fail_streak >= 20:
                        # HARD STOP: 20 consecutive all-fail turns = infrastructure truly dead
                        logger.error(
                            "infrastructure_failure_hard_stop",
                            consecutive_failures=self._infra_fail_streak,
                        )
                        state.is_complete = True
                        state.status = "failed"
                        state.error = f"Infrastructure failure: all agents failed {self._infra_fail_streak} consecutive turns"
                        break
                    elif self._infra_fail_streak >= 3:
                        # FIX D5: Probe-before-wait — try a lightweight LLM call first.
                        # If probe succeeds, skip the full backoff wait.
                        wait_secs = min(30 * (2 ** (self._infra_fail_streak - 3)), 300)
                        probe_ok = await self._probe_infrastructure()
                        if probe_ok:
                            logger.info("infrastructure_probe_recovered", streak=self._infra_fail_streak)
                            self._infra_fail_streak = max(0, self._infra_fail_streak - 2)
                        else:
                            logger.warning(
                                "infrastructure_failure_backoff",
                                consecutive_failures=self._infra_fail_streak,
                                wait_seconds=wait_secs,
                            )
                            if self._event_callback:
                                await self._event_callback({
                                    "event_type": "infrastructure_backoff",
                                    "consecutive_failures": self._infra_fail_streak,
                                    "wait_seconds": wait_secs,
                                    "message": f"All agents failed {self._infra_fail_streak}x. "
                                               f"Waiting {wait_secs}s before retry.",
                                })
                            await asyncio.sleep(wait_secs)
                else:
                    self._infra_fail_streak = 0

                # Evolution Agent: continuous improvement analysis (runs BEFORE evaluate)
                evolution_insight = await self._run_evolution(turn_result, state)
                if evolution_insight:
                    turn_result.evolution_insight = evolution_insight

                # Emit evolution event
                await self._emit_evolution_event(evolution_insight, state)

                # Orchestrator evaluates (with evolution context + ledger for richer insight)
                ledger_context = context_ledger.build_context_for_orchestrator(
                    current_turn=current_plan.turn_number,
                    max_chars=15000,
                )

                # Inject situational awareness signals so Orchestrator can make informed decisions
                situational_signals = self._build_situational_signals(state, turn_result)
                if situational_signals:
                    ledger_context = f"{situational_signals}\n\n{ledger_context}"

                # GAP-4 FIX: Inject verification context into evaluation
                verification_ctx = ledger_verifier.build_verification_context()
                if verification_ctx:
                    ledger_context = f"{verification_ctx}\n\n{ledger_context}"

                # ROOT-B: Static audit of agent-authored scripts for self-cancelling
                # ("tautology") verification code — a check forced to PASS for all inputs
                # (task d8b78042's "energy closure 0.00%"). Surfaces suspects so the
                # evaluator/critic re-runs them instead of trusting the self-graded PASS.
                _scans: dict[str, Any] = {}  # hoisted: also feeds the A3 completion gate
                try:
                    from pathlib import Path

                    from app.core import task_paths
                    from app.orchestration.tautology_detector import (
                        build_suspect_context,
                        scan_workspace_scripts,
                    )
                    from app.orchestration.tautology_prober import probe_scan

                    _work_dir = task_paths.work_dir(self._task_id)
                    _scans = scan_workspace_scripts(_work_dir)
                    # Runtime-confirm each suspect by re-executing its metric slice under
                    # varied inputs — promotes proven tautologies, drops false positives.
                    for _rel, _res in _scans.items():
                        try:
                            _src = (Path(_work_dir) / _rel).read_text(encoding="utf-8", errors="ignore")
                            probe_scan(_src, _res)
                        except Exception:
                            pass
                    _suspect_ctx = build_suspect_context(_scans)
                    if _suspect_ctx:
                        ledger_context = f"{_suspect_ctx}\n\n{ledger_context}"
                        logger.warning(
                            "tautology_suspect_detected",
                            task_id=self._task_id[:8],
                            files=list(_scans.keys())[:5],
                        )
                except Exception as e:
                    logger.debug("tautology_scan_failed", error=str(e), task_id=self._task_id[:8])

                # GAP-2 FIX: Inject drift context into evaluation
                drift_ctx = goal_compliance.corrective_context
                if drift_ctx:
                    ledger_context = f"{drift_ctx}\n\n{ledger_context}"

                evaluation = await orchestrator.evaluate_turn(
                    turn_result=turn_result,
                    state=state,
                    user_message=message,
                    turn_plan=current_plan,
                    evolution_context=evolution_insight,
                    ledger_context=ledger_context,
                )

                # Enrich evaluation with evolution insights
                # Pass stagnation info so Evolution doesn't infinitely block completion
                if evolution_insight and self._evolution_agent:
                    stagnation_turns = self._calc_stagnation_turns(state, evaluation)
                    evaluation = self._evolution_agent.enrich_evaluation(
                        evaluation, evolution_insight,
                        stagnation_turns=stagnation_turns,
                    )

                # CC-1/CC-3 integrity scans — computed ONLY when the orchestrator
                # intends to COMPLETE this turn (not every turn). Re-running scripts
                # is the right verification at the close gate, but doing it each turn
                # would repeat any side-effects (HTTP calls, writes) N times. At the
                # close gate it runs once. reexec compares claimed vs real output
                # (mismatch → block); numeric flags the same quantity with ≥2 values.
                _reexec_scan: Any = None
                _numeric_scan: Any = None
                if evaluation.is_complete:
                    try:
                        from app.core import task_paths as _tp
                        from app.orchestration.artifact_reexecutor import (
                            build_reexec_context,
                            reexecute_runnable_artifacts,
                        )
                        from app.orchestration.numeric_integrity import (
                            build_conflict_context,
                            run_numeric_integrity,
                        )
                        from app.sandbox.manager import SandboxManager

                        _wd = _tp.work_dir(self._task_id)
                        _claimed = [ar.output or "" for ar in turn_result.agent_results]
                        _reexec_scan = await reexecute_runnable_artifacts(
                            work_dir=_wd, claimed_outputs=_claimed, sandbox=SandboxManager(),
                        )
                        _rx_ctx = build_reexec_context(_reexec_scan)
                        if _rx_ctx:
                            ledger_context = f"{_rx_ctx}\n\n{ledger_context}"

                        # CC-3: reconcile the DELIVERABLE prose (not "") against the
                        # structured artifacts. Audit f188a4c4 G2: the headline number
                        # lives in _work/report/*.md, so an empty report_text made the
                        # scan blind to it. Read the canonical deliverable defensively.
                        _deliverable_text = self._read_deliverable_text(_wd)
                        _numeric_scan = run_numeric_integrity(_wd, report_text=_deliverable_text)
                        _nm_ctx = build_conflict_context(_numeric_scan)
                        if _nm_ctx:
                            ledger_context = f"{_nm_ctx}\n\n{ledger_context}"
                    except Exception as e:
                        logger.debug("reexec_or_numeric_scan_failed", error=str(e), task_id=self._task_id[:8])

                # A3: did an INDEPENDENT reviewer actually execute in a PRIOR turn?
                # Crucially, the current turn is EXCLUDED: a reviewer that runs in the
                # same turn that produced the self-attested verdict is grading work
                # submitted alongside it — that is the very rubber-stamp this gate
                # exists to stop. Independent review must come AFTER the work, i.e. in
                # an earlier turn. Roles limited to dedicated reviewers (critic/judge/
                # qa/reviewer); production roles like security_reviewer are excluded
                # because they author findings and would self-clear the gate.
                _review_roles = {"critic", "judge", "qa", "reviewer"}
                independent_review_recent = any(
                    ar.agent_role in _review_roles and (ar.output or ar.tool_calls)
                    for tr in state.turns_completed[-2:]
                    for ar in getattr(tr, "agent_results", [])
                )

                # Hard ceiling: at the absolute turn limit or a long stagnation
                # window, the A3 gates must NOT keep vetoing completion (else a task
                # whose critic can never clear the gate loops past _MAX_TURNS forever).
                from app.orchestration.termination import (
                    _MAX_STAGNANT_TURNS,
                    _MAX_STAGNANT_TURNS_SECURITY,
                )
                _stag_limit = (
                    _MAX_STAGNANT_TURNS_SECURITY if is_security_task else _MAX_STAGNANT_TURNS
                )
                _hard_ceiling_reached = (
                    state.turn_count >= _MAX_TURNS
                    or self._calc_stagnation_turns(state, evaluation) >= _stag_limit
                )

                # Deterministic guardrails: do not allow "complete" when the system
                # clearly lacks required runtime credentials (common failure mode
                # seen in tests as repeated "Missing credentials ... OPENAI_API_KEY"),
                # when this turn's verification caught a hallucinated "done" claim, or
                # when completion rests on a proven tautology / un-reviewed self-attest.
                evaluation = self._postprocess_evaluation(
                    turn_result, evaluation, turn_verification=turn_verification,
                    tautology_scans=_scans,
                    independent_review_recent=independent_review_recent,
                    hard_ceiling_reached=_hard_ceiling_reached,
                    reexec_scan=_reexec_scan,
                    numeric_scan=_numeric_scan,
                    veto_counts=orchestrator._veto_counts,
                )

                turn_result.orchestrator_evaluation = evaluation

                # Write ledger entries for this turn's results + discussion synthesis
                _is_complex = complexity in ("complex", "expert")
                self._write_ledger_entries(
                    context_ledger, turn_result, current_plan, evaluation,
                    context_sharing=context_sharing,
                    is_complex_task=_is_complex,
                )

                # Rebuild context index with current state
                context_index.rebuild(
                    current_turn=current_plan.turn_number,
                    progress_pct=evaluation.progress_pct,
                    user_message=message,
                    active_blockers=evaluation.missing_items[:5] if evaluation.missing_items else None,
                )

                # Epoch compression (every N turns) — LLM-enhanced when available
                await context_ledger.maybe_compress_epoch_with_llm(
                    current_plan.turn_number, llm=self._llm
                )

                # Append AFTER evaluation — prevents orchestrator from seeing
                # the current turn's data twice (once in accumulated_briefing,
                # once in the explicit results_text passed to evaluate_turn).
                state.turns_completed.append(turn_result)

                # ═══ Checkpoint: Save state after turn evaluation ═══
                await self._save_checkpoint_if_enabled(state, reason="turn_complete")

                # Log evaluation
                await activity_logger.log_orchestrator_evaluation(evaluation)

                # Emit evaluation event for frontend persistence
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "evaluation_done",
                        "turn": state.turn_count,
                        "progress_pct": evaluation.progress_pct,
                        "decision": "complete" if evaluation.is_complete else "continue",
                        "is_complete": evaluation.is_complete,
                        "missing_items": evaluation.missing_items[:5] if evaluation.missing_items else [],
                        "evaluation_text": (getattr(evaluation, "evaluation_text", "") or "")[:500],
                        "agent_evaluations": getattr(evaluation, "agent_evaluations", [])[:5],
                        "contradictions": getattr(evaluation, "contradictions", [])[:3],
                        "next_turn_focus": (getattr(evaluation, "next_turn_focus", "") or "")[:200],
                    })

                # Update TODO.md — single source of truth for progress
                await orchestrator.update_todo(
                    self._task_id, evaluation, user_message=message,
                )

                # Write DECISIONS.md — clean record of orchestrator + evolution assessments
                self._write_decisions_entry(
                    turn_number=current_plan.turn_number,
                    evaluation=evaluation,
                    evolution_insight=evolution_insight,
                )
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "document_created",
                        "document": "TODO_UPDATE",
                        "description": f"TODO updated after turn {state.current_turn} evaluation",
                    })

                if evaluation.is_complete:
                    # FIX D3: Auto-extension with STRICT guards.
                    # Only extend when next_turn_focus starts with actionable verb,
                    # progress is meaningfully incomplete, and we haven't exceeded 2x planned turns.
                    _ACTIONABLE_PREFIXES = (
                        "fix", "complete", "address", "implement", "run", "execute",
                        "install", "build", "create", "write", "deploy", "test",
                        "verify", "scan", "exploit", "investigate",
                    )
                    _next_focus = (evaluation.next_turn_focus or "").strip()
                    _first_word = _next_focus.split()[0].lower().rstrip(":") if _next_focus else ""
                    _has_actionable_steps = _first_word in _ACTIONABLE_PREFIXES
                    _meaningfully_incomplete = evaluation.progress_pct < 90
                    _within_reasonable_bounds = state.turn_count < max(total_turns_estimate * 2, 10)
                    _not_at_hard_ceiling = state.turn_count < _MAX_TURNS

                    if (
                        _has_actionable_steps
                        and _meaningfully_incomplete
                        and _within_reasonable_bounds
                        and _not_at_hard_ceiling
                    ):
                        evaluation.is_complete = False
                        logger.info(
                            "auto_extension_granted",
                            turns=state.turn_count,
                            reason=f"actionable next_turn_focus: {_first_word}",
                            next_focus=evaluation.next_turn_focus[:100],
                            progress=evaluation.progress_pct,
                        )

                if evaluation.is_complete:
                    state.is_complete = True
                    state.status = "complete"
                else:
                    # Plan next turn
                    next_plan = await orchestrator.plan_next_turn(state, evaluation)
                    if next_plan is None:
                        state.is_complete = True
                        state.status = "complete"
                    else:
                        current_plan = next_plan
                        # Emit next_turn_plan for frontend persistence
                        if self._event_callback:
                            await self._event_callback({
                                "event_type": "next_turn_plan",
                                "turn": next_plan.turn_number,
                                "objective": next_plan.objective,
                                "milestone": getattr(next_plan, "milestone", ""),
                                "execution_mode": getattr(next_plan, "execution_mode", "sequential"),
                                "assignments": [
                                    {
                                        "agent_name": a.agent_name,
                                        "agent_role": getattr(a, "agent_role", ""),
                                        "task": a.task_description[:300],
                                        "success_criteria": getattr(a, "success_criteria", "")[:200],
                                    }
                                    for a in next_plan.assignments
                                ],
                            })

            # Log the terminal marker reflecting the REAL terminal state (audit S4):
            # a genuine completion vs an honest halt (terminated_reason) vs a loop
            # that stopped without completing. ACTIVITY.md must agree with TODO.md.
            _term_reason = getattr(evaluation, "terminated_reason", "") if evaluation else ""
            await activity_logger.log_task_complete(
                is_complete=bool(state.is_complete and state.status == "complete"),
                terminated_reason=_term_reason,
            )

            # ═══ Phase 4: Generate Final Report (ALWAYS) ═══
            # IMPROVEMENT: Every task gets a full report — no fast-path skip.
            # Consistency and traceability are more important than saving a few LLM calls.
            report_gen = FinalReportGenerator(
                task_id=self._task_id,
                llm=self._llm,
                event_callback=self._event_callback,
            )
            final_report = await report_gen.generate()

            # ═══ Phase 4.5: XSkill Accumulate (fire-and-forget) ═══
            if state.is_complete and state.status == "complete":
                await self._accumulate_xskill(
                    message=message,
                    state=state,
                    final_report=final_report,
                    complexity=complexity,
                )

            # ═══ Phase 4.6: Graph RAG Auto-Index (fire-and-forget) ═══
            if state.is_complete and state.status == "complete":
                await self._auto_index_entities(
                    message=message,
                    final_report=final_report,
                )

            # ═══ Phase 5: Summarize for User ═══
            # IMPROVEMENT: Read the FULL report when summarizing — no truncation
            answer = await self._summarize_for_user(message, final_report)

            # Stream tokens to frontend
            await self._stream_answer(answer)

            return {
                "content": answer,
                "task_id": self._task_id,
                "tokens_streamed": True,
                "metadata": {
                    "turns": state.turn_count,
                    "agents": state.total_agents,
                    "complexity": complexity,
                    "status": state.status,
                },
            }

        except Exception as e:
            import traceback
            logger.error("pipeline_execution_failed", error=str(e), task_id=self._task_id[:8], traceback=traceback.format_exc())
            state.status = "failed"
            state.error = str(e)

            # Try to return whatever we have
            partial = self._collect_partial_output(state)
            if partial:
                await self._stream_answer(partial)
                return {
                    "content": partial,
                    "task_id": self._task_id,
                    "tokens_streamed": True,
                    "metadata": {"status": "failed", "error": str(e)[:200]},
                }

            error_msg = f"⚠️ Task execution failed: {str(e)[:200]}"
            await self._stream_answer(error_msg)
            return {
                "content": error_msg,
                "task_id": self._task_id,
                "tokens_streamed": True,
                "metadata": {"status": "failed", "error": str(e)[:200]},
            }

        finally:
            # Drop trace bindings so they don't bleed into the next task that may
            # reuse this async context (e.g. a pooled worker).
            trace_context.clear_trace()
            # Token Governor: clear per-task budget on task end (best-effort).
            try:
                from app.core.token_governor import clear_task_budget
                clear_task_budget(self._task_id)
            except Exception:
                pass

    def _restore_or_create_state(
        self, message: str, checkpoint: dict | None
    ) -> ExecutionState:
        """Create fresh ExecutionState or restore from checkpoint."""
        if not checkpoint:
            return ExecutionState(task_id=self._task_id, user_message=message)

        state_json = checkpoint.get("state_json", {})
        state = ExecutionState(
            task_id=state_json.get("task_id", self._task_id),
            user_message=state_json.get("user_message", message),
            current_turn=state_json.get("current_turn", 0),
            total_agents=state_json.get("total_agents", []),
            status="executing",
            collected_data_cache=state_json.get("collected_data_cache", []),
            failure_tracker=state_json.get("failure_tracker", {}),
            # FIX D2: Restore additional fields for full context on resume
            progress_history=state_json.get("progress_history", {}),
            objective_tracking=state_json.get("objective_tracking", []),
            complexity=state_json.get("complexity", "medium"),
            task_type=state_json.get("task_type", "general"),
            success_criteria=state_json.get("success_criteria", ""),
        )
        # Inject evolution suggestions into collected_data_cache for visibility
        _evo_suggestions = state_json.get("evolution_suggestions", [])
        if _evo_suggestions:
            state.collected_data_cache.append({
                "turn": state_json.get("current_turn", 0),
                "agent": "evolution_agent",
                "data_summary": "Prior evolution suggestions: " + "; ".join(_evo_suggestions[-5:]),
            })
        # Mark as resumed for downstream logic
        state._resumed_from_turn = checkpoint.get("turn_number", 0)
        logger.info(
            "state_restored_from_checkpoint",
            task_id=self._task_id[:8],
            resumed_turn=state._resumed_from_turn,
            prior_agents=state.total_agents[:3],
        )
        return state

    def _create_workers(self, plan_data: dict[str, Any]) -> dict[str, AgentWorker]:
        """Create AgentWorker instances for selected agents."""
        from app.services.agent_spawner import AGENT_TEMPLATES
        from app.orchestration.data_vault import DATA_VAULT_STORE_SCHEMA, DATA_VAULT_RETRIEVE_SCHEMA

        # GAP-1 FIX: Inject DataVault tool schemas so agents can store/retrieve artifacts
        _enriched_schemas = list(self._tool_schemas) + [DATA_VAULT_STORE_SCHEMA, DATA_VAULT_RETRIEVE_SCHEMA]

        # Task-scope signals → workers select solution scope (avoid forcing
        # full-stack on simple/research tasks).
        complexity, deliverable_mode = self._scope_signals(plan_data)

        workers: dict[str, AgentWorker] = {}
        selected = plan_data.get("selected_agents", [])

        for agent_info in selected:
            role = agent_info.get("role", "researcher")
            template = AGENT_TEMPLATES.get(role, {})

            raw_name = agent_info.get("name", "")
            agent_name = (
                raw_name
                if raw_name and raw_name.lower() != role.lower() and raw_name.lower() != role.replace("_", " ")
                else template.get("default_name", f"Agent-{role}")
            )

            worker = AgentWorker(
                agent_name=agent_name,
                agent_role=role,
                personality=template.get("personality", ""),
                expertise=template.get("expertise", []),
                thinking_style=template.get("thinking_style", "analytical"),
                llm=self._llm,
                tool_executor=self._tool_executor,
                tool_schemas=_enriched_schemas,
                event_callback=self._event_callback,
                domain_knowledge=template.get("knowledge", ""),
                complexity=complexity,
                deliverable_mode=deliverable_mode,
            )
            workers[role] = worker

        # Ensure at least one worker exists
        if not workers:
            workers["researcher"] = AgentWorker(
                agent_name="Research Specialist",
                agent_role="researcher",
                personality="Methodical, curious, detail-oriented.",
                expertise=["web_research", "fact_checking"],
                thinking_style="empirical",
                llm=self._llm,
                tool_executor=self._tool_executor,
                tool_schemas=_enriched_schemas,
                event_callback=self._event_callback,
                complexity=complexity,
                deliverable_mode=deliverable_mode,
            )

        return workers

    @staticmethod
    def _scope_signals(plan_data: dict[str, Any]) -> tuple[str, str]:
        """Extract (complexity, deliverable_mode) from the planner output.

        Both are best-effort; missing values yield "" so the prompt falls back
        to the production-grade default (back-compat).
        """
        complexity = str(plan_data.get("complexity", "") or "")
        mode = ""
        contract = plan_data.get("deliverable_contract")
        if isinstance(contract, dict):
            mode = str(contract.get("mode", "") or "")
        return complexity, mode

    def _ensure_workers_for_plan(
        self,
        plan: "TurnPlan",
        workers: dict[str, AgentWorker],
        complexity: str = "",
        deliverable_mode: str = "",
    ) -> None:
        """BUG-NEW-1 FIX: Dynamically create workers for roles assigned in later turns.

        Orchestrator's plan_next_turn() can assign roles not in the initial plan.
        Without dynamic expansion, those assignments get "(No worker available)".
        This method checks each assignment and creates a worker on-the-fly from
        AGENT_TEMPLATES if the role doesn't already have a worker.
        """
        from app.services.agent_spawner import AGENT_TEMPLATES
        from app.orchestration.data_vault import DATA_VAULT_STORE_SCHEMA, DATA_VAULT_RETRIEVE_SCHEMA

        _enriched_schemas = list(self._tool_schemas) + [DATA_VAULT_STORE_SCHEMA, DATA_VAULT_RETRIEVE_SCHEMA]

        for assignment in plan.assignments:
            if assignment.agent_role in workers:
                continue
            # Role not yet in workers — create dynamically from template
            template = AGENT_TEMPLATES.get(assignment.agent_role, {})
            raw_name = assignment.agent_name or ""
            _agent_name = (
                raw_name
                if raw_name and raw_name.lower() != assignment.agent_role.lower()
                   and raw_name.lower() != assignment.agent_role.replace("_", " ")
                else template.get("default_name", f"Agent-{assignment.agent_role}")
            )
            worker = AgentWorker(
                agent_name=_agent_name,
                agent_role=assignment.agent_role,
                personality=template.get("personality", ""),
                expertise=template.get("expertise", []),
                thinking_style=template.get("thinking_style", "analytical"),
                llm=self._llm,
                tool_executor=self._tool_executor,
                tool_schemas=_enriched_schemas,
                event_callback=self._event_callback,
                domain_knowledge=template.get("knowledge", ""),
                complexity=complexity,
                deliverable_mode=deliverable_mode,
            )
            workers[assignment.agent_role] = worker
            logger.info(
                "worker_dynamically_created",
                role=assignment.agent_role,
                name=assignment.agent_name,
                task_id=self._task_id[:8],
            )

    async def _summarize_for_user(self, user_message: str, report: str) -> str:
        """Summarize FINAL_REPORT into a user-facing answer.

        Always passes through LLM summarization to produce a clean,
        user-facing response (never expose raw internal markdown).
        """
        if not report or len(report) < 50:
            return "(No results generated)"

        system_prompt = build_final_summary_prompt()

        # IMPROVEMENT: For long reports, chunk and summarize in stages
        if len(report) > 30000:
            # Multi-chunk summarization for very long reports
            return await self._chunked_summarize(user_message, report, system_prompt)

        # Standard case: feed full report to LLM
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"## Original Question\n{user_message}\n\n"
                f"## Full Report\n{report}\n\n"
                "Provide the final answer to the user."
            )},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.3, max_tokens=32768)
            content = result.get("content", report)
            return self._append_truncation_warning(content)
        except Exception as e:
            logger.warning("summary_failed", error=str(e))
            return report

    async def _chunked_summarize(
        self,
        user_message: str,
        report: str,
        system_prompt: str,
    ) -> str:
        """Summarize a very long report by chunking and merging.

        IMPROVEMENT: For reports > 30K chars, we chunk them, summarize each,
        then do a final merge — ensuring the LLM processes ALL content.
        """
        chunk_size = 15000
        chunks = []
        remaining = report
        while remaining:
            chunks.append(remaining[:chunk_size])
            remaining = remaining[chunk_size:]

        # Summarize each chunk
        chunk_summaries: list[str] = []
        for i, chunk in enumerate(chunks):
            messages = [
                {"role": "system", "content": (
                    f"You are summarizing section {i + 1}/{len(chunks)} of a task report. "
                    "Extract the key findings, data, and conclusions from this section. "
                    "Be comprehensive — include all important details."
                )},
                {"role": "user", "content": chunk},
            ]
            try:
                result = await self._llm.chat(messages=messages, temperature=0.2, max_tokens=8192)
                chunk_summaries.append(result.get("content", ""))
            except Exception:
                chunk_summaries.append(chunk[:2000])

        # Final merge
        merged = "\n\n---\n\n".join(chunk_summaries)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"## Original Question\n{user_message}\n\n"
                f"## Report Summaries (all sections)\n{merged}\n\n"
                "Provide the final comprehensive answer to the user."
            )},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.3, max_tokens=32768)
            content = result.get("content", merged)
            return self._append_truncation_warning(content)
        except Exception as e:
            logger.warning("chunked_summary_failed", error=str(e))
            return merged

    @staticmethod
    def _append_truncation_warning(text: str) -> str:
        """Detect if LLM response was likely truncated and append a warning."""
        if not text:
            return text
        stripped = text.rstrip()
        if not stripped:
            return text
        # Consider it complete if it ends with sentence-ending punctuation or newline
        if stripped[-1] in ".!?。\n":
            return text
        return (
            text
            + "\n\n(Note: This summary may be truncated. "
            "See FINAL_REPORT.md in task artifacts for the full version.)"
        )

    async def _stream_answer(self, answer: str) -> None:
        """Stream the final answer as token events."""
        if not self._event_callback or not answer:
            return

        chunk_size = 40
        for i in range(0, len(answer), chunk_size):
            chunk = answer[i:i + chunk_size]
            try:
                await self._event_callback({"event_type": "token", "content": chunk})
                await asyncio.sleep(0.01)
            except Exception:
                break

    async def _probe_infrastructure(self) -> bool:
        """FIX D5: Lightweight probe to check if LLM providers recovered.

        Sends minimal request; returns True if any provider responds.
        """
        try:
            result = await self._llm.chat(
                messages=[{"role": "user", "content": "ping"}],
                temperature=0.0,
                max_tokens=5,
            )
            return bool(result.get("content"))
        except Exception:
            return False

    async def _run_evolution(
        self,
        turn_result: TurnResult,
        state: ExecutionState,
    ) -> Optional[EvolutionInsight]:
        """Run Evolution Agent with graceful degradation (never raises)."""
        try:
            result = await self._evolution_agent.analyze(turn_result, state)
            get_background_metrics().record_success("evolution_agent")
            return result
        except Exception as e:
            import traceback
            logger.warning("evolution_agent_error", error=str(e), traceback=traceback.format_exc())
            get_background_metrics().record_failure("evolution_agent", str(e))
            return None

    async def _emit_evolution_event(
        self, insight: Optional[EvolutionInsight], state: ExecutionState
    ) -> None:
        """Emit evolution insight event for DB persistence."""
        if not insight or not self._event_callback:
            return
        await self._event_callback({
            "event_type": "evolution_insight",
            "turn": state.current_turn,
            "improvement_suggestions": insight.improvement_suggestions[:3],
            "blind_spots": insight.blind_spots[:3],
            "quality_trajectory": insight.quality_trajectory,
            "challenge_questions": insight.challenge_questions[:2],
            "severity_score": insight.severity_score,
            "should_inject": insight.should_inject,
        })

    def _collect_partial_output(self, state: ExecutionState) -> str:
        """Collect whatever output exists from completed turns."""
        parts: list[str] = []
        for turn in state.turns_completed:
            for result in turn.agent_results:
                if result.output and len(result.output) > 50:
                    parts.append(f"**{result.agent_name}**: {result.output}")

        return "\n\n".join(parts) if parts else ""

    async def _aggregate_context(
        self, message: str, memory_context: str, settings: Any
    ) -> str:
        """Enrich memory_context via SmartContextAggregator (parallel multi-source fetch).

        Registers incoming memory_context as one source alongside KG, RAG, and xskill.
        Falls back to the original memory_context on any failure.
        """
        try:
            from app.core.dependencies import _session_factory

            if not _session_factory:
                return memory_context

            timeout = settings.context_source_timeout
            aggregator = SmartContextAggregator(
                budget_chars=settings.context_budget_complex,
                dedup_threshold=0.6,
            )

            # Source 1: Incoming memory_context (memory layers recall from caller)
            if memory_context:
                async def _fetch_memory_layers(q: str, **kw: Any) -> Optional[str]:
                    return memory_context

                aggregator.add_source("memory_layers", _fetch_memory_layers, timeout=timeout)

            # Source 2: Knowledge Graph
            async def _fetch_kg(q: str, **kw: Any) -> Optional[str]:
                from app.memory.manager import MemoryManager

                async with _session_factory() as db:
                    mm = MemoryManager(db)
                    results = await mm.search_knowledge(
                        query=q[:500], limit=5, include_relations=True, max_hops=1
                    )
                    if results:
                        lines = ["## Prior Knowledge\n"]
                        for node in results:
                            desc = node.get("description", "")
                            if desc and len(desc) > 20:
                                lines.append(f"- **{node.get('name', '')}**: {desc[:300]}")
                        if len(lines) > 1:
                            return "\n".join(lines)
                return None

            aggregator.add_source("knowledge_graph", _fetch_kg, timeout=timeout)

            # Source 3: RAG hybrid retrieval
            async def _fetch_rag(q: str, **kw: Any) -> Optional[str]:
                from app.rag.retriever import HybridRetriever, format_retrieval_context

                async with _session_factory() as db:
                    retriever = HybridRetriever(db)
                    rag_results = await retriever.retrieve(query=q[:500], top_k=5)
                    if rag_results:
                        ctx = format_retrieval_context(rag_results, max_chars=4000)
                        if ctx:
                            return f"## RAG Knowledge\n\n{ctx}"
                return None

            aggregator.add_source("rag", _fetch_rag, timeout=timeout)

            # Source 4: XSkill prior knowledge
            async def _fetch_xskill(q: str, **kw: Any) -> Optional[str]:
                from app.xskill.engine import ContinualLearningEngine

                async with _session_factory() as db:
                    engine = ContinualLearningEngine(db, self._llm)
                    knowledge = await engine.retrieve_knowledge(q)
                    ctx = knowledge.get("context", "")
                    if ctx:
                        return ctx[:3000]
                return None

            aggregator.add_source("xskill", _fetch_xskill, timeout=timeout)

            enriched = await aggregator.aggregate(user_query=message)
            return enriched if enriched else memory_context

        except Exception as e:
            logger.warning(
                "smart_context_aggregation_failed",
                error=str(e),
                task_id=self._task_id[:8],
            )
            return memory_context

    async def _accumulate_xskill(
        self,
        message: str,
        state: ExecutionState,
        final_report: str,
        complexity: str,
    ) -> None:
        """Fire-and-forget XSkill knowledge accumulation after successful task.

        Wraps in try/except so it never blocks or breaks user response.
        """
        if not self._db:
            return

        try:
            from app.xskill.engine import ContinualLearningEngine
            from app.core.dependencies import _session_factory

            if _session_factory is None:
                return

            async with _session_factory() as xskill_db:
                engine = ContinualLearningEngine(xskill_db, self._llm)

                # Build task_result dict from pipeline execution data
                tools_used: list[str] = []
                tool_calls_total = 0
                for turn in state.turns_completed:
                    for agent_result in turn.agent_results:
                        tool_calls_total += len(agent_result.tool_calls)
                        for tc in agent_result.tool_calls:
                            tool_name = tc.tool_name
                            if tool_name and tool_name not in tools_used:
                                tools_used.append(tool_name)

                task_result = {
                    "content": final_report[:5000],
                    "final_answer": final_report[:2000],
                    "tools_used": tools_used,
                    "tool_calls_total": tool_calls_total,
                    "turns": state.turn_count,
                    "complexity": complexity,
                }

                agents_used = [
                    {"role": role, "name": role}
                    for role in state.total_agents
                ]

                task_uuid: UUID | None = None
                try:
                    task_uuid = UUID(self._task_id)
                except (ValueError, AttributeError):
                    pass

                accumulated = await engine.accumulate_knowledge(
                    task_description=message,
                    task_result=task_result,
                    task_id=task_uuid,
                    task_status="completed",
                    agents_used=agents_used,
                )
                await xskill_db.commit()

            # Emit event on success
            if self._event_callback and accumulated.get("experience_count", 0) > 0:
                await self._event_callback({
                    "event_type": "xskill_accumulate",
                    "task_id": self._task_id,
                    "skill_id": accumulated.get("skill_id"),
                    "experience_count": accumulated.get("experience_count", 0),
                    "pipeline": accumulated.get("pipeline", "unknown"),
                })

            logger.info(
                "xskill_accumulate_done",
                task_id=self._task_id[:8],
                experience_count=accumulated.get("experience_count", 0),
                skill_built=accumulated.get("skill") is not None,
            )
            get_background_metrics().record_success("xskill_accumulate")

        except Exception as e:
            # Fire-and-forget: log but never propagate
            import traceback
            logger.warning(
                "xskill_accumulate_failed",
                task_id=self._task_id[:8],
                error=str(e),
                traceback=traceback.format_exc(),
            )
            get_background_metrics().record_failure("xskill_accumulate", str(e))

    async def _auto_index_entities(
        self,
        message: str,
        final_report: str,
    ) -> None:
        """Fire-and-forget: Extract entities from task results and store in Knowledge Graph.

        This ensures the Graph RAG has data to query for future requests.
        Combines user query + final report for comprehensive entity extraction.
        Uses LLM-based extraction for high-quality entities and relationships.

        Never raises — errors are logged and swallowed.
        """
        if not self._db:
            return

        try:
            from app.memory.entity_extractor import extract_entities_from_text

            # Combine message + report for richer entity extraction
            content_to_index = (
                f"## User Query\n{message}\n\n"
                f"## Task Results\n{final_report[:8000]}"
            )

            result = await extract_entities_from_text(
                content=content_to_index,
                task_id=self._task_id,
                db=self._db,
                llm=self._llm,
                incremental=False,
            )

            nodes_created = result.get("nodes_created", 0)
            edges_created = result.get("edges_created", 0)

            if nodes_created > 0 or edges_created > 0:
                logger.info(
                    "graph_rag_auto_index_done",
                    task_id=self._task_id[:8],
                    nodes_created=nodes_created,
                    nodes_updated=result.get("nodes_updated", 0),
                    edges_created=edges_created,
                )

                # Emit event for frontend visibility
                if self._event_callback:
                    await self._event_callback({
                        "event_type": "graph_rag_indexed",
                        "task_id": self._task_id,
                        "nodes_created": nodes_created,
                        "edges_created": edges_created,
                    })

            get_background_metrics().record_success("graph_rag_auto_index")

        except Exception as e:
            # Fire-and-forget: log but never propagate
            import traceback
            logger.warning(
                "graph_rag_auto_index_failed",
                task_id=self._task_id[:8],
                error=str(e),
                traceback=traceback.format_exc(),
            )
            get_background_metrics().record_failure("graph_rag_auto_index", str(e))

    @staticmethod
    def _write_ledger_entries(
        ledger: "ContextLedger",
        turn_result: TurnResult,
        plan: TurnPlan,
        evaluation: EvaluationResult,
        context_sharing: "ContextSharing | None" = None,
        is_complex_task: bool = False,
    ) -> None:
        """Extract structured conclusions from turn results and write to ledger.

        Each agent result becomes one LedgerEntry with:
        - did_what: extracted from evaluation notes or output tail
        - key_data: extracted data points (URLs, names, numbers, findings)
        - tools_used, success, blockers

        Also persists discussion synthesis (if any) as a special ledger entry
        so that agents in future turns know what was agreed/disagreed.

        FIX A2: Tiered key_data budget — complex tasks get more space.
        FIX B1: Discussion synthesis ALWAYS persists (no boilerplate filter).
        """
        from app.orchestration.context_ledger import LedgerEntry

        # The turn executor wrote an interim DRAFT per agent mid-turn (raw output
        # tail, no truncation manifest). This is the authoritative end-of-turn
        # write: it carries the orchestrator's notes (or a summary-aware
        # extraction) and the search hints. upsert_batch UPGRADES the draft in
        # place, so we build a final entry for EVERY agent — not skip the ones
        # that already have a draft (which discarded the better conclusion).
        entries: list[LedgerEntry] = []
        for result in turn_result.agent_results:
            # FIX A2: Tiered budget for did_what based on task complexity
            _did_what_cap = 1200 if is_complex_task else 800
            did_what = ""
            for ae in evaluation.agent_evaluations:
                if ae.get("agent") == result.agent_name or ae.get("role") == result.agent_role:
                    # smart_truncate (word boundary), not a raw slice: notes is the
                    # orchestrator's conclusion prose → a hard [:cap] cut it mid-word
                    # into the persisted LEDGER (audit tri-model 2026-07-01, H2).
                    from app.orchestration.text_budget import smart_truncate
                    did_what = smart_truncate(ae.get("notes", ""), _did_what_cap)
                    break
            if not did_what and result.output:
                did_what = _extract_did_what(result.output)

            # Extract key_data: important facts/findings from output
            key_data = _extract_key_data(result.output, result.tool_calls)

            # Tools used (unique names)
            tools_used = list(dict.fromkeys(
                tc.tool_name for tc in result.tool_calls if tc.tool_name
            ))[:10]

            # Blockers = crash error (if any) + the agent's STILL-MISSING handoff
            # (W-1/W-2): the outstanding-work note must survive to the next turn, not
            # just a crash string. _merge_into keeps `final.blockers or draft.blockers`
            # so the interim handoff isn't lost if the final has none.
            _err = result.error[:150] if (not result.success and result.error) else ""
            _still = _extract_still_missing(result.output or "")
            blockers = "; ".join(p for p in (_err, _still) if p)

            # Truncation manifest: record topics where full data was cut
            # so future agents know WHAT to search for via activity_search
            truncated_topics = _extract_truncated_topics(result.output, result.tool_calls)

            entries.append(LedgerEntry(
                turn=turn_result.turn_number,
                agent_role=result.agent_role,
                agent_name=result.agent_name,
                did_what=did_what,
                key_data=key_data,
                tools_used=tools_used,
                success=result.success,
                produced_deliverable=getattr(result, "produced_deliverable", False),
                output_chars=len(result.output) if result.output else 0,
                blockers=blockers,
                truncated_topics=truncated_topics,
            ))

        # FIX B1: Discussion synthesis ALWAYS persists to ledger (no boilerplate filter).
        # Storage cost is minimal (1 JSONL line/turn). Filtering at write layer caused
        # team decisions to be lost when agents needed them in future turns.
        if context_sharing:
            synthesis = context_sharing.get_thinking("_discussion_synthesis")
            if synthesis and synthesis.thinking:
                synth_text = synthesis.thinking
                prefix = "[This is an automated summary of the team discussion, not an agent.]\n"
                if synth_text.startswith(prefix):
                    synth_text = synth_text[len(prefix):]
                from app.orchestration.text_budget import smart_truncate as _st
                entries.append(LedgerEntry(
                    turn=turn_result.turn_number,
                    agent_role="_discussion_synthesis",
                    agent_name="Team Consensus",
                    # word-boundary, not a hard cut of a persisted binding agreement (H3)
                    did_what=_st(synth_text, 4000),
                    key_data=[],
                    tools_used=[],
                    success=True,
                    output_chars=len(synth_text),
                ))

        if entries:
            ledger.upsert_batch(entries)

    async def _check_authorization(self, message: str, task_type: str) -> dict[str, Any] | None:
        """Pre-flight authorization gate for live offensive ops (audit C2).

        Returns a terminal result dict (block) if the task is a live OFFENSIVE
        operation against REAL public-routable infrastructure without explicit
        owner opt-in; otherwise None (proceed).

        Offensive-ness is decided by the LLM's ``task_type == "security"`` OR an
        independent format/signal backstop (``detect_offensive_intent``) — so a
        real attack the LLM mislabels "general"/"intelligence" is still gated. A
        safety gate must not depend solely on the classifier it protects against.
        """
        try:
            from app.security.authorization_gate import (
                classify_target,
                detect_offensive_intent,
                evaluate_authorization,
                is_target_authorized,
            )
        except Exception:
            return None  # gate import failure must never wedge normal tasks

        # Primary: LLM task_type. Backstop: independent offensive-intent detection.
        is_offensive = task_type == "security" or detect_offensive_intent(message)
        if not is_offensive:
            return None

        # Authorization is per-target: a target covered by the declared pentest
        # scope (multi-tenant) is authorized even without the deployment-wide flag.
        _tc, _target = classify_target(message)
        decision = evaluate_authorization(
            message=message,
            is_offensive=is_offensive,
            authorized=is_target_authorized(_target),
        )
        if not decision.blocked:
            return None

        logger.warning(
            "authorization_gate_blocked",
            task_id=self._task_id[:8],
            target=decision.target,
            target_class=decision.target_class.value,
        )
        if self._event_callback:
            try:
                await self._event_callback({
                    "event_type": "authorization_required",
                    "task_id": self._task_id,
                    "target": decision.target,
                    "reason": decision.reason,
                })
            except Exception:
                pass
        return {
            "content": (
                "⛔ **Authorization required — task not executed.**\n\n"
                f"{decision.reason}\n\n"
                "No agents were spawned and no commands were run. This safeguard "
                "blocks live offensive actions against real third-party infrastructure "
                "by default. If you own this target or hold written authorization to "
                "test it, set `ALLOW_LIVE_OFFENSIVE=1` and resubmit."
            ),
            "task_id": self._task_id,
            "metadata": {
                "status": "blocked_authorization",
                "target_class": decision.target_class.value,
            },
        }

    def _postprocess_evaluation(
        self,
        turn_result: TurnResult,
        evaluation: EvaluationResult,
        turn_verification: Any = None,
        tautology_scans: dict[str, Any] | None = None,
        independent_review_recent: bool = False,
        hard_ceiling_reached: bool = False,
        reexec_scan: Any = None,
        numeric_scan: Any = None,
        veto_counts: dict[str, dict[str, int]] | None = None,
    ) -> EvaluationResult:
        """Apply deterministic guardrails to the LLM-based evaluation.

        The Orchestrator's evaluation is LLM-generated and can occasionally mark a
        turn/task as "complete" even when the runtime is misconfigured. In our
        test logs, the most common hard failure is missing LLM credentials
        ("Missing credentials ... OPENAI_API_KEY"). In that state, continuing to
        the final report produces low-quality or misleading outputs.

        Anti-hallucination gate: if this turn's ledger verification flagged a
        high-confidence FAILED claim (a "done" assertion the workspace contradicts)
        and the LLM wants to complete, force one more turn to reconcile it.

        Anti-verification-theater gate (audit A3): completion is ALSO blocked when
        (a) a runtime-CONFIRMED self-cancelling check exists in the workspace (its
        "PASS" verifies nothing), or (b) the completion rests on self-attested
        verdicts that no independent agent re-ran. These were previously only
        injected as LLM context (advisory); now they hard-block so a critic must
        re-derive/refute before the task may close.

        Safety: when ``hard_ceiling_reached`` (absolute turn limit or stagnation
        ceiling already hit), the A3 gates do NOT veto completion — the hard
        ceiling must always win, otherwise a task whose critic can never clear the
        gate (e.g. blacklisted) would loop past _MAX_TURNS forever.
        """
        # Verification gate runs first so it can veto an otherwise-complete eval.
        # NOT counted toward veto limits: a workspace-contradicted "done" claim is
        # ground truth, not a self-reinforcing opinion loop.
        try:
            from app.orchestration.ledger_verifier import verification_blocks_completion

            claims_failed = int(getattr(turn_verification, "claims_failed", 0) or 0)
            if (
                evaluation.is_complete
                and verification_blocks_completion(claims_failed)
            ):
                evaluation.is_complete = False
                evaluation.missing_items = list(evaluation.missing_items or [])
                _failed = [
                    r.claim for r in getattr(turn_verification, "results", [])
                    if not getattr(r, "verified", True)
                    and getattr(r, "confidence", 0) >= 0.7
                ][:3]
                evaluation.missing_items.append(
                    "Unverified 'done' claim(s) contradicted by workspace — reconcile before completing: "
                    + ("; ".join(_failed) if _failed else f"{claims_failed} failed verification(s)")
                )
                logger.info(
                    "completion_blocked_failed_verification",
                    task_id=self._task_id[:8],
                    claims_failed=claims_failed,
                )
        except Exception as e:
            logger.debug("verification_gate_failed", error=str(e), task_id=self._task_id[:8])

        # A3-gate 1: a runtime-CONFIRMED tautology voids any "PASS"/"complete".
        # Skipped at the hard ceiling so the absolute turn/stagnation limit wins.
        try:
            from app.orchestration.tautology_detector import (
                confirmed_tautology_lines,
                count_confirmed_tautologies,
            )

            if (
                not hard_ceiling_reached
                and evaluation.is_complete
                and count_confirmed_tautologies(tautology_scans or {}) > 0
                and self._veto_allowed("tautology", veto_counts)
            ):
                _lines = confirmed_tautology_lines(tautology_scans or {})
                evaluation.is_complete = False
                evaluation.missing_items = list(evaluation.missing_items or [])
                evaluation.missing_items.append(
                    "Self-cancelling check(s) PROVEN by re-execution (constant across inputs "
                    "→ verify NOTHING; any 'PASS' resting on them is void): "
                    + "; ".join(_lines)
                    + ". Fix the check and re-derive the result independently before completing."
                )
                if not evaluation.next_turn_focus:
                    evaluation.next_turn_focus = (
                        "[FIX TAUTOLOGY] A DIFFERENT agent (critic) must replace the self-cancelling "
                        "check with one that responds to inputs, then re-derive the headline numbers."
                    )
                logger.info(
                    "completion_blocked_confirmed_tautology",
                    task_id=self._task_id[:8],
                    confirmed=_lines[:3],
                )
        except Exception as e:
            logger.debug("tautology_gate_failed", error=str(e), task_id=self._task_id[:8])

        # A3-gate 2: completion resting on self-attested verdicts nobody re-ran →
        # block one turn for an INDEPENDENT critic re-execution.
        try:
            from app.orchestration.ledger_verifier import completion_needs_independent_review

            _self_attested = int(getattr(turn_verification, "claims_self_attested", 0) or 0)
            if (
                not hard_ceiling_reached
                and completion_needs_independent_review(
                    is_complete=evaluation.is_complete,
                    self_attested_recent=_self_attested,
                    independent_review_recent=independent_review_recent,
                )
                and self._veto_allowed("needs_review", veto_counts)
            ):
                evaluation.is_complete = False
                evaluation.missing_items = list(evaluation.missing_items or [])
                _verdicts = list(getattr(turn_verification, "self_attested", []))[:3]
                evaluation.missing_items.append(
                    "Completion rests on self-attested verdict(s) no independent agent re-ran: "
                    + ("; ".join(_verdicts) if _verdicts else f"{_self_attested} self-graded claim(s)")
                    + ". A DIFFERENT agent must re-execute/re-derive and try to REFUTE them first."
                )
                evaluation.next_turn_focus = (
                    "[INDEPENDENT REVIEW REQUIRED] A critic who did NOT produce this work must "
                    "re-execute/re-derive the self-attested numbers and try to REFUTE them "
                    "(watch for tautologies + back-solved targets) before this task may complete."
                )
                # Ensure the next turn actually brings in an independent critic.
                _assignments = list(evaluation.next_assignments or [])
                if not any(a.get("agent_role") == "critic" for a in _assignments):
                    _assignments.append({
                        "agent_role": "critic",
                        "task": "Independently re-execute and try to refute the self-attested verdicts.",
                        "success_criteria": "Each headline claim is independently re-derived or refuted with evidence.",
                    })
                    evaluation.next_assignments = _assignments
                logger.info(
                    "completion_blocked_needs_independent_review",
                    task_id=self._task_id[:8],
                    self_attested=_self_attested,
                )
        except Exception as e:
            logger.debug("independent_review_gate_failed", error=str(e), task_id=self._task_id[:8])

        # A3-gate 3 (CC-1): an artifact RE-EXECUTED whose real output contradicts
        # the agent's claimed headline numbers voids "complete". Only a genuine
        # mismatch blocks; "unverified" (no sandbox) is surfaced as context, never
        # a blocker. Skipped at the hard ceiling.
        try:
            from app.orchestration.artifact_reexecutor import count_mismatches, mismatch_lines

            if (
                not hard_ceiling_reached
                and evaluation.is_complete
                and count_mismatches(reexec_scan) > 0
                and self._veto_allowed("reexec_mismatch", veto_counts)
            ):
                evaluation.is_complete = False
                evaluation.missing_items = list(evaluation.missing_items or [])
                evaluation.missing_items.extend(mismatch_lines(reexec_scan))
                if not evaluation.next_turn_focus:
                    evaluation.next_turn_focus = (
                        "[ARTIFACT MISMATCH] A re-executed deliverable's real output does NOT "
                        "match the claimed numbers. Reconcile the script with the reported "
                        "figures (fix the script or correct the claim) before completing."
                    )
                logger.info(
                    "completion_blocked_artifact_mismatch",
                    task_id=self._task_id[:8],
                    mismatches=count_mismatches(reexec_scan),
                )
        except Exception as e:
            logger.debug("reexec_gate_failed", error=str(e), task_id=self._task_id[:8])

        # A3-gate 4 (CC-3): unreconciled conflicting numbers (same quantity, ≥2
        # values across artifacts/report) void "complete". Skipped at hard ceiling.
        try:
            from app.orchestration.numeric_integrity import conflict_lines, count_conflicts

            if (
                not hard_ceiling_reached
                and evaluation.is_complete
                and count_conflicts(numeric_scan) > 0
                and self._veto_allowed("numeric_conflict", veto_counts)
            ):
                evaluation.is_complete = False
                evaluation.missing_items = list(evaluation.missing_items or [])
                evaluation.missing_items.extend(conflict_lines(numeric_scan))
                if not evaluation.next_turn_focus:
                    evaluation.next_turn_focus = (
                        "[RECONCILE NUMBERS] The same quantity has ≥2 conflicting values across "
                        "artifacts/report. Pick the script-produced value as source of truth and "
                        "make every mention agree before completing."
                    )
                logger.info(
                    "completion_blocked_numeric_conflict",
                    task_id=self._task_id[:8],
                    conflicts=count_conflicts(numeric_scan),
                )
        except Exception as e:
            logger.debug("numeric_gate_failed", error=str(e), task_id=self._task_id[:8])

        try:
            missing_cred_agents: list[str] = []
            for ar in turn_result.agent_results:
                if ar.success:
                    continue
                text = f"{ar.error}\n{ar.output}"
                if "Missing credentials" in text and ("OPENAI_API_KEY" in text or "api_key" in text):
                    missing_cred_agents.append(ar.agent_name or ar.agent_role or "unknown")

            if missing_cred_agents:
                evaluation.is_complete = False
                evaluation.progress_pct = min(int(evaluation.progress_pct or 0), 90)
                evaluation.missing_items = list(evaluation.missing_items or [])
                evaluation.missing_items.append(
                    "Runtime LLM credentials missing (fix provider API keys). "
                    f"Affected agents: {', '.join(missing_cred_agents[:5])}"
                    + ("..." if len(missing_cred_agents) > 5 else "")
                )
                if not evaluation.next_turn_focus:
                    evaluation.next_turn_focus = "Fix LLM provider credentials and retry failed agents."
        except Exception as e:
            logger.debug("evaluation_postprocess_failed", error=str(e), task_id=self._task_id[:8])

        return evaluation

    def _read_deliverable_text(self, work_dir) -> str:
        """Best-effort read of the canonical deliverable prose for numeric scans.

        Audit f188a4c4 G2: the headline numbers live in the report markdown under
        ``_work/report/`` (e.g. FINAL_DELIVERABLE.md), so passing ``report_text=""``
        made the numeric-integrity scan blind to them. Concatenate the report
        markdown so CC-3 reconciliation sees the prose. Fail-open: any error or no
        report → "" (never crash the completion path). ``run_numeric_integrity``
        also globs other ``*.md`` notes, so this only needs the report dir.
        """
        try:
            from pathlib import Path

            report_dir = Path(work_dir) / "report"
            if not report_dir.exists():
                return ""
            parts: list[str] = []
            for p in sorted(report_dir.glob("*.md")):
                try:
                    if p.is_file() and p.stat().st_size <= 2_000_000:
                        parts.append(p.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
            return "\n\n".join(parts)
        except Exception as e:
            logger.debug("read_deliverable_failed", error=str(e), task_id=self._task_id[:8])
            return ""

    def _veto_allowed(self, kind: str, veto_counts: dict[str, dict[str, int]] | None) -> bool:
        """Count one veto of ``kind`` for this task and report if it may still veto.

        Returns True while the kind is below _MAX_VETOES_PER_KIND (the gate should
        veto as normal). Returns False once the kind has hit its limit — the gate
        must then NOT re-open completion (the loop is self-reinforcing; route to the
        reconciliation turn instead). Reset to empty when progress sets a new HWM
        peak (see Task 6).
        """
        if veto_counts is None:
            return True  # No counter available → don't suppress

        from app.orchestration.termination import veto_limit_reached

        store = veto_counts.setdefault(self._task_id, {})
        store[kind] = store.get(kind, 0) + 1
        if veto_limit_reached(store[kind]):
            logger.warning(
                "veto_limit_reached",
                task_id=self._task_id[:8],
                kind=kind,
                count=store[kind],
            )
            return False
        return True

    def _write_decisions_entry(
        self,
        turn_number: int,
        evaluation: EvaluationResult,
        evolution_insight: "Optional[EvolutionInsight]",
    ) -> None:
        """Write/update DECISIONS.md with orchestrator + evolution assessments.

        Keeps last 3 entries. This file gives both orchestrators a clean record
        of their own prior reasoning without wading through ACTIVITY.md noise.
        """
        import os
        from pathlib import Path

        from app.core import task_paths
        path = task_paths.context_file(self._task_id, "DECISIONS.md")
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build this turn's entry
        lines = [f"## Turn {turn_number}\n"]

        # Orchestrator section. G2-7: distinguish a genuine completion from a forced
        # HALT (budget/stagnation/barren/...) so a stalled run is never labelled
        # "complete" — the terminal reason drives the label.
        decision = format_decision_label(
            evaluation.is_complete, getattr(evaluation, "terminated_reason", "")
        )
        lines.append(f"### Nexus Orchestrator")
        lines.append(f"**Progress:** {evaluation.progress_pct}% | **Decision:** {decision}")
        if evaluation.evaluation_text:
            lines.append(f"**Reasoning:** {evaluation.evaluation_text}")
        if evaluation.agent_evaluations:
            agent_summaries = []
            for ae in evaluation.agent_evaluations[:5]:
                mark = "pass" if ae.get("completed") else "fail"
                agent_summaries.append(
                    f"  - {ae.get('agent', '?')} [{mark}]: {ae.get('notes', ae.get('quality', ''))}"
                )
            lines.append("**Agent results:**\n" + "\n".join(agent_summaries))
        if evaluation.contradictions:
            lines.append(f"**Contradictions:** {'; '.join(evaluation.contradictions[:3])}")
        if evaluation.next_turn_focus:
            focus = evaluation.next_turn_focus
            if "[Evolution:" in focus:
                focus = focus.split("[Evolution:")[0].strip()
            lines.append(f"**Next focus:** {focus}")
        if evaluation.forecast:
            lines.append(f"**Forecast:** {evaluation.forecast}")

        # Evolution section
        if evolution_insight:
            lines.append(f"\n### Evolution Agent Advisory")
            lines.append(
                f"**Severity:** {evolution_insight.severity_score:.1f} | "
                f"**Trajectory:** {evolution_insight.quality_trajectory}"
            )
            if evolution_insight.improvement_suggestions:
                lines.append("**Suggestions:**")
                for s in evolution_insight.improvement_suggestions[:3]:
                    lines.append(f"  - {s}")
            if evolution_insight.blind_spots:
                lines.append("**Blind spots:**")
                for b in evolution_insight.blind_spots[:3]:
                    lines.append(f"  - {b}")
            if evolution_insight.challenge_questions:
                lines.append("**Challenges:**")
                for q in evolution_insight.challenge_questions[:2]:
                    lines.append(f"  - {q}")
        else:
            lines.append(f"\n### Evolution Agent Advisory")
            lines.append("(Not triggered this turn)")

        lines.append("")
        new_entry = "\n".join(lines)

        # Read existing, keep last 2 entries + new = 3 total
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
        except Exception:
            existing = ""

        header = "# Orchestrator & Evolution Decisions\n\n"
        # Split existing entries (each starts with "## Turn")
        import re as _re
        if existing:
            body = existing.replace(header, "", 1)
            # Line-anchored ``^## Turn`` so a ``### Turn N`` quoted inside an
            # entry's reasoning is not mistaken for an entry boundary
            # (task 3c44a93f bug #2 family).
            entries = _re.split(r"(?m)(?=^## Turn \d+)", body)
            entries = [e for e in entries if e.strip()]
        else:
            entries = []

        # Keep last 2 existing + new entry = 3 visible
        entries = entries[-2:]
        entries.append(new_entry)

        path.write_text(header + "\n---\n".join(entries), encoding="utf-8")

    def _build_situational_signals(self, state: ExecutionState, turn_result: "TurnResult") -> str:
        """Build situational awareness context for the Orchestrator.

        Provides factual signals (not decisions) so the Orchestrator can
        make informed choices about continuation vs completion.
        """
        signals = []

        # Progress history signal
        recent_progress = []
        for tr in state.turns_completed[-10:]:
            ev = getattr(tr, "orchestrator_evaluation", None)
            if ev and hasattr(ev, "progress_pct"):
                recent_progress.append(ev.progress_pct)
        if len(recent_progress) >= 4:
            progress_range = max(recent_progress[-5:]) - min(recent_progress[-5:])
            signals.append(
                f"## Progress History (last {len(recent_progress)} turns)\n"
                f"Values: {recent_progress}\n"
                f"Range in last 5: {progress_range}% variance\n"
                f"Total turns completed: {len(state.turns_completed)}"
            )

        # Infrastructure health signal
        infra_streak = getattr(self, "_infra_fail_streak", 0)
        if infra_streak >= 2:
            signals.append(
                f"## Infrastructure Alert\n"
                f"All agents have failed for {infra_streak} CONSECUTIVE turns "
                f"(LLM provider/API issues). No productive work is being done."
            )

        # Repetition signal — detect if same missing items keep recurring
        if len(state.turns_completed) >= 3:
            recent_missing = []
            for tr in state.turns_completed[-5:]:
                ev = getattr(tr, "orchestrator_evaluation", None)
                if ev and hasattr(ev, "missing_items") and ev.missing_items:
                    recent_missing.extend(ev.missing_items[:3])
            if recent_missing:
                from collections import Counter
                repeated = [item for item, count in Counter(recent_missing).items() if count >= 3]
                if repeated:
                    signals.append(
                        f"## Repeated Unresolved Items (appearing 3+ turns in a row)\n"
                        + "\n".join(f"- {item}" for item in repeated[:5])
                    )

        # Agent stuck/force-stopped signal — escalate pattern from agent to pipeline level
        force_stopped = [
            ar for ar in turn_result.agent_results
            if "FORCE-STOPPED" in str(getattr(ar, "output", ""))
        ]
        if force_stopped:
            names = [getattr(ar, "agent_name", "?") for ar in force_stopped]
            signals.append(
                f"## Agents Force-Stopped This Turn\n"
                f"The following agents were killed for being stuck: {', '.join(names)}\n"
                f"Their approaches are exhausted — reassigning the same task will likely loop again."
            )

        if not signals:
            return ""
        return "# SITUATIONAL AWARENESS (factual signals for your decision)\n\n" + "\n\n".join(signals)

    def _calc_stagnation_turns(self, state: ExecutionState, evaluation: EvaluationResult) -> int:
        """Count how many recent turns have had ~same progress (stagnant)."""
        if not state.turns_completed:
            return 0
        recent_progress = []
        for tr in state.turns_completed[-10:]:
            ev = getattr(tr, "orchestrator_evaluation", None)
            if ev and hasattr(ev, "progress_pct"):
                recent_progress.append(ev.progress_pct)
        recent_progress.append(evaluation.progress_pct)
        if len(recent_progress) < 3:
            return 0
        # Count consecutive turns at end where progress barely moved (<=3% range)
        count = 0
        for i in range(len(recent_progress) - 1, 0, -1):
            if abs(recent_progress[i] - recent_progress[i - 1]) <= 3:
                count += 1
            else:
                break
        return count

    def _is_cancelled(self) -> bool:
        """Check cancellation state (in-memory token fast-path).

        The durable DB cancel flag is checked separately at turn boundaries via
        _is_cancelled_db() — this method stays synchronous and cheap for the
        many in-loop call sites.
        """
        if self._cancel_check is None:
            return False
        return self._cancel_check()

    async def _is_cancelled_db(self) -> bool:
        """Backstop: read the durable cancel_requested flag from the task row.

        Covers the race where the Redis cancel message was missed or the
        in-memory token was not yet registered. Called only at turn boundaries
        (already a checkpoint site), so the extra query is negligible.
        """
        try:
            from app.core.dependencies import _session_factory
            if _session_factory is None:
                return False
            from app.models.database import TaskModel
            from sqlalchemy import select as _sel
            import uuid as _uuid
            async with _session_factory() as _db:
                row = await _db.execute(
                    _sel(TaskModel.metadata_).where(
                        TaskModel.id == _uuid.UUID(self._task_id)
                    )
                )
                meta = row.scalar_one_or_none()
                return bool((meta or {}).get("cancel_requested"))
        except Exception:
            return False

    async def _should_cancel(self) -> bool:
        """Unified cancel decision for turn boundaries.

        Combines the in-memory token fast-path with the durable DB-flag
        backstop. When this returns True at a turn boundary the pipeline
        raises asyncio.CancelledError (NOT break) so cancellation routes into
        TaskRunner's existing CancelledError handler — which finalises a clean
        'task_cancelled' immediately, instead of falling through to final-report
        generation and a mislabelled 'task_completed'.
        """
        return self._is_cancelled() or await self._is_cancelled_db()

    async def _save_checkpoint_if_enabled(
        self, state: ExecutionState, reason: str = "turn_complete"
    ) -> None:
        """Save execution checkpoint if feature is enabled. Never raises."""
        try:
            from app.config import get_settings
            settings = get_settings()
            if not settings.enable_task_resume:
                return

            from app.core.dependencies import _session_factory
            if _session_factory is None:
                return

            from app.core.checkpoint_persistence import CheckpointPersistence
            svc = CheckpointPersistence(_session_factory)
            saved = await svc.save_checkpoint(
                task_id=self._task_id,
                turn_number=state.current_turn,
                state=state,
                reason=reason,
            )
            if saved:
                get_background_metrics().record_success("checkpoint_save")
            else:
                get_background_metrics().record_failure(
                    "checkpoint_save", "save_checkpoint returned False"
                )
        except Exception as e:
            logger.warning("checkpoint_save_skipped", error=str(e), task_id=self._task_id[:8])
            get_background_metrics().record_failure("checkpoint_save", str(e))


# Ledger-field extraction lives in app.orchestration.ledger_extract (pure, tested).
# Re-exported here so existing call sites keep working unchanged (audit C1).
from app.orchestration.ledger_extract import (  # noqa: E402
    extract_did_what as _extract_did_what,
    extract_key_data as _extract_key_data,
    extract_still_missing as _extract_still_missing,
    extract_truncated_topics as _extract_truncated_topics,
)


def _enforce_security_plan(
    plan_data: dict[str, Any],
    first_plan: "TurnPlan",
    message: str,
) -> tuple[dict[str, Any], "TurnPlan"]:
    """Enforce security-appropriate team when LLM classified as security but under-planned.

    If task_type is security but plan has fewer than 3 security agents,
    override with a proper security team and multi-phase execution plan.
    """
    from app.orchestration.models import AgentAssignment, TurnPlan
    from app.security.roe_generator import _extract_target

    selected_agents = plan_data.get("selected_agents", [])
    security_roles = {
        "pentester", "recon_specialist", "exploit_developer",
        "vulnerability_researcher", "network_analyst", "forensics_analyst",
        "threat_hunter", "osint_collector",
    }
    current_security_count = sum(
        1 for a in selected_agents if a.get("role", "") in security_roles
    )

    if current_security_count >= 3:
        return plan_data, first_plan

    target = _extract_target(message)

    plan_data["task_type"] = "security"
    plan_data["complexity"] = "expert"
    plan_data["selected_agents"] = [
        {"role": "recon_specialist", "name": "Recon Specialist", "primary_responsibility": "Full attack surface mapping and technology fingerprinting"},
        {"role": "pentester", "name": "Penetration Tester", "primary_responsibility": "Vulnerability identification and exploitation"},
        {"role": "exploit_developer", "name": "Exploit Engineer", "primary_responsibility": "PoC development and exploitation chain construction"},
        {"role": "vulnerability_researcher", "name": "Vuln Researcher", "primary_responsibility": "CVE research and vulnerability analysis"},
        {"role": "network_analyst", "name": "Network Analyst", "primary_responsibility": "Infrastructure analysis and lateral movement"},
    ]

    plan_data["execution_plan"] = [
        {
            "turn": 1,
            "objective": f"Full reconnaissance of {target}",
            "milestone": "Complete attack surface map: subdomains, tech stack, open ports, authentication flows",
            "execution_mode": "sequential",
            "assignments": [
                {
                    "agent_role": "recon_specialist",
                    "task": f"Perform comprehensive reconnaissance on {target}. "
                            "Start with passive (WHOIS, DNS, cert transparency, Google dorks, Shodan) "
                            "then active (port scan, service detection, web crawling, directory enumeration). "
                            "Identify ALL subdomains, technology stack, infrastructure, and potential entry points.",
                    "success_criteria": "Full target profile: IPs, subdomains, tech stack, open ports, entry points ranked by exploitation likelihood",
                    "dependencies": [],
                    "tools_hint": ["web_search", "http_request", "run_command"],
                },
            ],
        },
        {
            "turn": 2,
            "objective": "Vulnerability analysis across all discovered attack surfaces",
            "milestone": "At least 3 potential vulnerabilities identified with confidence grades",
            "execution_mode": "sequential",
            "assignments": [
                {
                    "agent_role": "vulnerability_researcher",
                    "task": "Based on recon findings, research known CVEs for detected software versions. "
                            "Check OWASP Top 10 vectors: SQLi, XSS, SSRF, SSTI, file upload, auth bypass. "
                            "Test each with minimal payloads to confirm exploitability.",
                    "success_criteria": "Vulnerability list with confirmed/probable/possible grades and exploitation paths",
                    "dependencies": ["recon_specialist"],
                    "tools_hint": ["web_search", "http_request", "run_command"],
                },
                {
                    "agent_role": "pentester",
                    "task": "Actively test authentication flows, input validation, and access controls. "
                            "Try default credentials, parameter tampering, privilege escalation paths. "
                            "Focus on vectors most likely to achieve the engagement goal.",
                    "success_criteria": "Confirmed exploitable vulnerabilities with PoC evidence",
                    "dependencies": ["recon_specialist"],
                    "tools_hint": ["http_request", "run_command"],
                },
            ],
        },
        {
            "turn": 3,
            "objective": "Exploitation — achieve engagement objective",
            "milestone": "Working exploit chain with proof of impact",
            "execution_mode": "sequential",
            "assignments": [
                {
                    "agent_role": "exploit_developer",
                    "task": "Build and execute exploitation chain targeting the highest-confidence vulnerability. "
                            "Develop working PoC, test reliability, achieve the engagement objective. "
                            "If first vector fails, pivot to next candidate.",
                    "success_criteria": "Proof of exploitation: command execution output, file access, or shell access",
                    "dependencies": ["vulnerability_researcher", "pentester"],
                    "tools_hint": ["http_request", "run_command"],
                },
            ],
        },
        {
            "turn": 4,
            "objective": "Post-exploitation and comprehensive reporting",
            "milestone": "Full engagement report with all findings, evidence, and recommendations",
            "execution_mode": "sequential",
            "assignments": [
                {
                    "agent_role": "network_analyst",
                    "task": "If access achieved: enumerate internal system (OS, users, services, network, credentials). "
                            "If no access: document all attempted vectors and their failure modes. "
                            "Either way: produce comprehensive technical report.",
                    "success_criteria": "Complete engagement report: all findings + evidence + impact assessment + remediation",
                    "dependencies": ["exploit_developer"],
                    "tools_hint": ["run_command", "http_request"],
                },
            ],
        },
    ]

    from app.orchestration.nexus_orchestrator import _convert_to_turn_plan
    new_first_plan = _convert_to_turn_plan(plan_data["execution_plan"][0], turn_number=1)

    logger.info(
        "security_plan_enforced",
        target=target,
        original_agents=len(selected_agents),
        enforced_agents=len(plan_data["selected_agents"]),
    )

    return plan_data, new_first_plan


def build_agent_catalog_text() -> str:
    """Build the agent catalog text for Orchestrator's planning prompt.

    Reads from AGENT_TEMPLATES and formats as a text catalog.
    """
    try:
        from app.services.agent_spawner import AGENT_TEMPLATES
    except ImportError:
        return "(Agent catalog unavailable)"

    lines: list[str] = []
    for role, template in AGENT_TEMPLATES.items():
        avatar = template.get("avatar", "🤖")
        name = template.get("default_name", role)
        desc = template.get("description", "")
        expertise = template.get("expertise", [])
        expertise_str = ", ".join(expertise[:6]) if expertise else ""

        lines.append(f"- **{role}** {avatar} ({name}): {desc}")
        if expertise_str:
            lines.append(f"  Expertise: {expertise_str}")

    return "\n".join(lines)
