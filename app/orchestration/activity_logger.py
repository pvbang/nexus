"""
MekongAI Nexus - Activity Logger.

Full fidelity logging - never lose information.

Append-only logger that writes ALL execution details to ACTIVITY.md.
Never truncates — only appends. This is the single source of truth
for everything that happened during task execution.

IMPROVEMENT: No truncation anywhere. Tool output, agent output, tool input —
everything is recorded in full. ACTIVITY.md is the ground truth and must
contain 100% of all information produced during execution. Even if a tool
returns 100KB of output, it gets logged completely. Storage is cheap;
lost information is irreplaceable.

CONTEXT MANAGEMENT STRATEGY:
- File storage (this module) = FULL fidelity, no limits whatsoever.
- Agent LLM context (agent_worker.py) = Separate concern, capped for context window.
- These are deliberately different: ACTIVITY.md is permanent record,
  LLM messages are ephemeral working memory with finite capacity.
- FINAL_REPORT.md reads from ACTIVITY.md (full data), not from LLM context.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import structlog

from app.orchestration.models import (
    AgentResult,
    AgentThinking,
    EvaluationResult,
    ToolCall,
    TurnPlan,
    TurnResult,
)

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
# Optional truncation for readability. Default: no truncation (full fidelity).
# Set ACTIVITY_TOOL_OUTPUT_MAX_CHARS to a positive int to enable truncation.
try:
    _ACTIVITY_TOOL_OUTPUT_MAX_CHARS = int(os.environ.get("ACTIVITY_TOOL_OUTPUT_MAX_CHARS", "0") or "0")
except Exception:
    _ACTIVITY_TOOL_OUTPUT_MAX_CHARS = 0


def _safe_code_fence(text: str, *, lang: str = "") -> str:
    """Wrap text in a markdown code fence that won't be broken by embedded backticks."""
    t = text or ""
    # Indent content so accidental markdown headers inside tool output don't match
    # our ACTIVITY.md section regexes (which are anchored at start-of-line).
    t = "\n".join(" " + ln for ln in t.splitlines())
    runs = re.findall(r"`+", t)
    max_run = max((len(r) for r in runs), default=0)
    fence_len = max(3, max_run + 1)
    fence = "`" * fence_len
    info = lang.strip()
    header = f"{fence}{info}\n" if info else f"{fence}\n"
    return f"{header}{t}\n{fence}\n"


