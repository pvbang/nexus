"""
Nexus - Agent Worker.

Individual agent that receives an assignment, thinks about it,
discusses with peers, and executes using tools.
Uses a simplified tool-calling loop (reuses existing ToolExecutor).

Agents have unlimited tool rounds — they run until the LLM decides
it has completed the task (no more tool calls). Only external controls:
cancellation, per-tool timeout, and orchestrator turn limits.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

from app.core.tool_policy import AgentToolPolicy
from app.core.self_reflection import SelfReflectionEngine, ReflectionContext, inject_reflection, is_self_reflection_enabled
from app.orchestration.failure_breaker import (
    MAX_CONSECUTIVE_FAILED_ROUNDS,
    update_failed_round_streak,
)
from app.orchestration.json_parse import parse_json_response as _parse_json_response  # noqa: F401 (re-exported)
from app.orchestration.message_repair import fix_orphan_tool_use
from app.orchestration.models import AgentAssignment, AgentResult, AgentThinking, ToolCall
from app.orchestration.stuck_detector import detect_stuck
from app.orchestration.prompts import (
    build_agent_execution_prompt,
    build_agent_thinking_prompt,
)
from app.services.prompted_tools import strip_tool_call_markup
from app.services.safety_finish_reason import SAFETY_FINISH_REASONS
from app.services.toolcall_recovery import recover_tool_calls

logger = structlog.get_logger(__name__)


def _format_discussion_item(item: Any) -> str:
    """Format a discussion challenge/support item as readable text.

    LLM may return items as dicts with {to, topic, detail} or plain strings.
    This ensures they always render as readable text, not raw Python repr.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        to = item.get("to", "")
        topic = item.get("topic", "")
        detail = item.get("detail", "")
        message = item.get("message", "")
        if to and topic:
            return f"→ @{to} [{topic}]: {detail}" if detail else f"→ @{to} [{topic}]"
        if message:
            return message
        if detail:
            return detail
        # Fallback: join all values
        parts = [f"{k}: {v}" for k, v in item.items() if v]
        return " | ".join(parts) if parts else str(item)
    return str(item)

# No artificial limit on tool rounds. Agents run until they're done.
# The only controls are: (1) agent decides to stop (no more tool calls),
# (2) cancellation token, (3) per-tool timeout, (4) orchestrator turn limits.
# Safety ceiling: prevents infinite loops from bugs (NOT a flow control mechanism).
_TOOL_ROUND_SAFETY_CEILING = 100000
# Fallback only — the live per-tool timeout comes from Settings.tool_timeout
# (M3: single source of truth). Used if settings can't be loaded in __init__.
_TOOL_TIMEOUT_SECONDS = 8000
_MAX_TIMEOUT_RETRIES = 3

# CONTEXT MANAGEMENT: Tool output cap for LLM messages.
# Full output is always saved in the ToolCall record (→ ACTIVITY.md).
# But the version sent into LLM messages is capped to prevent context overflow.
# 20KB is generous enough for most tool results while protecting the context window.
# Fallback only — live value comes from Settings.agent_tool_output_cap_chars (M3).
_TOOL_OUTPUT_CAP_CHARS = 20000

# Smart context compression: when CONVERSATION messages (excluding system prompt)
# exceed this char threshold, older tool results are compressed into summaries.
# System prompt is NEVER truncated — it contains agent identity and instructions.
# This threshold applies only to user/assistant/tool messages after the system prompt.
# NOTE: System prompt can be 20-40K (ROE, workspace, tools). Total model budget is
# ~128K chars. Conversation threshold must leave room for system prompt.
_CONTEXT_COMPRESS_THRESHOLD_CHARS = 60000  # fallback only — live value from Settings.context_compress_threshold_chars (M3)
_CONTEXT_KEEP_RECENT_MESSAGES = 20  # Keep last N messages uncompressed (covers ~5-6 tool rounds)
_MAX_PRIORITY_PROTECTED = 5  # Max additional messages to protect based on relevance


def _estimate_chars_per_token(content_sample: str) -> float:
    """Estimate chars-per-token ratio based on content character distribution.

    Latin text averages ~4 chars/token, but CJK/emoji characters are 1-2 tokens
    each. This detects non-ASCII-heavy content and returns a lower ratio so the
    compression threshold triggers earlier, preventing context overflow.
    """
    if not content_sample:
        return 4.0

    sample = content_sample[:2000]
    non_ascii = sum(1 for c in sample if ord(c) > 127)
    ratio = non_ascii / len(sample) if sample else 0

    if ratio > 0.5:
        return 2.0
    elif ratio > 0.3:
        return 2.5
    elif ratio > 0.15:
        return 3.0
    return 4.0


def _score_message_relevance(msg: dict[str, Any], assignment_keywords: list[str]) -> float:
    """Score a message's importance for context retention.

    Higher score = more likely to contain critical findings worth preserving.
    """
    content = msg.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    if not content or len(content) < 50:
        return 0.0

    score = 0.0
    content_lower = content.lower()

    if any(marker in content for marker in ("http://", "https://", "```")):
        score += 0.3
    if any(marker in content_lower for marker in ("found", "confirmed", "discovered", "result:")):
        score += 0.2
    if any(marker in content for marker in ("{", "[")):
        score += 0.15
    # I-2 (context-arch audit): a COMPUTED-NUMBER fact (CGE=62.93%, closure 4.2%,
    # net 20 kW, d_throat=115.8 mm) is often the single most important thing to keep,
    # yet it carries no assignment keyword. Reward an assignment "= <num>" or a
    # number+unit so a tool result full of figures is protected, not evicted.
    if re.search(
        r"[A-Za-z_)\]%]\s*[=:]\s*-?\d|"
        r"\d[\d.,]*\s*(?:%|kW|kWe|kWh|MW|MJ|kJ|GJ|mm|cm|°C|Nm3|kg|USD|VND|MPa|bar|Hz)\b",
        content, re.IGNORECASE,
    ):
        score += 0.25

    if assignment_keywords:
        hits = sum(1 for kw in assignment_keywords if kw in content_lower)
        score += min(0.3, hits * 0.1)

    if content_lower.startswith(("i'll ", "let me ", "now i'll ", "okay,")):
        score -= 0.1
    if "error" in content_lower and "traceback" in content_lower:
        score -= 0.15

    return max(0.0, min(1.0, score))


# Stuck escalation: force-stop agent after this many consecutive stuck detections.
# Increased from 5 to 8: agents that genuinely iterate (different payloads,
# different approaches) need room to try. The stuck detector itself differentiates
# "same action repeated" from "different attempts at same goal."
MAX_STUCK_ESCALATIONS = 8

# Default max rounds for roles not found in AgentToolPolicy._ROLE_MAX_ROUNDS
_DEFAULT_ROLE_MAX_ROUNDS = 500

# Tools that PRODUCE a deliverable (write to disk / act on the world), as opposed
# to read/analysis tools. Single source of truth for the EDP output-first nudge
# and the F2 phantom-success guard.
_PRODUCE_TOOLS = frozenset({
    "write_file", "edit_file", "execute_code", "execute_command",
    "http_request", "audit_log", "task_doc_write",
})

# Tools that actually WRITE A DELIVERABLE FILE to disk. Distinct from _PRODUCE_TOOLS
# (which also counts http_request/execute_code/audit_log — real work, but NOT a file
# write). R2/P100-1 (tri-run round-2): the 5641 pentester ran http_request+execute_code
# +audit_log successfully (so _did_produce_deliverable=True) while NEVER calling
# write_file for the assigned evidence file — the deliverable never reached disk, yet
# the phantom guard (keyed on _produced_ok) did not fire. When the assignment demands
# a NAMED FILE, "produced something" is not enough — a FILE must have been written.
_WRITE_FILE_TOOLS = frozenset({"write_file", "edit_file", "task_doc_write"})


# File-writing code idioms (audit S1, 2026-06-30). A turn that pastes file-writing
# CODE inside a fenced block but never dispatches a tool to run it has "produced"
# nothing — yet without this signal the phantom-produce guard cannot tell it apart
# from a legitimate analysis turn. Matched STRUCTURALLY (a code idiom inside a ```
# fence), never via prose keywords ("I wrote the file" stays unflagged): keyword-
# prose matching is forbidden and would fail legitimate wrap-up/analysis turns.
_FILE_WRITE_CODE_PATTERNS = (
    re.compile(r"""open\s*\([^)]*,\s*['"][wax]b?\+?['"]""", re.IGNORECASE),
    re.compile(r"""\.write_text\s*\(""", re.IGNORECASE),
    re.compile(r"""json\.dump\s*\(\s*[^,]+,\s*\w+""", re.IGNORECASE),
)


def _describes_unexecuted_file_write(content: str) -> bool:
    """Whether ``content`` PASTES file-writing code in a fenced block but did not
    run it (the S1 phantom-write shape: code-in-prose, zero produce-tool calls).

    Structural, not prose-based: we only inspect text INSIDE Markdown ``` fences
    for a file-writing idiom (``open(p,"w")`` / ``.write_text(`` / ``json.dump(x, f``).
    A turn that merely SAYS it wrote a file, with no such code idiom, returns False
    by design — analysis/wrap-up turns must stay valid (phantom-write doctrine).
    """
    if not content:
        return False
    # Collect the text inside line-leading ``` fences only (so a backtick in prose
    # never opens a "fence"). Pair fences; an unclosed final fence still counts to
    # end-of-text so a glued/ragged close cannot hide a pasted script.
    segments: list[str] = []
    in_fence = False
    seg_start = 0
    for m in re.finditer(r"(?m)^[ \t]*```", content):
        if not in_fence:
            in_fence = True
            seg_start = m.end()
        else:
            segments.append(content[seg_start:m.start()])
            in_fence = False
    if in_fence:
        segments.append(content[seg_start:])
    fenced = "\n".join(segments)
    if not fenced:
        return False
    return any(p.search(fenced) for p in _FILE_WRITE_CODE_PATTERNS)


# A fabricated tool RESPONSE the model authored itself (E8, run 3a75a0db). The
# system never emits <tool_response> into a model's own content, so any such
# block is machinery the model invented to fake execution ("File written ✅").
_FABRICATED_TOOL_RESPONSE_RE = re.compile(
    r"<tool_response>.*?</tool_response\s*>", re.DOTALL | re.IGNORECASE
)

# P0-2 (tri-run round-2, task-03ce93bd): tool-call MARKUP a model emitted that the
# parser could not turn into an actionable call. When content carries one of these
# AND zero calls were parsed/recovered, the turn is a SILENT parse-drop — the model
# tried to call a tool but the runtime saw nothing — not a final answer. Surfacing
# it (log + feed back) stops the orchestrator misdiagnosing 6 turns of "shell/env
# broke" when the real cause is an unparsed tool-call template. These are TAGS, so a
# prose sentence merely discussing "a tool call" does not match.
_TOOLCALL_MARKUP_MARKERS = (
    "<tool_call>", "</tool_call>", "<arg_key>", "<arg_value>",
    "<invoke", "<function_calls>", "```tool_call",
    "<｜tool▁call", "<｜tool▁calls▁begin｜>", "<|tool_call|>", "[tool_call]",
)


def content_has_unparsed_toolcall_markup(content: str | None) -> bool:
    """Whether ``content`` contains tool-call markup (a TAG, not prose mentioning it).

    Used only when zero calls were parsed AND recovered: a match then means the model
    tried to call a tool in a template the parser didn't read (P0-2 parse-drop),
    which must be surfaced rather than treated as a completed turn.
    """
    if not content:
        return False
    low = content.lower()
    return any(m.lower() in low for m in _TOOLCALL_MARKUP_MARKERS)


# E-R7 (tri-run audit 2026-07-02): an assignment that names a concrete FILE to WRITE
# (create-verb + filename.ext) but lands zero write is a phantom prose-write — the
# Opus run composed a 67K-char report INLINE and never called write_file. Detection
# is STRUCTURAL: a write/create verb near a filename WITH AN EXTENSION. A pure
# analysis/review assignment ("analyze the data", "review the design"), a
# write-verb with no file target ("write up your analysis"), or a filename merely to
# READ ("read results.json and summarize") is NOT tripped — those are prose
# deliverables that must stay valid (phantom-write doctrine).
_WRITE_VERB = (
    r"writ\w*|sav\w*|creat\w*|generat\w*|produc\w*|export\w*|output|emit|"
    r"tạo|ghi|xuất|lưu|sinh|biên soạn|soạn"
)
# filename with a real deliverable extension (kept explicit so a bare word.token
# — e.g. "node.js" mentioned in prose — doesn't masquerade as a file target).
_DELIVERABLE_FILE_RE = (
    r"[\w./-]+\.(?:md|csv|json|py|txt|docx?|pdf|xlsx?|dxf|step|stl|svg|png|html?|"
    r"ya?ml|toml|ini|sql|ipynb|tsv)\b"
)
# Two orderings both mean "write this file":
#   (1) verb THEN target — "save results.json", "write report.md"
#   (2) target THEN passive verb — "FINAL-REPORT.md is written", "bom.csv được tạo"
# Split so "read results.json and summarize" (READ verb before the file) never
# matches, while both write orderings do.
_DEMANDS_WRITE_RES = (
    re.compile(
        rf"\b(?:{_WRITE_VERB})\b[\w\s,'\"`()/-]{{0,40}}?{_DELIVERABLE_FILE_RE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_DELIVERABLE_FILE_RE}[\w\s,'\"`()/-]{{0,25}}?\b(?:{_WRITE_VERB})\b",
        re.IGNORECASE,
    ),
)


def _assignment_demands_file_write(task: str | None, success_criteria: str | None) -> bool:
    """Whether the assignment requires writing a NAMED file (create-verb + file.ext).

    Structural, not a length/keyword heuristic: it needs a write/create verb within a
    short window of a filename WITH a deliverable extension (verb-then-file OR
    file-then-passive-verb). So a review/analysis assignment, a verb-without-a-file
    ("write up your analysis"), or a file mentioned only to READ ("read results.json
    and summarize") returns False — those legitimately produce prose, not a file.
    """
    combined = f"{task or ''}\n{success_criteria or ''}"
    if not combined.strip():
        return False
    return any(r.search(combined) for r in _DEMANDS_WRITE_RES)


def _sanitize_output_for_context(content: Optional[str]) -> Optional[str]:
    """Strip fabricated ``<tool_response>`` blocks from persisted agent output.

    The output is persisted as ``AgentResult.output`` and flows into downstream
    context (pipeline synthesis, did_what extraction, fact collection). A leaked
    ``<tool_response>`` there becomes a self-authored "File written" lie that
    later turns read as ground truth, spreading the phantom-write across turns.

    Unlike ``strip_tool_call_markup`` (display-only, truncates at the first pipe
    token), this removes ONLY the response blocks — persisted output may
    legitimately contain deliverable prose with pipe characters or fenced code,
    which must be preserved verbatim. Pass-through on ``None``/``""``.
    """
    if not content:
        return content
    cleaned = _FABRICATED_TOOL_RESPONSE_RE.sub("", content)
    if cleaned == content:
        return content
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _turn_budget_hardstop_message(
    used: int, limit: int, assignment: str, success_criteria: str
) -> str:
    """Wrap-up prompt when THIS turn's (react-loop's) token budget is spent (E2).

    The per-agent budget is a PER-TURN ceiling (E1). The message must therefore
    scope the wrap-up to the CURRENT turn: conclude what this react-loop did, but
    do NOT imply the whole task is finished or infeasible — the orchestrator will
    dispatch more turns. Saying "provide your final output" without that scope is
    what pushed run 1894f0ef into declaring the objective dead every turn.
    """
    return (
        f"[SYSTEM: This turn (react-loop) has used ~95% of its per-turn token "
        f"budget ({used}/{limit}). This is a THIS-TURN limit, NOT the whole task — "
        "the orchestrator will continue the task on the next turn. Conclude THIS "
        "turn now: briefly summarize what you accomplished this turn and hand off "
        "cleanly. Do NOT pronounce a verdict on the overall objective, and make no "
        "more tool calls this turn.]\n\n"
        f"REMINDER — Your assignment was: {assignment}\n"
        f"Success criteria: {success_criteria}"
    )


def budget_pressure_factor(task_budget_level: str | None) -> float:
    """Multiplier on the context-compression threshold, by TASK-budget pressure.

    User question (2026-07-03): does context auto-compress as budget runs low? The
    task budget is a HARD cumulative-effort ceiling — hitting it halts by design
    (it stops a looping task from burning tokens forever), and we do NOT raise it.
    But as the budget crosses warn/hard-stop we compress EARLIER (smaller factor →
    lower threshold) so each remaining turn ships a leaner context and the same
    ceiling buys more useful turns. Pure; never zero/negative (that would force
    pathological compression every call).
    """
    lvl = (task_budget_level or "ok").lower()
    if lvl == "hard_stop":
        return 0.4   # compress aggressively — squeeze the final synthesis turn(s)
    if lvl == "warn":
        return 0.65  # start trimming so the tail of the task stays affordable
    return 1.0       # ok / unknown → unchanged behaviour


def _turn_budget_warn_message() -> str:
    """Soft nudge at ~80% of THIS turn's budget — conclude the turn soon."""
    return (
        "[SYSTEM: This turn has used ~80% of its per-turn token budget. Prioritize "
        "concluding THIS turn's work; avoid expensive or redundant tool calls. Aim "
        "to produce this turn's output within the next 1-2 rounds. The task itself "
        "continues on later turns — this is only a per-turn limit.]"
    )


def _max_rounds_wrapup_message(
    max_rounds: int, role: str, assignment: str, success_criteria: str
) -> str:
    """Wrap-up prompt when an agent hits its per-role max tool rounds (D-Z1).

    Same THIS-TURN scoping as _turn_budget_hardstop_message: hitting the round
    limit ends only THIS turn's react-loop, NOT the task. Without that scope,
    "provide your final output NOW" made agents declare the whole objective
    done/dead (run 1894f0ef) — the exact honest-paralysis E2 fixed for budget.
    """
    return (
        f"[SYSTEM: You have reached the maximum tool rounds ({max_rounds}) for "
        f"your role '{role}' THIS TURN. This is a THIS-TURN limit, NOT the whole "
        "task — the orchestrator will continue the task on the next turn. Conclude "
        "THIS turn now: briefly summarize what you accomplished this turn and hand "
        "off cleanly. Do NOT pronounce a verdict on the overall objective, and make "
        "no more tool calls this turn.]\n\n"
        f"REMINDER — Your assignment was: {assignment}\n"
        f"Success criteria: {success_criteria}"
    )