class ActivityLogger:
    """Append-only activity logger to ACTIVITY.md.

    CRITICAL INVARIANT: This file is NEVER truncated or overwritten.
    Every call appends content. The file grows monotonically.

    IMPROVEMENT: Full fidelity logging - never lose information.
    All tool inputs, tool outputs, and agent outputs are logged in their
    ENTIRETY regardless of size. This file is the source of truth.
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._context_dir = task_paths.context_dir(task_id)
        self._file_path = self._context_dir / "ACTIVITY.md"
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_header(self) -> None:
        """Write header if file doesn't exist yet."""
        if self._initialized:
            return
        if not self._file_path.exists():
            now = datetime.now(timezone(timedelta(hours=7)))
            header = (
                "# Task Activity Log\n\n"
                f"**Task ID**: `{self._task_id[:8]}`\n"
                f"**Started**: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)\n\n"
                "---\n\n"
            )
            await self._append(header)
        self._initialized = True

    _MAX_ACTIVITY_SIZE = 500_000  # 500KB — rotate to prevent unbounded growth

    async def _append(self, content: str) -> None:
        """Append content to ACTIVITY.md (thread-safe).

        Rotates file when exceeding _MAX_ACTIVITY_SIZE to prevent multi-MB files
        that cannot be read by agents. Archived files remain for audit purposes.
        """
        # CC-6: scrub secrets at the durable write boundary. ACTIVITY.md is on
        # disk and cannot be redacted after the fact — every append funnels
        # through here, so this is the single chokepoint that keeps credentials
        # (incl. dumped `env`) out of the persistent log.
        try:
            from app.core.secret_redaction import redact_secrets
            content = redact_secrets(content)
        except Exception:
            pass  # never let redaction failure drop the log line entirely

        async with self._lock:
            try:
                # Check rotation need
                if self._file_path.exists() and self._file_path.stat().st_size > self._MAX_ACTIVITY_SIZE:
                    # Archive: rename to ACTIVITY_epoch{N}.md
                    import glob
                    existing = list(self._context_dir.glob("ACTIVITY_epoch*.md"))
                    epoch_num = len(existing) + 1
                    archive_path = self._context_dir / f"ACTIVITY_epoch{epoch_num}.md"
                    self._file_path.rename(archive_path)
                    logger.info("activity_log_rotated", archive=str(archive_path), epoch=epoch_num)
                    # Write continuation header
                    with open(self._file_path, "w", encoding="utf-8") as f:
                        f.write(f"# Task Activity Log (continued from epoch {epoch_num})\n\n---\n\n")

                with open(self._file_path, "a", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                logger.warning("activity_logger_write_failed", error=str(e))

    async def log_turn_start(self, turn_number: int, plan: TurnPlan) -> None:
        """Log the beginning of a new turn with its plan."""
        await self._ensure_header()
        now = datetime.now(timezone(timedelta(hours=7)))

        assignments_text = ""
        for a in plan.assignments:
            # Strip injected boilerplate (constraints, DO NOT REDO hints) from logged text.
            # These are repeated every turn for every agent and bloat ACTIVITY.md enormously.
            task_for_log = a.task_description
            for marker in ("[DESIGN CONSTRAINTS", "[DO NOT REDO", "[AGENT RELIABILITY WARNING"):
                idx = task_for_log.find(marker)
                if idx > 0:
                    task_for_log = task_for_log[:idx].rstrip()
            assignments_text += (
                f"  - **{a.agent_name}** ({a.agent_role}): {task_for_log}\n"
                f"    - Success criteria: {a.success_criteria}\n"
            )
            if a.dependencies:
                assignments_text += f"    - Dependencies: {', '.join(a.dependencies)}\n"

        content = (
            f"## Turn {turn_number}\n"
            f"**Timestamp**: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)\n"
            f"**Objective**: {plan.objective}\n"
            f"**Milestone**: {plan.milestone}\n"
            f"**Execution Mode**: {plan.execution_mode}\n\n"
            f"### Orchestrator Plan\n"
            f"Assignments:\n{assignments_text}\n"
        )
        await self._append(content)

    async def log_agent_thinking(self, agent_name: str, thinking: AgentThinking) -> None:
        """Log an agent's thinking output."""
        content = f"### Agent: {agent_name} ({thinking.agent_role})\n"
        content += f"#### Thinking\n{_safe_code_fence(thinking.thinking)}\n"

        if thinking.challenges:
            content += "#### Challenges\n"
            for c in thinking.challenges:
                content += f"- {c}\n"
            content += "\n"

        if thinking.supports:
            content += "#### Supports\n"
            for s in thinking.supports:
                content += f"- {s}\n"
            content += "\n"

        await self._append(content)

    async def log_agent_discussion(self, agent_name: str, thinking: AgentThinking) -> None:
        """Log an agent's discussion response (challenges/supports after seeing others)."""
        content = f"#### Discussion Response ({agent_name})\n"
        if thinking.challenges:
            for c in thinking.challenges:
                content += f"- ⚡ Challenge: {c}\n"
        if thinking.supports:
            for s in thinking.supports:
                content += f"- ✅ Support: {s}\n"
        if not thinking.challenges and not thinking.supports:
            content += "- (No challenges or supports — agrees with team direction)\n"
        content += "\n"
        await self._append(content)

    async def log_agent_execution(
        self,
        agent_name: str,
        agent_role: str,
        tool_calls: list[ToolCall],
        output: str,
    ) -> None:
        """Log an agent's tool calls and final output.

        Full-fidelity logging by default (no truncation). Optional tool output
        truncation can be enabled via ACTIVITY_TOOL_OUTPUT_MAX_CHARS for
        readability in extremely noisy runs.
        """
        content = f"#### Tool Calls ({agent_name})\n"

        if tool_calls:
            import json as _json
            for i, tc in enumerate(tool_calls, 1):
                status = "✅" if tc.success else "❌"
                # BUG-3 FIX: Use json.dumps for proper JSON formatting instead of
                # Python str() repr which produces {'key': 'value'} syntax.
                try:
                    args_str = _json.dumps(tc.arguments, ensure_ascii=False, default=str)
                except Exception:
                    args_str = str(tc.arguments)
                output_text = tc.output or ""
                if _ACTIVITY_TOOL_OUTPUT_MAX_CHARS > 0 and len(output_text) > _ACTIVITY_TOOL_OUTPUT_MAX_CHARS:
                    output_text = (
                        output_text[:_ACTIVITY_TOOL_OUTPUT_MAX_CHARS]
                        + f"\n... [truncated, {len(tc.output)} chars total]"
                    )
                exec_ms = tc.execution_time_ms if isinstance(tc.execution_time_ms, (int, float)) else 0
                content += (
                    f"{i}. {status} `{tc.tool_name}` ({exec_ms:.0f}ms)\n"
                    f"   **Input**:\n{_safe_code_fence(args_str, lang='json')}"
                    f"   **Output**:\n{_safe_code_fence(output_text)}\n"
                )
        else:
            content += "(No tool calls made)\n\n"

        # IMPROVEMENT: Full agent output — no truncation whatsoever.
        content += f"#### Output ({agent_name})\n{_safe_code_fence(output)}\n"
        await self._append(content)

    async def log_orchestrator_evaluation(self, evaluation: EvaluationResult) -> None:
        """Log the Orchestrator's evaluation of the turn."""
        content = (
            f"### Orchestrator Evaluation\n"
            f"**Progress**: {evaluation.progress_pct}%\n"
            f"**Decision**: {'✅ COMPLETE' if evaluation.is_complete else '🔄 CONTINUE'}\n\n"
        )

        if evaluation.agent_evaluations:
            content += "**Agent Assessments**:\n"
            for ae in evaluation.agent_evaluations:
                status = "✅" if ae.get("completed") else "❌"
                content += f"- {status} {ae.get('agent', '')}: {ae.get('notes', '')}\n"
            content += "\n"

        if evaluation.missing_items:
            content += "**Still Missing**:\n"
            for m in evaluation.missing_items:
                content += f"- {m}\n"
            content += "\n"

        if evaluation.contradictions:
            content += "**Contradictions Found**:\n"
            for c in evaluation.contradictions:
                content += f"- ⚠️ {c}\n"
            content += "\n"

        if not evaluation.is_complete and evaluation.next_turn_focus:
            content += f"**Next Turn Focus**: {evaluation.next_turn_focus}\n\n"

        content += "---\n\n"
        await self._append(content)

    async def log_context_handoff(
        self,
        direction: str,
        counterpart_task_id: str,
        context_keys: list[str],
        context_sizes: dict[str, int],
        goal: str = "",
    ) -> None:
        """Log context transfer between parent task and subtask.

        Args:
            direction: "SENT_TO_SUBTASK" or "RECEIVED_FROM_SUBTASK"
            counterpart_task_id: The other task's ID (subtask or parent)
            context_keys: Which context docs were transferred (e.g. ["task_brief", "todo", "activity_tail"])
            context_sizes: Size in bytes of each transferred context piece
            goal: The subtask's goal/description
        """
        await self._ensure_header()
        now = datetime.now(timezone(timedelta(hours=7)))

        arrow = "→" if "SENT" in direction else "←"
        emoji = "📤" if "SENT" in direction else "📥"

        sizes_text = ", ".join(f"{k}: {v:,}B" for k, v in context_sizes.items() if v > 0)

        content = (
            f"### {emoji} Context Handoff ({direction})\n"
            f"**Timestamp**: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)\n"
            f"**Direction**: This task {arrow} `{counterpart_task_id[:8]}`\n"
            f"**Context transferred**: {', '.join(context_keys)}\n"
            f"**Sizes**: {sizes_text}\n"
        )
        if goal:
            content += f"**Subtask goal**: {goal[:300]}\n"
        content += "\n"

        await self._append(content)

    async def log_audit_transaction(
        self,
        action: str,
        target: str,
        agent_name: str = "",
        details: str = "",
        evidence: str = "",
        severity: str = "",
        category: str = "",
        unsubstantiated: bool = False,
    ) -> None:
        """Log an audit transaction — any significant action taken during investigation.

        Used by security/intelligence agents to create an immutable audit trail of:
        - Scans performed, queries executed, evidence collected
        - Financial transactions traced, wallets identified
        - IOCs validated, findings confirmed/refuted
        - Any action that changes state or produces evidence

        Args:
            action: What was done (e.g. "port_scan", "wallet_trace", "cve_verify")
            target: What it was performed against (e.g. IP, domain, wallet address)
            agent_name: Which agent performed it
            details: Full description of the action and parameters
            evidence: Key evidence/output produced (hash, finding, etc.)
            severity: Severity if applicable (critical/high/medium/low/info)
            category: Category (recon/exploit/forensics/finint/osint/network/malware)
        """
        await self._ensure_header()
        now = datetime.now(timezone(timedelta(hours=7)))

        severity_badge = ""
        if severity:
            sev_map = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
            severity_badge = f" {sev_map.get(severity.lower(), '⚪')} [{severity.upper()}]"

        content = (
            f"### 📋 AUDIT TRANSACTION{severity_badge}\n"
            f"**Timestamp**: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+7)\n"
            f"**Action**: `{action}`\n"
            f"**Target**: `{target}`\n"
        )
        if agent_name:
            content += f"**Agent**: {agent_name}\n"
        if category:
            content += f"**Category**: {category}\n"
        if details:
            content += f"**Details**:\n{_safe_code_fence(details)}"
        if evidence:
            content += f"**Evidence**:\n{_safe_code_fence(evidence)}"
        if unsubstantiated:
            content += (
                "> ⚠ **UNSUBSTANTIATED** — a finding of this severity was logged with NO "
                "evidence. Treat as an unproven claim until re-logged with concrete evidence.\n"
            )
        content += "\n"

        await self._append(content)

    async def log_task_complete(
        self, is_complete: bool = True, terminated_reason: str = ""
    ) -> None:
        """Log the task's terminal marker, reflecting the REAL terminal state.

        Audit S4: this used to stamp "✅ Task Completed" unconditionally, so an
        honest halt (TODO.md wrote "⚠ HALTED") or a loop that stopped with the
        decision still 'continue' was mislabelled as a success. The marker now
        distinguishes three terminal states so ACTIVITY.md agrees with TODO.md:
          - genuine completion           → ✅ Task Completed
          - honest halt (has a reason)   → ⚠ Task Halted (reason: …)
          - stopped, not complete, no reason (budget/turn exhaustion with
            decision still 'continue') → ⏳ Task Stopped (incomplete)
        """
        now = datetime.now(timezone(timedelta(hours=7)))
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        reason = (terminated_reason or "").strip()
        if reason:
            header = f"## ⚠ Task Halted (reason: {reason})"
        elif is_complete:
            header = "## ✅ Task Completed"
        else:
            header = "## ⏳ Task Stopped (incomplete — loop ended without completion)"
        await self._append(f"{header}\n**Finished**: {ts} (UTC+7)\n\n")