class AgentWorker:
    """Individual agent worker — thinks, discusses, and executes.

    Each worker is bound to a specific agent identity (role, expertise, personality)
    from AGENT_TEMPLATES. It uses LLM + ToolExecutor to complete assignments.

    Agents have UNLIMITED tool rounds — they run until the LLM decides to stop
    calling tools (produces a text-only response). The only external controls are:
    - Cancellation token (cooperative cancel from user/orchestrator)
    - Per-tool timeout (_TOOL_TIMEOUT_SECONDS)
    - Orchestrator turn limits (max turns across the whole task)
    """

    def __init__(
        self,
        agent_name: str,
        agent_role: str,
        personality: str = "",
        expertise: list[str] | None = None,
        thinking_style: str = "analytical",
        llm: Any = None,
        tool_executor: Any = None,
        tool_schemas: list[dict[str, Any]] | None = None,
        event_callback: Optional[Callable] = None,
        cancel_check: Optional[Callable] = None,
        domain_knowledge: str = "",
        complexity: str = "",
        deliverable_mode: str = "",
    ) -> None:
        self.agent_name = agent_name
        self.agent_role = agent_role
        self.personality = personality
        self.expertise = expertise or []
        self.thinking_style = thinking_style
        self._llm = llm
        self._tool_executor = tool_executor
        self._tool_schemas = tool_schemas or []
        self._event_callback = event_callback
        self._cancel_check = cancel_check
        self._domain_knowledge = domain_knowledge
        # Task-scope signals from the planner → select solution scope in the
        # execution prompt (avoid forcing full-stack on simple/research tasks).
        self._complexity = complexity
        self._deliverable_mode = deliverable_mode
        self._recent_tool_calls: list[dict[str, Any]] = []
        # Central token accounting for this worker's LLM calls (by model/caller).
        from app.orchestration.token_journal import TokenJournal
        self._token_journal = TokenJournal()

        # Token Governor (A per-agent budget + B estimator + C loop monitor).
        from app.core.token_governor import (
            TokenEstimator, BudgetTracker, LoopMonitor,
        )
        try:
            from app.config import get_settings
            _gs = get_settings()
            self._token_estimator = TokenEstimator(
                use_tiktoken=bool(_gs.token_estimator_use_tiktoken)
            )
            # PER-TURN (react-loop) effort ceiling — reset every execute() (E1).
            # Distinct from the PER-TASK ceiling held in the token_governor
            # registry (get_task_budget), which is never reset per turn.
            self._turn_budget = BudgetTracker(
                limit=int(_gs.agent_token_budget),
                warn_ratio=float(_gs.token_budget_warn_ratio),
                hard_stop_ratio=float(_gs.token_budget_hard_stop_ratio),
            )
            self._loop_monitor = LoopMonitor(
                warn_count=int(_gs.loop_monitor_warn_count),
                hard_stop_count=int(_gs.loop_monitor_hard_stop_count),
            )
        except Exception:
            self._token_estimator = TokenEstimator(use_tiktoken=False)
            self._turn_budget = BudgetTracker(limit=400_000)
            self._loop_monitor = LoopMonitor()
        self._budget_warn_emitted = False  # warn message injected once per turn
        self._last_estimate = 0

        self._stuck_count: int = 0
        self._no_progress_since: float | None = None  # wall-clock timestamp when futile loop started
        self._max_rounds = self._resolve_max_rounds()
        self._reflection_engine: SelfReflectionEngine | None = (
            SelfReflectionEngine(llm, reflection_interval=3, min_tool_calls_before_reflect=3)
            if llm and is_self_reflection_enabled() else None
        )
        self._total_tool_calls_this_run: int = 0
        self._consecutive_error_calls: int = 0
        self._consecutive_failed_rounds: int = 0

        # M3: agent-execution tunables sourced from config (single source of truth).
        # Falls back to the historical constants if settings are unavailable.
        try:
            from app.config import get_settings
            _s = get_settings()
            self._tool_timeout: float = float(_s.tool_timeout)
            self._tool_output_cap: int = int(_s.agent_tool_output_cap_chars)
            self._compress_threshold: int = int(_s.context_compress_threshold_chars)
        except Exception:
            self._tool_timeout = float(_TOOL_TIMEOUT_SECONDS)
            self._tool_output_cap = _TOOL_OUTPUT_CAP_CHARS
            self._compress_threshold = _CONTEXT_COMPRESS_THRESHOLD_CHARS

    def _resolve_max_rounds(self) -> int:
        """Resolve role-based max rounds from AgentToolPolicy.

        Tries exact role match first, then fuzzy-matches role keywords
        from agent_name (e.g. "Backend Developer" → "developer").
        Falls back to _DEFAULT_ROLE_MAX_ROUNDS for unknown roles.
        """
        try:
            policy = AgentToolPolicy()
            # Try exact role match
            limit = policy.get_max_rounds(self.agent_role)
            # get_max_rounds returns config default (20) for unknown roles;
            # but we want to also try fuzzy-matching agent_name keywords
            role_lower = self.agent_role.lower() if self.agent_role else ""
            if role_lower in policy._ROLE_MAX_ROUNDS:
                return limit

            # Fuzzy match: check if any known role keyword appears in agent_name
            name_lower = self.agent_name.lower() if self.agent_name else ""
            for known_role in policy._ROLE_MAX_ROUNDS:
                if known_role in name_lower or known_role in role_lower:
                    return policy._ROLE_MAX_ROUNDS[known_role]

            return _DEFAULT_ROLE_MAX_ROUNDS
        except Exception:
            return _DEFAULT_ROLE_MAX_ROUNDS

    @property
    def role(self) -> str:
        return self.agent_role

    def set_cancel_check(self, cancel_check: Optional[Callable]) -> None:
        """Set cancellation check function (propagated from TurnExecutor)."""
        self._cancel_check = cancel_check

    def _is_cancelled(self) -> bool:
        """Check if execution should be cancelled."""
        if self._cancel_check is None:
            return False
        return self._cancel_check()

    async def think(
        self,
        assignment: AgentAssignment,
        shared_context: str,
        previous_turns: str = "",
        roe_context: str = "",
    ) -> AgentThinking:
        """Produce thinking/reasoning about the assignment.

        This is a single LLM call (no tools) where the agent
        analyzes the task and plans its approach.
        """
        system_prompt = build_agent_thinking_prompt(
            agent_name=self.agent_name,
            agent_role=self.agent_role,
            personality=self.personality,
            expertise=self.expertise,
            thinking_style=self.thinking_style,
            assignment=assignment.task_description,
            success_criteria=assignment.success_criteria,
            shared_context=shared_context,
            previous_turns=previous_turns,
            roe_context=roe_context,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Produce your thinking about this assignment: {assignment.task_description}"},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.4, max_tokens=2048)
            content = result.get("content", "")

            # Parse JSON response
            parsed = _parse_json_response(content)
            if parsed:
                # Coerce challenges/supports to readable strings (LLM may return dict objects)
                challenges = [_format_discussion_item(c) for c in parsed.get("challenges", [])]
                supports = [_format_discussion_item(s) for s in parsed.get("supports", [])]
                return AgentThinking(
                    agent_name=self.agent_name,
                    agent_role=self.agent_role,
                    thinking=parsed.get("thinking", content),
                    challenges=challenges,
                    supports=supports,
                )

            # Fallback: treat entire response as thinking
            return AgentThinking(
                agent_name=self.agent_name,
                agent_role=self.agent_role,
                thinking=content,
            )
        except Exception as e:
            logger.warning("agent_think_failed", agent=self.agent_name, error=str(e))
            return AgentThinking(
                agent_name=self.agent_name,
                agent_role=self.agent_role,
                thinking=f"(Thinking failed: {str(e)[:100]})",
            )

    async def discuss(
        self,
        other_thinkings: list[AgentThinking],
        own_assignment: AgentAssignment,
    ) -> AgentThinking:
        """React to other agents' thinking — challenge or support.

        Quick LLM call to see if this agent has reactions to others' plans.
        Returns updated thinking with challenges/supports filled.
        """
        if not other_thinkings:
            return AgentThinking(agent_name=self.agent_name, agent_role=self.agent_role)

        others_text = ""
        for t in other_thinkings:
            if t.agent_role == self.agent_role:
                continue
            others_text += f"- **{t.agent_name}** ({t.agent_role}): {t.thinking}\n"

        if not others_text:
            return AgentThinking(agent_name=self.agent_name, agent_role=self.agent_role)

        messages = [
            {"role": "system", "content": (
                f"You are {self.agent_name} ({self.agent_role}). "
                f"Your expertise: {', '.join(self.expertise)}.\n"
                f"Your assignment: {own_assignment.task_description}\n\n"
                "Other agents have shared their thinking. "
                "If you have challenges or supports, state them briefly. "
                "Respond with JSON: {\"challenges\": [...], \"supports\": [...]}\n"
                "Only include items where you have genuine expert insight. "
                "Empty arrays are fine if you have nothing to add."
            )},
            {"role": "user", "content": f"Other agents' thinking:\n{others_text}"},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.3, max_tokens=1024)
            content = result.get("content", "")
            parsed = _parse_json_response(content)
            if parsed:
                # Coerce challenges/supports to readable strings (LLM may return dict objects)
                challenges = [_format_discussion_item(c) for c in parsed.get("challenges", [])]
                supports = [_format_discussion_item(s) for s in parsed.get("supports", [])]
                return AgentThinking(
                    agent_name=self.agent_name,
                    agent_role=self.agent_role,
                    challenges=challenges,
                    supports=supports,
                )
        except Exception as e:
            logger.debug("agent_discuss_skipped", agent=self.agent_name, error=str(e))

        return AgentThinking(agent_name=self.agent_name, agent_role=self.agent_role)

    async def execute(
        self,
        assignment: AgentAssignment,
        shared_context: str,
        own_thinking: str = "",
        previous_turns: str = "",
        current_turn: int = 0,
        roe_context: str = "",
        workspace_path: str = "",
    ) -> AgentResult:
        """Execute the assignment using tools.

        This is the main execution loop where the agent:
        1. Gets a system prompt with full context
        2. Makes LLM calls with tools available
        3. Iterates until agent stops calling tools (unlimited rounds)
        4. Returns final output with all tool call records

        Agent runs until done — no artificial round limit.
        """
        # FIX F3: Reset reflection engine state for fresh execution context.
        # Prevents stale quality scores from previous turns leaking into new assignments.
        if self._reflection_engine:
            self._reflection_engine._reflection_count = 0
            self._reflection_engine._last_quality_score = 0.5

        # Build tool catalog text for prompt
        tool_catalog = _build_tool_catalog_text(self._tool_schemas)

        # Build infrastructure map (self-awareness)
        infra_map = ""
        try:
            from app.core.infrastructure_map import get_infrastructure_map
            import asyncio
            infra_map = await get_infrastructure_map().build_map()
        except Exception:
            pass

        system_prompt = build_agent_execution_prompt(
            agent_name=self.agent_name,
            agent_role=self.agent_role,
            personality=self.personality,
            expertise=self.expertise,
            thinking_style=self.thinking_style,
            assignment=assignment.task_description,
            success_criteria=assignment.success_criteria,
            shared_context=shared_context,
            own_thinking=own_thinking,
            previous_turns=previous_turns,
            tool_catalog=tool_catalog,
            roe_context=roe_context,
            workspace_path=workspace_path,
            infrastructure_map=infra_map,
            domain_knowledge=self._domain_knowledge,
            complexity=self._complexity,
            deliverable_mode=self._deliverable_mode,
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Execute your assignment: {assignment.task_description}\n\n"
                f"Success criteria: {assignment.success_criteria}\n\n"
                "Use tools as needed. When finished, provide your final output."
            )},
        ]

        all_tool_calls: list[ToolCall] = []
        final_content = ""
        round_num = 0
        _timeout_retries = 0
        _timed_out = False

        # Reset ALL execution state for this new assignment (workers reused across turns)
        self._recent_tool_calls = []
        self._stuck_count = 0
        self._no_progress_since = None
        self._toolcall_parsedrop_retried = False  # P0-2: one parse-drop re-try per run
        self._total_tool_calls_this_run = 0
        self._consecutive_error_calls = 0
        self._consecutive_failed_rounds = 0
        self._edp_strike_fired = False
        self._edp_output_last_warn = 0
        self._loop_monitor.reset()
        # E1: the per-turn budget is a per-react-loop ceiling ("cumulative tokens
        # / one agent react-loop"). Workers are REUSED across turns, so without
        # this reset `_turn_budget._used` accumulates across independent turns and
        # trips hard_stop "before the first tool call" on later turns — the
        # budget-death seen across all three audited runs. Scope it to this
        # execute() so a turn is judged on its own effort, not the worker's life.
        # (The PER-TASK ceiling in the token_governor registry is intentionally
        # NOT reset here — it is the whole-task lifetime limit.)
        self._turn_budget.reset()
        self._budget_warn_emitted = False
        self._last_estimate = 0

        # Set execution context for tools
        from app.core.execution_context import current_agent_name, current_task_id
        current_agent_name.set(self.agent_name)
        # Ensure current_task_id is set (extract from workspace_path if context empty)
        if workspace_path and not current_task_id.get(""):
            # workspace_path formats:
            #   "workspace_data/tasks/{id}/_work"                       (top-level)
            #   "workspace_data/tasks/{parent}/subtasks/{child}/_work"  (nested subtask)
            # Prefer the subtask id when nested so context lands in the subtask.
            parts = Path(workspace_path).parts
            _resolved = ""
            for i, part in enumerate(parts):
                if part == "subtasks" and i + 1 < len(parts):
                    _resolved = parts[i + 1]
                    break
                if part == "tasks" and i + 1 < len(parts):
                    _resolved = parts[i + 1]
            if _resolved:
                current_task_id.set(_resolved)

        while True:
            # Role-based round limit: enforce per-role max rounds
            if round_num >= self._max_rounds:
                logger.warning(
                    "agent_max_rounds_reached",
                    agent=self.agent_name,
                    role=self.agent_role,
                    max_rounds=self._max_rounds,
                    round=round_num,
                )
                # Inject wrap-up message so agent produces final output
                messages.append({
                    "role": "user",
                    "content": _max_rounds_wrapup_message(
                        max_rounds=self._max_rounds,
                        role=self.agent_role,
                        assignment=assignment.task_description,
                        success_criteria=assignment.success_criteria,
                    ),
                })
                # One last LLM call without tools to get final summary
                try:
                    wrap_result = await self._llm.chat(
                        messages=messages, temperature=0.3, max_tokens=8192,
                    )
                    final_content = wrap_result.get("content", "")
                except Exception:
                    pass
                break

            # Safety ceiling: prevent infinite loops from bugs (not flow control)
            if round_num >= _TOOL_ROUND_SAFETY_CEILING:
                logger.warning(
                    "agent_safety_ceiling_hit",
                    agent=self.agent_name,
                    rounds=round_num,
                )
                break

            # Check cancellation at start of each round
            if self._is_cancelled():
                final_content = "(Cancelled by user)"
                break

            # Emit thinking event
            await self._emit_event("agent_thinking", {
                "agent_name": self.agent_name,
                "agent_role": self.agent_role,
                "action": "thinking",
                "content": f"Starting execution round {round_num + 1}...",
            })

            # Smart context compression: if messages too large, compress older ones
            old_len = len(messages)
            messages = self._compress_context_if_needed(messages)
            if len(messages) < old_len:
                await self._emit_event("context_compaction", {
                    "agent_name": self.agent_name,
                    "agent_role": self.agent_role,
                    "original_messages": old_len,
                    "compressed_messages": len(messages),
                    "round": round_num,
                })

            # Token Governor — PER-TURN (react-loop) cumulative-effort budget.
            # Reset each turn (E1); the whole-task ceiling lives in the registry
            # and drives the orchestrator's one-synthesis-then-halt separately.
            try:
                _bstatus = self._turn_budget.status()
                if _bstatus.level == "hard_stop":
                    logger.warning(
                        "agent_turn_budget_exhausted",
                        agent=self.agent_name, used=_bstatus.used, limit=_bstatus.limit,
                    )
                    messages.append({
                        "role": "user",
                        "content": _turn_budget_hardstop_message(
                            used=_bstatus.used, limit=_bstatus.limit,
                            assignment=assignment.task_description,
                            success_criteria=assignment.success_criteria,
                        ),
                    })
                    try:
                        wrap_result = await self._llm.chat(
                            messages=messages, temperature=0.3, max_tokens=8192,
                        )
                        final_content = wrap_result.get("content", "") or final_content
                        # Record the final wrap-up call's usage so budget/journal stay consistent.
                        self._account_usage(wrap_result.get("usage"))
                    except Exception:
                        pass
                    break
                elif _bstatus.level == "warn" and not self._budget_warn_emitted:
                    self._budget_warn_emitted = True
                    messages.append({
                        "role": "user",
                        "content": _turn_budget_warn_message(),
                    })
            except Exception:
                pass  # governor must never break the agent loop

            # ── LLM call with error recovery + GAP-7 concurrency control ──
            from app.orchestration.adaptive_concurrency import get_concurrency_limiter
            _limiter = get_concurrency_limiter()
            await _limiter.acquire()
            _llm_success = True
            try:
                from app.core.tracing import get_tracer
                with get_tracer().span(
                    "agent_llm_call",
                    attrs={
                        "agent": self.agent_name,
                        "model": getattr(self._llm, "model", "unknown"),
                        "round": round_num,
                    },
                ):
                    result = await self._llm.chat(
                        messages=messages,
                        tools=self._tool_schemas,
                        temperature=0.4,
                        max_tokens=16384,
                    )
                # Reset timeout retry counter on success
                _timeout_retries = 0
            except asyncio.TimeoutError:
                _llm_success = True  # Timeout ≠ rate limit
                _limiter.release(success=True)
                # Timeout → compress context and retry (max _MAX_TIMEOUT_RETRIES)
                _timeout_retries += 1
                logger.warning(
                    "agent_llm_timeout",
                    agent=self.agent_name,
                    round=round_num,
                    attempt=_timeout_retries,
                )
                if _timeout_retries <= _MAX_TIMEOUT_RETRIES:
                    messages = self._compress_context_if_needed(messages, force=True)
                    continue  # Retry with compressed context
                # All retries exhausted
                logger.error(
                    "agent_llm_timeout_exhausted",
                    agent=self.agent_name,
                    round=round_num,
                )
                _timed_out = True
                break
            except Exception as e:
                _err_str = str(e).lower()
                # GAP-7: Detect rate limiting and signal to concurrency limiter
                _is_rate_limit = any(
                    pattern in _err_str
                    for pattern in ["429", "rate limit", "too many requests", "quota exceeded"]
                )
                _limiter.release(success=not _is_rate_limit)

                # Check for context length exceeded → compress and retry once
                _is_context_overflow = any(
                    pattern in _err_str
                    for pattern in [
                        "context length", "too many tokens", "maximum context",
                        "input too long", "request too large", "token limit",
                        "exceeds the maximum", "payload too large",
                    ]
                )
                if _is_context_overflow:
                    logger.warning(
                        "agent_context_overflow_recovery",
                        agent=self.agent_name,
                        round=round_num,
                        error=str(e)[:200],
                    )
                    messages = self._compress_context_if_needed(messages, force=True)
                    # Retry once after compression
                    try:
                        result = await self._llm.chat(
                            messages=messages,
                            tools=self._tool_schemas,
                            temperature=0.4,
                            max_tokens=16384,
                        )
                    except Exception as retry_e:
                        logger.error(
                            "agent_context_overflow_retry_failed",
                            agent=self.agent_name,
                            error=str(retry_e)[:200],
                        )
                        final_content = f"(Agent execution failed after context overflow: {retry_e})"
                        break
                elif _is_rate_limit:
                    # GAP-7: Rate limited — wait and retry (limiter already reduced concurrency)
                    logger.warning("agent_rate_limited", agent=self.agent_name, round=round_num)
                    await asyncio.sleep(2.0)  # Brief pause, limiter handles longer backoff
                    continue
                else:
                    # Check for orphan tool_use protocol violation
                    _is_tool_orphan = "tool_use" in _err_str and "tool_result" in _err_str
                    if _is_tool_orphan:
                        logger.warning(
                            "agent_tool_orphan_recovery",
                            agent=self.agent_name,
                            round=round_num,
                        )
                        messages = self._fix_orphan_tool_use(messages)
                        continue  # Retry with fixed messages

                    # Generic LLM failure — try without tools as fallback
                    logger.error(
                        "agent_llm_failed",
                        agent=self.agent_name,
                        round=round_num,
                        error=str(e),
                    )
                    try:
                        result = await self._llm.chat(
                            messages=messages,
                            temperature=0.4,
                            max_tokens=8192,
                        )
                        final_content = result.get("content", f"(Agent error: {e})")
                    except Exception:
                        final_content = f"(Agent execution failed: {e})"
                    break
            else:
                # LLM call succeeded — release with success
                _limiter.release(success=True)

            # Central token accounting (by model/caller) for this worker.
            _usage = result.get("usage")
            self._account_usage(_usage)
            # Token Governor: calibrate estimator from real prompt_tokens (only the
            # main call has a fresh outbound estimate to compare against).
            try:
                if _usage:
                    _model = getattr(self._llm, "model", "unknown")
                    _real = int(_usage.get("prompt_tokens", 0) or 0)
                    if _real > 0 and self._last_estimate > 0:
                        self._token_estimator.observe(_model, self._last_estimate, _real)
            except Exception:
                pass  # accounting must never break the agent loop

            # ── Orphan tool-use fix: check if last message is tool_use without result ──
            messages = self._fix_orphan_tool_use(messages)

            # ── Validate LLM response (malformed check) ──
            content_text = result.get("content", "") or ""
            tool_calls = result.get("tool_calls", [])

            if not content_text.strip() and not tool_calls:
                # Malformed response (no content, no tool_calls) → retry once
                logger.warning(
                    "agent_llm_malformed_response",
                    agent=self.agent_name,
                    round=round_num,
                )
                try:
                    result = await self._llm.chat(
                        messages=messages,
                        tools=self._tool_schemas,
                        temperature=0.4,
                        max_tokens=16384,
                    )
                    content_text = result.get("content", "") or ""
                    tool_calls = result.get("tool_calls", [])
                except Exception:
                    pass  # Use whatever we had

            # ── F1: recover tool-calls the model emitted as JSON inside prose ──
            # On native function-calling providers (bedrock/opus) the model
            # occasionally writes a tool call as a ```json``` / bare-JSON blob in
            # `content` while leaving the native tool_calls slot empty. The runtime
            # then drops it ("phantom write"). The recovery parser exists but is
            # gated to prompted providers in llm_client, so it never runs here.
            # The worker — which already knows this turn has no actionable native
            # call — scans the content itself and executes any real tool call it
            # finds, instead of mistaking the prose for a final answer.
            if not tool_calls and content_text.strip():
                recovered = self._recover_tool_calls_from_content(content_text)
                if recovered:
                    logger.warning(
                        "agent_recovered_tool_calls_from_prose",
                        agent=self.agent_name,
                        role=self.agent_role,
                        round=round_num,
                        count=len(recovered),
                        tools=[c["function"]["name"] for c in recovered],
                    )
                    tool_calls = recovered
                    result["tool_calls"] = recovered

            if not tool_calls:
                # No tool calls — agent is done
                finish_reason = result.get("finish_reason", "")
                # P0-2 (tri-run round-2): the content carries tool-call MARKUP but
                # nothing parsed or recovered — a SILENT parse-drop, not a final
                # answer. The 03ce93bd run emitted ~30 such blocks over 6 turns while
                # the orchestrator misread the empty tools_used as a shell/env failure.
                # Surface it and feed the schema back ONCE so the model re-emits in a
                # readable format instead of the turn ending as a phantom "done".
                if (
                    content_has_unparsed_toolcall_markup(content_text)
                    and not getattr(self, "_toolcall_parsedrop_retried", False)
                ):
                    self._toolcall_parsedrop_retried = True
                    logger.warning(
                        "agent_toolcall_parse_dropped",
                        agent=self.agent_name,
                        role=self.agent_role,
                        round=round_num,
                        finish_reason=finish_reason,
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM: Your last message contained tool-call markup "
                            "(e.g. <tool_call>/<arg_key>) that the runtime could NOT "
                            "parse, so NO tool actually ran. Do NOT describe results "
                            "you did not obtain. Re-issue the call using the NATIVE "
                            "tool-calling mechanism (a real function call), not text "
                            "tags. If you cannot, state plainly which tool + arguments "
                            "you intend so it can be run.]"
                        ),
                    })
                    continue  # give the model one clean re-try before ending the turn
                if not content_text.strip() and round_num == 0:
                    # LLM returned nothing on first round — likely model refusal.
                    # Retry WITHOUT tools (some models refuse tool_use + sensitive content
                    # but accept plain chat). This gives the agent a chance to at least
                    # produce a text response explaining what it can do.
                    logger.warning(
                        "agent_empty_first_round_retrying_no_tools",
                        agent=self.agent_name,
                        role=self.agent_role,
                        finish_reason=finish_reason,
                    )
                    try:
                        retry_result = await self._llm.chat(
                            messages=messages,
                            temperature=0.4,
                            max_tokens=8192,
                        )
                        retry_content = (retry_result.get("content", "") or "").strip()
                        if retry_content:
                            final_content = retry_content
                            break
                    except Exception:
                        pass

                    # Still empty after retry — produce diagnostic. An empty
                    # response can be either an infra issue OR a model declining on
                    # safety grounds. Do NOT advise "try a different model/provider"
                    # as a way to route around a possible safety refusal (CC-5):
                    # state both possibilities honestly and let the orchestrator's
                    # refusal-terminal guard handle a genuine safety stop.
                    msg_count = len(messages)
                    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
                    tools_count = len(self._tool_schemas) if self._tool_schemas else 0
                    # H4 (audit 2026-06-29): when the provider gives a SAFETY
                    # finish_reason (content_filter / refusal / safety / …), this is
                    # a CONFIRMED model refusal, not an infra blip. Emit a phrase the
                    # orchestrator's refusal_is_terminal guard recognizes so the
                    # blocked-terminal/lawful_refusal ceiling fires and the loop stops
                    # — otherwise an offensive role assigned to a defensive/blocked
                    # task keeps getting re-dispatched and content-filtered for dozens
                    # of turns (the 32-turn role-mismatch loop in task 85b5e3c9).
                    _is_safety_finish = (
                        str(finish_reason or "").lower() in SAFETY_FINISH_REASONS
                        or bool(result.get("_safety_stripped"))
                    )
                    if _is_safety_finish:
                        final_content = (
                            f"(Agent '{self.agent_name}' — terminal refusal: the model "
                            f"declined this request on safety/content-policy grounds "
                            f"(finish_reason={finish_reason}). This is a valid safety "
                            f"refusal; stopping rather than switching models to force it "
                            f"through. If the target is third-party infrastructure not "
                            f"authorized in scope, cannot proceed without authorization.)"
                        )
                    else:
                        final_content = (
                            f"(Agent '{self.agent_name}' received an empty response from the LLM. "
                            f"finish_reason={finish_reason}, "
                            f"context: {msg_count} messages, ~{total_chars} chars, {tools_count} tools. "
                            f"This is likely a transient infrastructure issue — retry is "
                            f"appropriate.)"
                        )
                    logger.error(
                        "agent_model_refusal_confirmed",
                        agent=self.agent_name,
                        role=self.agent_role,
                        finish_reason=finish_reason,
                        is_safety_finish=_is_safety_finish,
                        message_count=msg_count,
                        total_chars=total_chars,
                    )
                elif not content_text.strip() and round_num > 0 and all_tool_calls:
                    # Agent called tools but then LLM stopped without concluding
                    break  # Will hit _force_synthesis fallback below
                else:
                    final_content = content_text
                break

            # Process tool calls
            assistant_content = content_text
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
            })
            _loop_hard_stop = False  # set by LoopMonitor when a tool repeats too often

            # Emit reasoning if present. Strip any leaked tool-call machinery
            # FIRST (model families serialize calls into prose with different
            # delimiters), then truncate — so the displayed reasoning is clean
            # regardless of which model is in rotation, and a delimiter can't be
            # split mid-token by the length cap.
            _reasoning_display = strip_tool_call_markup(assistant_content)
            if _reasoning_display and _reasoning_display.strip():
                await self._emit_event("agent_thinking", {
                    "agent_name": self.agent_name,
                    "agent_role": self.agent_role,
                    "action": "reasoning",
                    "content": _reasoning_display[:1000],
                })

            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                tool_args_str = tc["function"]["arguments"]
                tool_call_id = tc.get("id", f"call_{len(all_tool_calls)}")

                # Parse arguments
                try:
                    tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                except Exception:
                    tool_args = {}

                # Token Governor C: in-agent loop detection.
                try:
                    _lstatus = self._loop_monitor.record(tool_name, tool_args)
                    if _lstatus.level == "warn":
                        logger.info(
                            "agent_tool_loop_warn",
                            agent=self.agent_name, tool=tool_name, count=_lstatus.count,
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                f"[SYSTEM: You have called `{tool_name}` with nearly identical "
                                f"arguments {_lstatus.count} times. This is going in circles. "
                                "Change your approach OR conclude with the data you already have.]"
                            ),
                        })
                    elif _lstatus.level == "hard_stop":
                        logger.warning(
                            "agent_tool_loop_hard_stop",
                            agent=self.agent_name, tool=tool_name, count=_lstatus.count,
                        )
                        _loop_hard_stop = True
                        break  # stop dispatching further tool calls this round
                except Exception:
                    pass  # loop detection must never break the agent loop

                # Emit tool_start
                await self._emit_event("tool_start", {
                    "agent_name": self.agent_name,
                    "agent_role": self.agent_role,
                    "tool": tool_name,
                    "params": tool_args,
                })

                # Register action for parallel deconfliction
                _ctx_sharing = getattr(self, '_context_sharing', None)
                if _ctx_sharing and tool_name:
                    _action_target = str(
                        tool_args.get("url", tool_args.get("target", tool_args.get("path", tool_args.get("query", ""))))
                    )[:100]
                    if _action_target:
                        _ctx_sharing.register_action(self.agent_role, f"{tool_name}:{_action_target}")

                # Execute tool
                t_start = time.perf_counter()
                tool_output = ""
                tool_success = True

                # E-R1/E-R2 (tri-run audit 2026-07-02): cross-turn hard-skip for an
                # action that ALREADY FAILED 3+ times across turns. The registry's
                # should_skip has an "already succeeded" branch too, but we
                # deliberately act ONLY on the exhausted-failure branch here — a
                # succeeded action is often legitimately repeated (re-read a file to
                # continue, re-run a fixed script), so hard-blocking that would break
                # normal work. A repeatedly-failing action (deepseek looped
                # list_files on a missing dir 10×) is the real churn to cut. Advisory
                # only when the registry says "retry allowed".
                _registry = getattr(self, '_cross_turn_registry', None)
                if _registry is not None and tool_name not in (
                    "context_recall", "data_vault_store", "data_vault_retrieve"
                ):
                    try:
                        _akey = f"{tool_name}:{str(tool_args)[:100]}"
                        _skip, _reason = _registry.should_skip(_akey)
                        # Only the exhausted-FAILURE branch blocks; a prior SUCCESS
                        # must not (re-reads / re-runs are legitimate).
                        if _skip and "Failed" in _reason:
                            logger.warning(
                                "agent_cross_turn_skip_exhausted",
                                agent=self.agent_name, tool=tool_name, reason=_reason[:120],
                            )
                            tool_output = (
                                f"[SKIPPED — this action already FAILED repeatedly across "
                                f"turns: {_reason} Do NOT retry it identically; change the "
                                f"approach (different tool/args) or report the blocker.]"
                            )
                            tool_success = False
                            all_tool_calls.append(ToolCall(
                                tool_name=tool_name, arguments=tool_args,
                                output=tool_output, success=False,
                            ))
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": tool_output,
                            })
                            continue  # skip execution; move to next tool call
                    except Exception:
                        pass  # cross-turn skip must never break the agent loop

                # Intercept context_recall pseudo-tool (handled locally, no external call)
                if tool_name == "context_recall":
                    _ledger = getattr(self, '_context_ledger', None)
                    if _ledger:
                        tool_output = _ledger.query_for_tool(tool_args.get("query", ""))
                    else:
                        tool_output = "(context_recall unavailable — no ledger attached to this worker)"
                    tool_success = True
                elif tool_name == "data_vault_store":
                    # GAP-1 FIX: Store artifact in DataVault
                    _vault = getattr(self, '_data_vault', None)
                    if _vault:
                        _store_result = _vault.store(
                            key=tool_args.get("key", ""),
                            content=tool_args.get("content", ""),
                            turn=getattr(self, '_current_turn', 0),
                            agent_role=self.agent_role,
                            agent_name=self.agent_name,
                            tags=tool_args.get("tags"),
                            content_type=tool_args.get("content_type", "text"),
                        )
                        tool_output = json.dumps(_store_result, ensure_ascii=False)
                        tool_success = _store_result.get("success", False)
                    else:
                        tool_output = "(data_vault unavailable)"
                        tool_success = False
                elif tool_name == "data_vault_retrieve":
                    # GAP-1 FIX: Retrieve artifact from DataVault
                    _vault = getattr(self, '_data_vault', None)
                    if _vault:
                        if tool_args.get("list_keys_only"):
                            _results = _vault.list_keys(prefix=tool_args.get("key", ""))
                            tool_output = json.dumps(_results, ensure_ascii=False)
                        else:
                            _turn_range = None
                            if tool_args.get("turn_min") or tool_args.get("turn_max"):
                                _turn_range = (
                                    tool_args.get("turn_min", 0),
                                    tool_args.get("turn_max", 9999),
                                )
                            _results = _vault.retrieve(
                                key=tool_args.get("key"),
                                tags=tool_args.get("tags"),
                                agent_role=tool_args.get("agent_role"),
                                turn_range=_turn_range,
                            )
                            tool_output = json.dumps(_results, ensure_ascii=False)
                        tool_success = True
                    else:
                        tool_output = "(data_vault unavailable)"
                        tool_success = False
                else:
                    try:
                        tool_result = await asyncio.wait_for(
                            self._tool_executor.execute(tool_name, tool_args),
                            timeout=self._tool_timeout,
                        )
                        tool_output = tool_result.get("output", "") if isinstance(tool_result, dict) else str(tool_result)
                        tool_success = tool_result.get("success", True) if isinstance(tool_result, dict) else True
                    except asyncio.TimeoutError:
                        tool_output = f"Tool '{tool_name}' timed out after {self._tool_timeout}s"
                        tool_success = False
                    except Exception as te:
                        tool_output = f"Tool error: {te}"
                        tool_success = False

                # Self-evolve support: if a tool registers new tools at runtime,
                # refresh function-calling schemas so the LLM can call them in the
                # SAME agent run (without waiting for a new task/turn).
                if tool_success and tool_name in ("load_tool_module", "scaffold_custom_tool_module"):
                    try:
                        from app.mcp.tool_executor import ToolExecutor as _ToolExecutor
                        self._tool_schemas = _ToolExecutor.get_tool_schemas()
                    except Exception:
                        pass

                elapsed_ms = (time.perf_counter() - t_start) * 1000

                # Track for stuck detection (cap at 120 to bound memory)
                self._recent_tool_calls.append({
                    "name": tool_name,
                    "args_hash": hash(
                        json.dumps(tool_args, sort_keys=True, default=str)
                    ),
                    "success": tool_success,
                    "output_snippet": tool_output[:500].lower(),
                    "elapsed_s": elapsed_ms / 1000.0,
                })
                if len(self._recent_tool_calls) > 120:
                    self._recent_tool_calls = self._recent_tool_calls[-100:]

                # Record tool call — output capped to prevent OOM on long-running agents.
                # ACTIVITY.md gets full output via event callback (tool_done event above).
                _tc_output_cap = 4000
                tc_record = ToolCall(
                    tool_name=tool_name,
                    arguments=tool_args,
                    output=tool_output[:_tc_output_cap] if len(tool_output) > _tc_output_cap else tool_output,
                    success=tool_success,
                    execution_time_ms=elapsed_ms,
                )
                all_tool_calls.append(tc_record)

                # Emit tool_done
                await self._emit_event("tool_done", {
                    "agent_name": self.agent_name,
                    "agent_role": self.agent_role,
                    "tool": tool_name,
                    "success": tool_success,
                    "execution_time_ms": round(elapsed_ms, 1),
                    "output": tool_output[:500],
                })

                # CONTEXT MANAGEMENT: File gets full output, LLM gets capped version.
                # ToolCall record above has the complete output (for ACTIVITY.md).
                # But the LLM message is capped at 20KB to protect context window.
                # GAP-4 FIX: When truncating, also extract key findings from the
                # TAIL of the output (where conclusions often live) so agents don't
                # lose critical data buried past the 20KB mark.
                if len(tool_output) > self._tool_output_cap:
                    # Keep head + extract tail summary (last 2KB often has conclusions)
                    _tail_extract = tool_output[-2000:] if len(tool_output) > self._tool_output_cap + 2000 else ""
                    _tail_section = ""
                    if _tail_extract:
                        _tail_section = f"\n\n[... TAIL OF OUTPUT (last 2KB) ...]\n{_tail_extract}"
                    llm_tool_output = (
                        tool_output[:self._tool_output_cap]
                        + f"\n\n[OUTPUT TRUNCATED — {len(tool_output)} chars total, "
                        f"showing first {self._tool_output_cap} + last 2000]"
                        + _tail_section
                        + f"\n[Full content: activity_search(keyword=\"{tool_name}\", turn={current_turn})]"
                    )
                else:
                    llm_tool_output = tool_output

                # Append capped tool result to LLM messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": llm_tool_output,
                })

            # Token Governor C: a tool loop hit the hard ceiling → force conclusion.
            if _loop_hard_stop:
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM: Repeated tool calls detected — you are stuck in a loop. "
                        "Stop calling tools. Summarize what you've found and provide your "
                        "final output NOW.]\n\n"
                        f"REMINDER — Your assignment was: {assignment.task_description}"
                    ),
                })
                # Loop hard-stop may have broken mid-dispatch, leaving tool_calls
                # without results. Reconcile so the wrap-up call is provider-valid.
                messages = self._fix_orphan_tool_use(messages)
                try:
                    wrap_result = await self._llm.chat(
                        messages=messages, temperature=0.3, max_tokens=8192,
                    )
                    final_content = wrap_result.get("content", "") or final_content
                    # Record the final wrap-up call's usage so budget/journal stay consistent.
                    self._account_usage(wrap_result.get("usage"))
                except Exception:
                    pass
                # An agent loop-stopped with ZERO successful tools is pure thrashing,
                # not a conclusion — mark it FORCE-STOPPED so it reports success=False
                # (consistent with the stuck-pattern / consecutive-failure breakers).
                # An agent that produced real work before looping keeps its wrap-up
                # summary and success unchanged.
                if not any(tc.success for tc in all_tool_calls):
                    final_content = (
                        "[AGENT FORCE-STOPPED: repeated tool calls with no successful "
                        "result (loop detected). Halting to avoid wasted rounds.]"
                    )
                break  # exit react-loop

            # ══════════════════════════════════════════════════════════════
            # EXECUTION DISCIPLINE PROTOCOL (EDP)
            # Hard code-level enforcement BEFORE soft stuck detection.
            # Each rule fires AT MOST ONCE (uses _edp_fired flags to avoid
            # spamming context with repeated warnings — that would cause
            # context bloat and compression loops).
            # ══════════════════════════════════════════════════════════════

            _total_calls = len(self._recent_tool_calls)
            _recent = self._recent_tool_calls

            # RULE 1: 3-STRIKE — same tool + same error 3x → force approach change (ONCE)
            if _total_calls >= 3 and not getattr(self, '_edp_strike_fired', False):
                _last3 = _recent[-3:]
                _same_tool = len(set(tc["name"] for tc in _last3)) == 1
                _all_failed = all(not tc["success"] for tc in _last3)
                _same_error = (
                    _all_failed and len(set(tc["output_snippet"][:100] for tc in _last3)) == 1
                )
                if _same_tool and _same_error:
                    self._edp_strike_fired = True
                    _failing_tool = _last3[0]["name"]
                    _err_preview = _last3[0]["output_snippet"][:150]
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SYSTEM: 3-STRIKE] `{_failing_tool}` failed identically 3x: "
                            f"{_err_preview}\n"
                            f"Do NOT call `{_failing_tool}` the same way again. Climb the Failure "
                            f"Recovery ladder: diagnose root cause → fix usage → or BUILD A BYPASS "
                            f"(write a script / call the API directly / chain tools) to achieve the "
                            f"SAME goal. Switch tools only if a bypass is impossible. Do not abandon "
                            f"the goal."
                        ),
                    })

            # Reset strike flag when agent uses a DIFFERENT tool successfully
            if getattr(self, '_edp_strike_fired', False) and _total_calls > 3:
                _latest = _recent[-1]
                if _latest["success"] and (
                    not _recent[-4:-1] or _latest["name"] != _recent[-2]["name"]
                ):
                    self._edp_strike_fired = False

            # RULE 2: OUTPUT-FIRST — 5 reads without produce → warn (ONCE per 10 calls)
            _edp_output_last = getattr(self, '_edp_output_last_warn', 0)
            if _total_calls >= 5 and _total_calls - _edp_output_last >= 10:
                _READ_TOOLS = {
                    "read_file", "list_files", "search_files", "glob_files",
                    "find_workspace_file", "get_workspace_info", "task_doc_read",
                    "task_doc_search", "activity_search",
                }
                _last5 = _recent[-5:]
                _all_reads = all(tc["name"] in _READ_TOOLS for tc in _last5)
                _no_produce = not any(tc["name"] in _PRODUCE_TOOLS for tc in _last5)
                if _all_reads and _no_produce:
                    self._edp_output_last_warn = _total_calls
                    messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM: OUTPUT-FIRST] 5 consecutive reads without producing output. "
                            "Next call MUST be write_file, execute_code, or execute_command."
                        ),
                    })

            # ══════════════════════════════════════════════════════════════
            # END EDP (Rule 3 removed — too fragile, depends on text matching)
            # ══════════════════════════════════════════════════════════════

            # ── Track consecutive errors for early reflection trigger ──
            _ERROR_SNIPPETS = ("503", "502", "timeout", "connection refused", "service unavailable")
            _round_has_error = any(
                any(ind in tc.get("output_snippet", "") for ind in _ERROR_SNIPPETS)
                or not tc.get("success", True)
                for tc in self._recent_tool_calls[-len(tool_calls):]
            ) if self._recent_tool_calls else False
            if _round_has_error:
                self._consecutive_error_calls += len(tool_calls)
            else:
                self._consecutive_error_calls = 0

            # Force reflection after 5 consecutive error calls (don't wait for interval)
            if (
                self._consecutive_error_calls >= 5
                and self._reflection_engine
                and self._total_tool_calls_this_run >= 5
            ):
                self._reflection_engine._last_quality_score = 0.2  # Force next should_reflect=True
                self._consecutive_error_calls = 0  # Reset to avoid triggering every round

            # ── P3 circuit-breaker: force-stop on consecutive all-failed rounds ──
            # Unlike the reflection nudge above (which is a no-op without a reflection
            # engine) and stuck detection (which keys off *repeated* actions), this
            # cuts off an agent whose every tool call keeps failing round after round,
            # even if it keeps varying its attempts. Caps wasted rounds (audit §5 P3).
            _round_calls = self._recent_tool_calls[-len(tool_calls):] if tool_calls else []
            self._consecutive_failed_rounds = update_failed_round_streak(
                self._consecutive_failed_rounds, _round_calls
            )
            if self._consecutive_failed_rounds >= MAX_CONSECUTIVE_FAILED_ROUNDS:
                logger.warning(
                    "agent_force_stopped_consecutive_failures",
                    agent=self.agent_name,
                    role=self.agent_role,
                    failed_rounds=self._consecutive_failed_rounds,
                    round=round_num,
                )
                await self._emit_event("agent_force_stopped", {
                    "agent_name": self.agent_name,
                    "agent_role": self.agent_role,
                    "reason": f"{self._consecutive_failed_rounds} consecutive rounds with all tool calls failing",
                    "round": round_num,
                })
                final_content = (
                    f"[AGENT FORCE-STOPPED: {self._consecutive_failed_rounds} consecutive "
                    f"rounds where every tool call failed. Halting to avoid wasted rounds.]"
                )
                break

            # ── Stuck detection: metacognitive analysis when repeating actions ──
            stuck_reason = self._detect_stuck()
            if stuck_reason:
                self._stuck_count += 1
                # Track wall-clock time in futile loop
                if self._no_progress_since is None:
                    self._no_progress_since = time.perf_counter()
                logger.warning(
                    "agent_stuck_detected",
                    agent=self.agent_name,
                    round=round_num,
                    reason=stuck_reason,
                    stuck_count=self._stuck_count,
                )

                # Time-based force-stop: if stuck for >5 minutes wall-clock, stop
                _futile_duration = time.perf_counter() - self._no_progress_since
                if _futile_duration > 300:  # 5 minutes
                    logger.warning(
                        "agent_force_stopped_time",
                        agent=self.agent_name,
                        futile_seconds=round(_futile_duration),
                        stuck_count=self._stuck_count,
                    )
                    await self._emit_event("agent_force_stopped", {
                        "agent_name": self.agent_name,
                        "agent_role": self.agent_role,
                        "reason": f"No progress for {round(_futile_duration)}s: {stuck_reason}",
                        "stuck_count": self._stuck_count,
                        "round": round_num,
                    })
                    final_content = (
                        f"[AGENT FORCE-STOPPED: No meaningful progress for "
                        f"{round(_futile_duration)}s. Last stuck reason: {stuck_reason}]"
                    )
                    break

                # Force-stop agent after MAX_STUCK_ESCALATIONS consecutive stucks
                if self._stuck_count >= MAX_STUCK_ESCALATIONS:
                    logger.warning(
                        "agent_force_stopped",
                        agent=self.agent_name,
                        stuck_count=self._stuck_count,
                        last_reason=stuck_reason,
                    )
                    await self._emit_event("agent_force_stopped", {
                        "agent_name": self.agent_name,
                        "agent_role": self.agent_role,
                        "reason": stuck_reason,
                        "stuck_count": self._stuck_count,
                        "round": round_num,
                    })
                    final_content = (
                        f"[AGENT FORCE-STOPPED: Repeated stuck pattern detected "
                        f"({self._stuck_count} consecutive). Last reason: {stuck_reason}]"
                    )
                    break

                # FIX C3: Progressive metacognition — escalating intervention schedule
                # stuck 1,2,3,5,7 (not just every 3rd) → faster response to loops
                metacog_text = ""
                _metacog_triggers = {1, 2, 3, 5, 7}
                if self._stuck_count in _metacog_triggers:
                    try:
                        from app.orchestration.metacognition import MetacognitiveAgent
                        from app.orchestration.models import ExecutionState
                        metacog = MetacognitiveAgent(llm=self._llm)
                        # Build recent actions summary for metacognition
                        recent_summary = "; ".join(
                            f"{tc['name']}({'OK' if tc.get('success') else 'FAIL'})"
                            for tc in self._recent_tool_calls[-10:]
                        )
                        dummy_state = ExecutionState(
                            task_id="", user_message=assignment.task_description
                        )
                        insight = await metacog.analyze_blockage(
                            state=dummy_state,
                            stuck_pattern=stuck_reason,
                            recent_actions=recent_summary,
                            agent_context=assignment.success_criteria,
                        )
                        if insight.concrete_next_action:
                            metacog_text = (
                                f"\n[METACOGNITIVE ANALYSIS]\n"
                                f"Root cause: {insight.root_cause}\n"
                                f"Wrong assumption: {'; '.join(insight.wrong_assumptions[:2])}\n"
                                f"What's actually needed: {'; '.join(insight.missing_prerequisites[:2])}\n"
                                f"Reframed approach: {insight.reframe}\n"
                                f">>> DO THIS NOW: {insight.concrete_next_action}\n"
                            )
                    except Exception as e:
                        logger.debug("metacognition_in_worker_failed", error=str(e))

                remaining = MAX_STUCK_ESCALATIONS - self._stuck_count
                # SPECIAL HANDLER for workspace path loop (Pattern 10):
                if "workspace path loop" in stuck_reason.lower():
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[SYSTEM: WORKSPACE PATH LOOP DETECTED — {stuck_reason}]\n"
                            "You are stuck because file tools see different paths than your shell.\n"
                            "IMMEDIATE DIAGNOSTICS:\n"
                            f"1. Run `get_workspace_info` — see the actual paths in use\n"
                            f"2. If files exist but tools can't find them, use `find_workspace_file` with the filename\n"
                            f"3. Use shell: `find /app -name \"*.md\" -type f 2>/dev/null` to locate files bypassing tools\n"
                            f"4. Once found, use full absolute path with tools\n"
                            f"5. DO NOT create directories or claim 'workspace empty' — the data exists\n"
                        ),
                    })
                else:
                    messages.append({
                    "role": "user",
                    "content": (
                        f"[SYSTEM: Repeated actions detected — {stuck_reason}. "
                        "You MUST change your approach. "
                        f"WARNING: {remaining} more repeated actions will force-terminate this agent.]\n"
                        f"{metacog_text}\n"
                        f"REMINDER — Your assignment: {assignment.task_description}\n"
                        f"Success criteria: {assignment.success_criteria}"
                    ),
                })
                # Trim history but keep enough for semantic patterns (6/7/8)
                self._recent_tool_calls = self._recent_tool_calls[-10:]

                await self._emit_event("agent_stuck", {
                    "agent_name": self.agent_name,
                    "agent_role": self.agent_role,
                    "reason": stuck_reason,
                    "round": round_num,
                    "stuck_count": self._stuck_count,
                })
            else:
                # Agent recovered — reset stuck counter and timer
                self._stuck_count = 0
                self._no_progress_since = None

            # ── Self-reflection checkpoint: pause and evaluate progress ──
            self._total_tool_calls_this_run += len(tool_calls)
            if self._reflection_engine and self._reflection_engine.should_reflect(
                round_num=round_num + 1,
                tool_calls=self._total_tool_calls_this_run,
            ):
                try:
                    recent_tools = [tc["name"] for tc in self._recent_tool_calls[-10:]]
                    recent_results = ""
                    for msg in reversed(messages[-6:]):
                        if msg.get("role") == "tool":
                            recent_results += msg.get("content", "")[:500] + "\n"
                    # Build full tool call history for reflection (detect failure loops)
                    _history_parts = []
                    for tc in self._recent_tool_calls:
                        status = "OK" if tc.get("success") else "FAIL"
                        snippet = tc.get("output_snippet", "")[:80]
                        elapsed = tc.get("elapsed_s", 0)
                        _history_parts.append(
                            f"  {tc['name']}({status}, {elapsed:.1f}s): {snippet}"
                        )
                    tool_call_history = "\n".join(_history_parts[-30:])
                    reflection_ctx = ReflectionContext(
                        task=assignment.task_description,
                        strategy=own_thinking[:300],
                        tools_used=recent_tools,
                        tool_results_summary=recent_results[:2000],
                        tool_call_history=tool_call_history,
                        current_output=final_content[:500],
                        round_num=round_num + 1,
                        total_rounds=self._max_rounds,
                        success_criteria=[assignment.success_criteria] if assignment.success_criteria else [],
                        agent_name=self.agent_name,
                        agent_role=self.agent_role,
                    )
                    reflection_result = await self._reflection_engine.reflect(
                        reflection_ctx, event_callback=self._event_callback
                    )
                    if reflection_result.needs_intervention:
                        inject_reflection(messages, reflection_result)
                        await self._emit_event("self_reflection", {
                            "agent_name": self.agent_name,
                            "quality_score": reflection_result.quality_score,
                            "on_track": reflection_result.on_track,
                            "strategy_change": reflection_result.strategy_change[:200],
                        })
                    if reflection_result.should_stop:
                        final_content = f"[SELF-REFLECTION STOP: {reflection_result.stop_reason}]"
                        break
                except Exception as e:
                    logger.debug("self_reflection_in_worker_failed", error=str(e))

            logger.info(
                "agent_round_complete",
                agent=self.agent_name,
                round=round_num,
                tools_called=len(tool_calls),
            )
            round_num += 1

        # FIX A4: Force structured self-assessment when agent finishes naturally
        # This produces a structured conclusion the orchestrator can evaluate accurately,
        # rather than relying on heuristic tail-extraction from raw output.
        if final_content and all_tool_calls and not final_content.startswith(("[AGENT FORCE", "(Agent ", "(Cancelled")):
            structured_suffix = await self._structured_self_assessment(
                messages, assignment, final_content
            )
            if structured_suffix:
                final_content = f"{final_content}\n\n{structured_suffix}"

        # Fallback synthesis: if loop exited without final content but tools were used
        _was_synthesized = False
        if not final_content and all_tool_calls:
            final_content = await self._force_synthesis(messages, assignment, all_tool_calls)
            if final_content and not final_content.startswith("("):
                _was_synthesized = True
            if _timed_out and final_content and not final_content.startswith("("):
                final_content = f"[TIMEOUT-SYNTHESIZED]: {final_content}"

        # Detect agent failure or partial completion
        _self_reflection_stopped = final_content.startswith("[SELF-REFLECTION STOP")
        _has_successful_tools = any(tc.success and tc.output for tc in all_tool_calls)
        if not _was_synthesized:
            _is_error_output = (
                final_content.startswith("(Agent ")
                or final_content.startswith("(Cancelled")
                or final_content.startswith("[AGENT FORCE-STOPPED")
                or final_content.startswith("(Synthesis failed")
            )
            _agent_failed = _is_error_output and _has_successful_tools
        else:
            _agent_failed = False

        if _agent_failed:
            # Preserve partial results from successful tool calls
            partial_parts = [f"Agent failed but gathered partial data:"]
            for tc in all_tool_calls:
                if tc.success and tc.output:
                    partial_parts.append(f"- {tc.tool_name}: {tc.output[:1500]}")
            partial_output = "\n".join(partial_parts[:20])
            # Prepend partial data to the error so downstream can use it
            final_content = f"{partial_output}\n\n[FAILURE]: {final_content}"

            # Emit structured error event
            failure_reason = final_content.split("[FAILURE]: ")[-1] if "[FAILURE]: " in final_content else final_content
            await self._emit_event("agent_failed", {
                "agent_name": self.agent_name,
                "agent_role": self.agent_role,
                "failure_reason": failure_reason,
                "successful_tool_count": sum(1 for tc in all_tool_calls if tc.success),
                "total_tool_count": len(all_tool_calls),
                "partial_data_preserved": True,
            })

        # A force-stop (no-progress / stuck-pattern / consecutive-failure breaker) is
        # always a failure, even with no successful tools to preserve as partial data —
        # otherwise `_agent_failed` (which requires _has_successful_tools) would let a
        # thrashing agent that never succeeded report success=True (audit §5 P3).
        _force_stopped = final_content.startswith("[AGENT FORCE-STOPPED")

        # ── F2: phantom-success guard ──────────────────────────────────────
        # An agent that demonstrably TRIED to produce a deliverable but landed zero
        # successful produce-tool calls must NOT report success — otherwise the
        # orchestrator burns turns/budget believing progress was made while nothing
        # reached disk (audit 2026-06-25 phantom-write).
        #
        # "Tried to produce" is detected from actual signals, never from role, so a
        # legitimate analysis turn (critic reviewing, no produce intent) is never
        # failed:
        #   A) a produce-tool call was dispatched this run (native OR recovered by
        #      F1) — present in all_tool_calls; or
        #   B) the FINAL answer itself is an unexecuted produce-tool JSON blob in
        #      prose (covers a tools-off wrap-up that describes a write that never
        #      ran). Reuses the strict recovery parser, so prose merely *mentioning*
        #      a produce tool does not count.
        #   C) the FINAL answer PASTES file-writing CODE in a fence but ran nothing
        #      (audit S1: deepseek pasted ``open(p,"w").write(...)``; gasifier pasted
        #      a whole solve.py). Structural idiom, not prose keywords.
        #   D) the ASSIGNMENT names a concrete file to WRITE (create-verb + file.ext)
        #      but the turn produced nothing (E-R7: Opus composed a 67K-char report
        #      INLINE and never called write_file — no code idiom, no tool JSON, so
        #      A/B/C all missed it). Combined with `not _produced_ok` below, this only
        #      fires when the turn produced ZERO deliverables — an analysis/review
        #      assignment (no named file) never trips it.
        _produced_ok = self._did_produce_deliverable(all_tool_calls)
        _wrote_a_file = any(
            tc.tool_name in _WRITE_FILE_TOOLS and tc.success for tc in all_tool_calls
        )
        _demands_file = _assignment_demands_file_write(
            assignment.task_description, assignment.success_criteria
        )
        _attempted_produce = any(
            tc.tool_name in _PRODUCE_TOOLS for tc in all_tool_calls
        ) or any(
            c["function"]["name"] in _PRODUCE_TOOLS
            for c in self._recover_tool_calls_from_content(final_content)
        ) or _describes_unexecuted_file_write(final_content) or _demands_file
        # Two phantom shapes:
        #  (i)  produced NOTHING at all while having produce-intent (original E-R7);
        #  (ii) R2/P100-1: the assignment demands a NAMED FILE but no write-file tool
        #       succeeded — even if other produce-tools (http_request/execute_code)
        #       ran. "Ran something" is not "wrote the deliverable". This catches the
        #       pentester that probed with http_request but never saved evidence.md.
        _phantom_produce = (_attempted_produce and not _produced_ok) or (
            _demands_file and not _wrote_a_file
        )
        if _phantom_produce:
            logger.warning(
                "agent_phantom_produce_no_success",
                agent=self.agent_name,
                role=self.agent_role,
                total_tools=len(all_tool_calls),
            )

        # No-op guard (task 3c44a93f bug #6a): a turn that dispatched zero tools
        # AND produced no substantive output did no work — it must not report
        # success, or the decision layer keeps dispatching on a phantom "success"
        # (Turns 6/8/10 of that run). Distinct from _phantom_produce (which needs
        # produce INTENT): this catches the fully-empty turn.
        _no_op_turn = self._is_no_op_turn(all_tool_calls, final_content)
        if _no_op_turn:
            logger.warning(
                "agent_no_op_turn",
                agent=self.agent_name,
                role=self.agent_role,
                output_chars=len((final_content or "").strip()),
            )

        # Self-reflection stop = partial success (agent did work but was halted).
        # Mark success=True only if agent completed naturally or was synthesized.
        _success = (
            not _agent_failed
            and not _self_reflection_stopped
            and not _force_stopped
            and not _phantom_produce
            and not _no_op_turn
        )
        # Exception: if self-reflection stopped but agent produced real output files,
        # treat as success (the stop was a "done enough" signal, not a failure).
        # A phantom-produce turn is never resurrected by this exception.
        if _self_reflection_stopped and _has_successful_tools and not _phantom_produce:
            _success = True

        return AgentResult(
            agent_name=self.agent_name,
            agent_role=self.agent_role,
            thinking=AgentThinking(
                agent_name=self.agent_name,
                agent_role=self.agent_role,
                thinking=own_thinking,
            ),
            tool_calls=all_tool_calls,
            # Guards above intentionally read the RAW final_content (phantom /
            # no-op detection needs the model's verbatim text). Only the persisted
            # output is sanitized, so a fabricated <tool_response> cannot poison
            # downstream turns as ground truth (E8).
            output=_sanitize_output_for_context(final_content),
            success=_success,
            produced_deliverable=_produced_ok,
            error=(
                final_content.split("[FAILURE]: ")[-1] if _agent_failed
                else "Attempted to produce a deliverable but no produce-tool "
                     "call succeeded (nothing reached disk)." if _phantom_produce
                else "No-op turn: agent dispatched no tools and produced no "
                     "substantive output." if _no_op_turn
                else ""
            ),
        )

    def _valid_tool_names(self) -> set[str]:
        """Names of every tool this worker can call (from its function schemas).

        Mirrors the set llm_client builds for prompted providers, so the prose
        recovery only matches real, callable tools — never arbitrary JSON or a
        tool name merely mentioned in prose.
        """
        return {
            name
            for t in self._tool_schemas
            if (name := (t.get("function", t) or {}).get("name"))
        }

    def _recover_tool_calls_from_content(self, content: str) -> list[dict[str, Any]]:
        """F1 net: recover a tool-call this worker's model leaked into prose.

        Boundary recovery in llm_client already covers the normal tooled turn;
        this remains for the F2 phantom-success guard, which inspects the
        tools-off wrap-up `final_content` (a synthesis call that may carry no
        `tools`, so the boundary couldn't recover it). Delegates to the shared
        pure module so the gating logic lives in exactly one place.
        """
        return recover_tool_calls(content, self._valid_tool_names())

    def _detect_stuck(self) -> str:
        """Detect if the agent is stuck in a loop.

        Thin wrapper over ``stuck_detector.detect_stuck`` (pure, unit-tested,
        extracted in audit C1/C3); supplies this worker's recent tool-call history.
        """
        return detect_stuck(self._recent_tool_calls)

    @staticmethod
    def _did_produce_deliverable(tool_calls: list["ToolCall"]) -> bool:
        """Ground-truth: did a PRODUCE tool run SUCCESSFULLY this turn? (E3)

        True iff at least one write/execute tool succeeded — work that reached
        disk / acted on the world. Read/analysis tools and prose do NOT count.
        This is the signal the decision/loop layer needs to distinguish a
        prose-only turn (talk) from real production, WITHOUT changing `success`
        (a legitimate analysis turn stays success=True but produces nothing).
        """
        return any(
            tc.tool_name in _PRODUCE_TOOLS and tc.success for tc in tool_calls
        )

    @staticmethod
    def _is_no_op_turn(tool_calls: list["ToolCall"], final_content: str) -> bool:
        """A turn that did NOTHING: zero tool calls AND empty output.

        Task 3c44a93f bug #6a: Turns 6/8/10 dispatched no tools and produced
        output_chars=0, yet were reported success=true — fooling the decision
        layer into continuing. The gate is the ABSENCE OF BOTH signals (no tool
        dispatched AND no output at all) — never a turn counter, never a length
        heuristic. A turn with ANY real output (even a short tools-off wrap-up
        like "Report finished.") is NOT a no-op — that is the deliberate
        distinction the phantom-write audit insisted on (don't kill legitimate
        analysis/wrap-up turns).
        """
        if tool_calls:
            return False
        return not (final_content or "").strip()

    @staticmethod
    def _fix_orphan_tool_use(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reconcile tool_use ↔ tool_result so history is provider-valid.

        Thin wrapper over ``message_repair.fix_orphan_tool_use`` (pure, unit-tested,
        extracted in audit C1/C3).
        """
        return fix_orphan_tool_use(messages)

    async def _structured_self_assessment(
        self,
        messages: list[dict[str, Any]],
        assignment: AgentAssignment,
        current_output: str,
    ) -> str:
        """FIX A4: Generate structured self-assessment for orchestrator evaluation.

        Agent produces: what it DID, what it FOUND, what's STILL MISSING.
        This structured output gives the orchestrator accurate evaluation data
        rather than relying on heuristic tail-extraction from raw output.

        Returns empty string on failure (non-blocking enhancement).
        """
        if len(current_output) < 200:
            return ""
        try:
            assess_messages = [
                {"role": "system", "content": (
                    "You just completed a task. Produce a BRIEF structured assessment. "
                    "Be factual and specific. Format:\n"
                    "## ACCOMPLISHED\n- (bullet list of what you completed)\n"
                    "## KEY FINDINGS\n- (bullet list of important data/results)\n"
                    "## STILL MISSING\n- (what remains undone, or 'Nothing — task complete')\n"
                    "Keep each section to 3-5 bullets max. Be honest about gaps."
                )},
                {"role": "user", "content": (
                    f"Task: {assignment.task_description[:500]}\n"
                    f"Success criteria: {assignment.success_criteria[:300]}\n"
                    f"Your output (tail): {current_output[-2000:]}"
                )},
            ]
            result = await self._llm.chat(
                messages=assess_messages, temperature=0.2, max_tokens=1024
            )
            content = result.get("content", "")
            if content and "ACCOMPLISHED" in content:
                return content[:2000]
        except Exception:
            pass
        return ""

    async def _force_synthesis(
        self,
        messages: list[dict[str, Any]],
        assignment: AgentAssignment,
        all_tool_calls: list[ToolCall] | None = None,
    ) -> str:
        """Force agent to synthesize tool results into a final answer.

        FIX D1: Uses all_tool_calls records (which preserve full output)
        rather than just message-level tool content (which may be compressed).
        """
        tool_data_parts: list[str] = []

        # keep_head_tail, not a head-only [:8000]: this step SYNTHESISES the
        # agent's final answer, and a command's final result usually lives at the
        # END of its output — a head slice drops it exactly when concluding (N4).
        from app.orchestration.text_budget import keep_head_tail

        # Primary source: ToolCall records (uncompressed, authoritative)
        if all_tool_calls:
            for tc in all_tool_calls:
                if tc.success and tc.output and len(tc.output) > 30:
                    header = f"[{tc.tool_name}] "
                    tool_data_parts.append(header + keep_head_tail(tc.output, 6000, 2000))

        # Fallback: message-level tool responses (if no ToolCall records)
        if not tool_data_parts:
            for msg in messages:
                if msg.get("role") == "tool":
                    content = msg.get("content", "")
                    if len(content) > 30:
                        tool_data_parts.append(keep_head_tail(content, 6000, 2000))

        tool_summary = "\n---\n".join(tool_data_parts[-20:]) if tool_data_parts else "(no data collected)"

        synth_messages = [
            {"role": "system", "content": (
                f"You are {self.agent_name}. Synthesize ALL tool results below into a comprehensive answer.\n"
                f"Assignment: {assignment.task_description}\n"
                f"Success criteria: {assignment.success_criteria}\n"
                "Include ALL findings, data, and accomplishments. Do NOT omit any results."
            )},
            {"role": "user", "content": f"Tool results ({len(tool_data_parts)} calls):\n{tool_summary[:50000]}"},
        ]

        try:
            result = await self._llm.chat(messages=synth_messages, temperature=0.3, max_tokens=16384)
            return result.get("content", "(Synthesis failed)")
        except Exception as e:
            return f"(Synthesis failed: {e})"

    def _account_usage(self, usage: Optional[dict[str, Any]]) -> None:
        """Record one LLM call's usage into journal + per-agent + per-task budget.

        Single sink for the three accounting call sites (main react-loop call and
        the two tools-off wrap-up calls). Fail-open: accounting must never break
        the agent loop. Does NOT calibrate the estimator — that needs a fresh
        outbound estimate (`self._last_estimate`) and stays at the main call site.
        """
        try:
            _model = getattr(self._llm, "model", "unknown")
            self._token_journal.record(
                usage, model=_model, caller=self.agent_name or "unknown",
            )
            # Feed BOTH tiers from the same usage: per-turn (reset each turn) and
            # per-task (registry, whole-task lifetime — never reset per turn).
            self._turn_budget.add(usage)
            if usage:
                from app.core.execution_context import current_task_id
                from app.core.token_governor import get_task_budget
                _tid = current_task_id.get("")
                if _tid:
                    try:
                        from app.config import get_settings
                        _lim = int(get_settings().task_token_budget)
                    except Exception:
                        _lim = 2_000_000
                    get_task_budget(_tid, _lim).add(usage)
        except Exception:
            pass  # accounting must never break the agent loop

    def _compress_context_if_needed(
        self, messages: list[dict[str, Any]], force: bool = False
    ) -> list[dict[str, Any]]:
        """Smart context compression when messages exceed threshold.

        Strategy:
        - Calculate total char size of all messages
        - If below threshold, return unchanged (unless force=True)
        - If above threshold, keep:
            • messages[0] (system prompt) — agent identity + full assignment
            • messages[1] (user message) — assignment + success criteria
            • last N messages (recent tool interactions)
        - Compress middle messages into a summary that re-states the objective

        This allows agents to run indefinitely without hitting context limits.
        Full tool outputs are always in ACTIVITY.md (recoverable via activity_search).

        Args:
            messages: Current message list.
            force: If True, compress regardless of threshold (for error recovery).
        """
        # Measure conversation size EXCLUDING system prompt (which is never truncated).
        # This ensures large system prompts don't trigger premature compression.
        conversation_chars = sum(
            len(m.get("content", "")) if isinstance(m.get("content"), str)
            else sum(len(b.get("text", "")) for b in m.get("content", []) if isinstance(b, dict))
            for m in messages[1:]
        )

        # Adaptive threshold: CJK/emoji content uses more tokens per character,
        # so we trigger compression earlier to prevent context overflow.
        _sample = "".join(
            m.get("content", "")[:200] if isinstance(m.get("content"), str) else ""
            for m in messages[1:6]
        )
        _chars_per_token = _estimate_chars_per_token(_sample)
        _adaptive_threshold = int(self._compress_threshold * (_chars_per_token / 4.0))
        # Budget-aware: as the TASK budget runs low, compress earlier so the same
        # hard ceiling buys more useful turns (does NOT raise the ceiling). Best-
        # effort — never let a budget-lookup error change compression behaviour.
        try:
            from app.core.execution_context import current_task_id as _cti
            from app.core.token_governor import get_task_budget as _gtb
            from app.config import get_settings as _gs3
            _tid2 = _cti.get("")
            if _tid2:
                _lvl = _gtb(_tid2, int(_gs3().task_token_budget)).status().level
                _adaptive_threshold = int(_adaptive_threshold * budget_pressure_factor(_lvl))
        except Exception:
            pass

        # Token Governor B: record a calibrated token estimate of the OUTBOUND
        # context so Step 3's observe() can compare against real prompt_tokens.
        try:
            _model = getattr(self._llm, "model", "")
            self._last_estimate = self._token_estimator.estimate(messages, _model)
        except Exception:
            self._last_estimate = 0

        if not force and conversation_chars <= _adaptive_threshold:
            return messages

        # Anchor messages: system prompt + initial user message (assignment).
        # These MUST survive compression — losing them causes agents to forget
        # what they're doing and loop aimlessly.
        _NUM_ANCHOR_MSGS = 2  # messages[0]=system, messages[1]=user/assignment

        if len(messages) <= _CONTEXT_KEEP_RECENT_MESSAGES + _NUM_ANCHOR_MSGS:
            return messages  # Too few to compress

        anchor_msgs = messages[:_NUM_ANCHOR_MSGS]
        # Find a clean split point: never start recent_msgs at a "tool" message
        # (that would orphan it from its assistant turn). Scan backwards to find
        # a split that starts at assistant or user — preserving tool context.
        split_idx = len(messages) - _CONTEXT_KEEP_RECENT_MESSAGES
        while split_idx < len(messages) and messages[split_idx].get("role") == "tool":
            split_idx -= 1  # Include the preceding assistant message
        if split_idx <= _NUM_ANCHOR_MSGS:
            split_idx = _NUM_ANCHOR_MSGS  # Don't overlap with anchors
        recent_msgs = messages[split_idx:]
        middle_msgs = messages[_NUM_ANCHOR_MSGS:split_idx]

        # Priority-based eviction: protect high-value messages from middle section
        if middle_msgs and len(middle_msgs) > _MAX_PRIORITY_PROTECTED:
            assignment_text = ""
            if len(messages) > 1:
                first_user = messages[1].get("content", "")
                if isinstance(first_user, str):
                    assignment_text = first_user
            keywords = [w.lower() for w in assignment_text.split() if len(w) > 4][:20]

            scored = [
                (i, _score_message_relevance(m, keywords))
                for i, m in enumerate(middle_msgs)
                if m.get("role") != "tool"
            ]
            scored.sort(key=lambda x: x[1], reverse=True)

            protect_indices = set()
            for idx, sc in scored[:_MAX_PRIORITY_PROTECTED]:
                if sc >= 0.4:
                    protect_indices.add(idx)
                    # I-6 (context-arch audit): a protected assistant turn may have
                    # dispatched SEVERAL tool calls → several consecutive tool
                    # messages carry its results (the numbers). Keep ALL of them, not
                    # just idx+1 — otherwise the 2nd+ tool result is orphaned and
                    # fix_orphan_tool_use overwrites it with a placeholder, losing the
                    # very data (CGE/closure) the turn produced.
                    _j = idx + 1
                    while _j < len(middle_msgs) and middle_msgs[_j].get("role") == "tool":
                        protect_indices.add(_j)
                        _j += 1

            if protect_indices:
                priority_msgs = [middle_msgs[i] for i in sorted(protect_indices)]
                middle_msgs = [m for i, m in enumerate(middle_msgs) if i not in protect_indices]
                recent_msgs = priority_msgs + recent_msgs

        # FIX A1: Preserve ALL strategy-critical messages from middle before discarding.
        # These contain stuck-detection overrides, metacognitive breakthroughs, strategy
        # pivots, self-reflection injections, and EDP warnings.
        # Without preserving them, agent reverts to original (proven-stuck) approach
        # after compression, creating an infinite stuck→compress→stuck loop.
        #
        # Detection: any message tagged _strategy_pin=True, OR matching known patterns.
        strategy_messages: list[str] = []
        _STRATEGY_PATTERNS = (
            "[SYSTEM:", "[METACOGNITIVE", "🪞 SELF-REFLECTION",
            "[FORECAST PIVOT]", "[INSTALL REQUIRED]", "3-STRIKE",
            "OUTPUT-FIRST", "MANDATORY DIRECTIVE",
        )
        for msg in middle_msgs:
            # Pinned messages always survive compression
            if msg.get("_strategy_pin"):
                strategy_messages.append(str(msg.get("content", ""))[:1200])
                continue
            # I-4 (context-arch audit): strategy injections arrive as role="user"
            # (SYSTEM nudges) AND role="system" (self-reflection / metacognitive
            # inserts). The old check only scanned "user", so a system-role
            # self-reflection was evicted on compression → the agent reverted to the
            # proven-stuck approach. Scan BOTH roles.
            if msg.get("role") in ("user", "system"):
                content = str(msg.get("content", ""))
                if any(pat in content for pat in _STRATEGY_PATTERNS):
                    strategy_messages.append(content[:1200])

        # User message (anchor[1]) is NEVER fully discarded — it contains the
        # assignment and success criteria which the agent must remember.
        # Only trim if extremely long (>20KB), keeping the critical first portion.
        _USER_MSG_MAX = 20000
        if len(anchor_msgs) > 1:
            user_content = str(anchor_msgs[1].get("content", ""))
            if len(user_content) > _USER_MSG_MAX:
                anchor_msgs = list(anchor_msgs)  # copy to avoid mutation
                anchor_msgs[1] = {
                    **anchor_msgs[1],
                    "content": user_content[:_USER_MSG_MAX] + "\n[...user message trimmed for context budget]",
                }

        # Compress middle messages
        compressed: list[dict[str, Any]] = list(anchor_msgs)

        # Build comprehensive summary preserving ALL structured data from compressed history.
        # Strategy: extract structured findings from every tool result (not just last),
        # plus assistant reasoning, plus URLs/numbers/key-value pairs.
        summary_parts: list[str] = []
        findings_parts: list[str] = []
        urls_found: list[str] = []
        tool_call_count = 0
        tool_success_count = 0
        last_substantial_results: list[str] = []

        for msg in middle_msgs:
            role = msg.get("role", "")
            if role == "tool":
                tool_call_count += 1
                content = str(msg.get("content", ""))
                first_line = content.split("\n")[0][:100] if content else ""
                if "error" not in first_line.lower():
                    tool_success_count += 1
                    if len(content) > 100:
                        # Keep tail of each substantial result (not just last one)
                        tail = content[-600:].strip() if len(content) > 600 else content.strip()
                        last_substantial_results.append(tail)
                        # Extract structured lines AND prose conclusions from tool results
                        import re as _re
                        for line in content.strip().split("\n")[:80]:
                            line = line.strip()
                            if len(line) < 15:
                                continue
                            if line.startswith("[") and "TRUNCATED" in line:
                                continue
                            # Structured data patterns
                            if line.startswith(("-", "•", "*", "✓", "✅", "→", "►")):
                                findings_parts.append(line[:250])
                            elif line[0].isdigit() and "." in line[:4]:
                                findings_parts.append(line[:250])
                            elif "http" in line or "://" in line:
                                url_match = _re.search(r'https?://\S+', line)
                                if url_match:
                                    urls_found.append(url_match.group()[:200])
                                findings_parts.append(line[:250])
                            elif ": " in line[:50] and len(line) > 20:
                                findings_parts.append(line[:250])
                            elif len(line) > 60 and line[0].isupper() and not line.startswith(("{", "[", "<", "HTTP")):
                                # Prose sentence that looks like a conclusion
                                findings_parts.append(line[:250])
            elif role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tools_used = [tc.get("function", {}).get("name", "?") for tc in tool_calls[:5]]
                    summary_parts.append(f"Called: {', '.join(tools_used)}")
                content = msg.get("content", "")
                if content and len(content) > 50:
                    # FIX MODERATE #4: Capture more reasoning (1000 chars) and prioritize
                    # tail where conclusions/decisions live, not just the head.
                    if len(content) <= 1000:
                        summary_parts.append(f"Thought: {content}")
                    else:
                        head = content[:400]
                        tail = content[-600:]
                        summary_parts.append(f"Thought: {head}[...]{tail}")

        # Extract assignment reminder from the anchor user message
        assignment_reminder = ""
        if len(anchor_msgs) > 1:
            user_content = str(anchor_msgs[1].get("content", ""))
            assignment_reminder = user_content[:500]

        # Build key findings section — deduplicate and cap at 10KB
        findings_text = ""
        if findings_parts:
            unique_findings = list(dict.fromkeys(findings_parts))[:40]
            findings_text = (
                "\n📋 Key findings from compressed history:\n"
                + "\n".join(f"  • {f}" for f in unique_findings)
                + "\n"
            )
            if len(findings_text) > 10000:
                findings_text = findings_text[:10000] + "\n  [...]"

        # URLs section (deduped)
        urls_text = ""
        if urls_found:
            unique_urls = list(dict.fromkeys(urls_found))[:15]
            urls_text = "\n🔗 URLs encountered:\n" + "\n".join(f"  • {u}" for u in unique_urls) + "\n"

        # Include last 3 substantial results (not just 1) for continuity
        last_results_text = ""
        if last_substantial_results:
            recent_results = last_substantial_results[-3:]
            last_results_text = "\n📌 Recent substantial results (latest last):\n"
            for i, r in enumerate(recent_results, 1):
                last_results_text += f"--- Result {i} ---\n{r[:1500]}\n"
            if len(last_results_text) > 5000:
                last_results_text = last_results_text[:5000] + "\n[...]"

        # FIX CRITICAL #3 (cont): Build strategy override section from preserved messages
        strategy_text = ""
        if strategy_messages:
            strategy_text = (
                "\n🚨 ACTIVE STRATEGY OVERRIDES (from system interventions — DO NOT ignore):\n"
                + "\n".join(f"  {s}" for s in strategy_messages[-3:])
                + "\n"
            )

        compressed_summary = (
            f"[CONTEXT COMPRESSED — {tool_call_count} tool calls "
            f"({tool_success_count} succeeded), {len(middle_msgs)} messages summarized]\n"
            f"Key actions: {'; '.join(summary_parts[-10:])}\n"
            f"{findings_text}"
            f"{urls_text}"
            f"{last_results_text}\n"
            f"{strategy_text}"
            f"⚠️ REMINDER — Your current objective (DO NOT deviate):\n"
            f"{assignment_reminder}\n\n"
            f"[Full history preserved in ACTIVITY.md — use activity_search to retrieve]\n"
            f"[Structured progress in LEDGER.jsonl — all prior findings are tracked there]"
        )

        compressed.append({
            "role": "user",
            "content": compressed_summary,
        })

        # Add recent messages (uncompressed)
        compressed.extend(recent_msgs)

        logger.info(
            "agent_context_compressed",
            agent=self.agent_name,
            original_msgs=len(messages),
            compressed_msgs=len(compressed),
            original_chars=conversation_chars,
            compressed_chars=sum(len(str(m.get("content", ""))) for m in compressed),
        )

        # Fix tool_use/tool_result mismatches caused by slicing
        return self._fix_orphan_tool_use(compressed)

    async def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event via callback (best-effort)."""
        if self._event_callback is None:
            return
        try:
            await self._event_callback({"event_type": event_type, **data})
        except Exception:
            pass


def _build_tool_catalog_text(schemas: list[dict[str, Any]]) -> str:
    """Build a concise tool catalog from schemas for prompt injection."""
    if not schemas:
        return "(No tools available)"

    lines: list[str] = []
    for schema in schemas[:50]:  # Cap at 50 tools in catalog display
        func = schema.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")[:80]
        lines.append(f"- `{name}`: {desc}")

    return "\n".join(lines)
