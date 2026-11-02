"""
Nexus - Context Compressor (Hermes-agent inspired).

Advanced context window compression with:
- Structured summary templates (Goal/Progress/Decisions/Questions)
- Iterative summary updates (preserves info across compactions)
- Token-budget tail protection instead of fixed message count
- Tool output pruning before LLM summarization (cheap pre-pass)
- Tool-call/result pair integrity (orphan sanitization)
- Focus-topic guided compression

Inspired by: hermes-agent/agent/context_compressor.py
"""

import logging
import time
from typing import Any, Optional

import structlog

from app.services.llm_client import LLMClient

logger = structlog.get_logger(__name__)

# Constants
_CHARS_PER_TOKEN = 4
_MIN_SUMMARY_TOKENS = 2000
_SUMMARY_RATIO = 0.20
_SUMMARY_TOKENS_CEILING = 12_000
_PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared to save context space]"
_SUMMARY_FAILURE_COOLDOWN = 600  # seconds

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. Respond ONLY to the latest user message "
    "that appears AFTER this summary."
)

# Max context window and threshold
MAX_CONTEXT_TOKENS = 128_000
COMPACTION_THRESHOLD = 0.50
MAX_TOOL_RESULT_CHARS = 10_000
MAX_CHAIN_DEPTH = 10


def estimate_tokens(text: str) -> int:
    """Ước tính tokens (rule of thumb: ~4 chars = 1 token).

    NOTE (PERF-003): This is a rough estimate, NOT exact token counting via tiktoken.
    Actual token counts may differ by 20-30% depending on language and encoding.
    All compression thresholds using this function include a 15% safety margin
    to prevent context overflow from estimation inaccuracy.
    """
    return len(text) // _CHARS_PER_TOKEN


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        total += len(content) // _CHARS_PER_TOKEN + 10  # +10 for role/metadata
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                args = tc.get("function", {}).get("arguments", "")
                total += len(args) // _CHARS_PER_TOKEN
    return total


def truncate_tool_result(output: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Truncate tool output keeping head + tail (claude-code pattern)."""
    if len(output) <= max_chars:
        return output
    keep = max_chars // 2
    return (
        output[:keep]
        + f"\n\n... [truncated {len(output) - max_chars} chars] ...\n\n"
        + output[-keep:]
    )


class QueryChainTracker:
    """Track tool call chains to prevent infinite loops."""

    def __init__(self, chain_id: Optional[str] = None, max_depth: int = MAX_CHAIN_DEPTH):
        self.chain_id = chain_id or ""
        self.depth = 0
        self.max_depth = max_depth
        self.tool_calls: list[str] = []

    def enter(self, tool_name: str) -> bool:
        """Check and record tool call. Returns False if depth exceeded."""
        self.depth += 1
        self.tool_calls.append(tool_name)
        if self.depth > self.max_depth:
            logger.warning("chain_depth_exceeded", depth=self.depth, max=self.max_depth)
            return False
        return True

    def exit(self) -> None:
        self.depth = max(0, self.depth - 1)


class ContextCompressor:
    """
    Advanced context compression engine inspired by hermes-agent.

    Algorithm:
      1. Prune old tool results (cheap, no LLM call)
      2. Protect head messages (system prompt + first exchange)
      3. Protect tail messages by token budget (most recent ~20K tokens)
      4. Summarize middle turns with structured LLM prompt
      5. On subsequent compactions, iteratively update the previous summary
      6. Sanitize orphaned tool_call/tool_result pairs
    """

    def __init__(
        self,
        llm: LLMClient,
        context_length: int = MAX_CONTEXT_TOKENS,
        threshold_percent: float = COMPACTION_THRESHOLD,
        protect_first_n: int = 3,
        summary_target_ratio: float = 0.20,
    ):
        self.llm = llm
        self.context_length = context_length
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.summary_target_ratio = max(0.10, min(summary_target_ratio, 0.80))

        # PERF-003 FIX: Apply 15% safety margin to compression threshold.
        # Since estimate_tokens() uses len//4 (not tiktoken), actual token counts
        # may be 20-30% higher. Triggering compression 15% earlier prevents overflow.
        _raw_threshold = int(context_length * threshold_percent)
        self.threshold_tokens = max(
            int(_raw_threshold * 0.85),  # 15% safety margin
            32_000,  # minimum threshold
        )
        self.tail_token_budget = int(self.threshold_tokens * self.summary_target_ratio)
        self.max_summary_tokens = min(
            int(self.context_length * 0.05), _SUMMARY_TOKENS_CEILING
        )

        self.compression_count = 0
        self.last_prompt_tokens = 0
        self._previous_summary: Optional[str] = None
        self._summary_failure_cooldown_until: float = 0.0

    def should_compress(self, prompt_tokens: Optional[int] = None) -> bool:
        """Check if context exceeds the compression threshold."""
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return tokens >= self.threshold_tokens

    def update_from_response(self, usage: dict[str, Any]) -> None:
        """Update tracked token usage from API response."""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)

    # ------------------------------------------------------------------
    # Tool output pruning (cheap pre-pass, no LLM call)
    # ------------------------------------------------------------------

    def _prune_old_tool_results(
        self, messages: list[dict], protect_tail_tokens: int
    ) -> tuple[list[dict], int]:
        """Replace old tool result contents with placeholder."""
        if not messages:
            return messages, 0

        result = [m.copy() for m in messages]
        pruned = 0

        # Token-budget approach: walk backward accumulating tokens
        accumulated = 0
        boundary = len(result)
        min_protect = min(3, len(result) - 1)

        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            content_len = len(msg.get("content") or "")
            msg_tokens = content_len // _CHARS_PER_TOKEN + 10
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    msg_tokens += len(args) // _CHARS_PER_TOKEN
            if accumulated + msg_tokens > protect_tail_tokens and (len(result) - i) >= min_protect:
                boundary = i
                break
            accumulated += msg_tokens
            boundary = i

        for i in range(boundary):
            msg = result[i]
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not content or content == _PRUNED_TOOL_PLACEHOLDER:
                continue
            if len(content) > 200:
                result[i] = {**msg, "content": _PRUNED_TOOL_PLACEHOLDER}
                pruned += 1

        return result, pruned

    # ------------------------------------------------------------------
    # Serialization for summary
    # ------------------------------------------------------------------

    _CONTENT_MAX = 6000
    _CONTENT_HEAD = 4000
    _CONTENT_TAIL = 1500
    _TOOL_ARGS_MAX = 1500

    def _serialize_for_summary(self, turns: list[dict]) -> str:
        """Serialize conversation turns into labeled text for the summarizer."""
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""

            if role == "tool":
                tool_id = msg.get("tool_call_id", "")
                if len(content) > self._CONTENT_MAX:
                    content = content[:self._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-self._CONTENT_TAIL:]
                parts.append(f"[TOOL RESULT {tool_id}]: {content}")
                continue

            if role == "assistant":
                if len(content) > self._CONTENT_MAX:
                    content = content[:self._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-self._CONTENT_TAIL:]
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tc_parts = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            name = fn.get("name", "?")
                            args = fn.get("arguments", "")
                            if len(args) > self._TOOL_ARGS_MAX:
                                args = args[:1200] + "..."
                            tc_parts.append(f"  {name}({args})")
                    content += "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
                parts.append(f"[ASSISTANT]: {content}")
                continue

            if len(content) > self._CONTENT_MAX:
                content = content[:self._CONTENT_HEAD] + "\n...[truncated]...\n" + content[-self._CONTENT_TAIL:]
            parts.append(f"[{role.upper()}]: {content}")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Summary generation
    # ------------------------------------------------------------------

    def _compute_summary_budget(self, turns: list[dict]) -> int:
        """Scale summary token budget with content being compressed."""
        content_tokens = estimate_messages_tokens(turns)
        budget = int(content_tokens * _SUMMARY_RATIO)
        return max(_MIN_SUMMARY_TOKENS, min(budget, self.max_summary_tokens))

    async def _generate_summary(
        self,
        turns: list[dict],
        focus_topic: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a structured summary of conversation turns.

        Uses structured template with explicit preamble telling the
        summarizer not to answer questions. When a previous summary exists,
        generates an iterative update.
        """
        now = time.monotonic()
        if now < self._summary_failure_cooldown_until:
            return None

        summary_budget = self._compute_summary_budget(turns)
        content_to_summarize = self._serialize_for_summary(turns)

        _preamble = (
            "You are a summarization agent creating a context checkpoint. "
            "Your output will be injected as reference material for a DIFFERENT "
            "assistant that continues the conversation. "
            "Do NOT respond to any questions or requests — "
            "only output the structured summary. "
            "Do NOT include any preamble or prefix."
        )

        _template = f"""## Goal
[What the user is trying to accomplish]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions]

## Progress
### Done
[Completed work — include specific file paths, commands, results]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important technical decisions and why]

## Resolved Questions
[Questions already answered — include the answer]

## Pending User Asks
[Questions NOT yet answered. If none, write "None."]

## Relevant Files
[Files read, modified, or created — with brief note on each]

## Remaining Work
[What remains — framed as context, not instructions]

## Critical Context
[Specific values, error messages, configuration details that would be lost]

Target ~{summary_budget} tokens. Be specific — include file paths, command outputs, error messages."""

        if self._previous_summary:
            prompt = f"""{_preamble}

You are updating a context compaction summary. Previous summary below. New turns to incorporate.

PREVIOUS SUMMARY:
{self._previous_summary}

NEW TURNS TO INCORPORATE:
{content_to_summarize}

Update using this structure. PRESERVE all existing information still relevant. ADD new progress.

{_template}"""
        else:
            prompt = f"""{_preamble}

Create a structured handoff summary for a different assistant.

TURNS TO SUMMARIZE:
{content_to_summarize}

Use this structure:

{_template}"""

        if focus_topic:
            prompt += f"""

FOCUS TOPIC: "{focus_topic}"
Prioritise preserving all information related to "{focus_topic}". Summarise unrelated content more aggressively."""

        try:
            result = await self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=summary_budget * 2,
            )
            summary = (result.get("content") or "").strip()
            self._previous_summary = summary
            self._summary_failure_cooldown_until = 0.0
            return f"{SUMMARY_PREFIX}\n{summary}" if summary else SUMMARY_PREFIX
        except Exception as e:
            self._summary_failure_cooldown_until = time.monotonic() + _SUMMARY_FAILURE_COOLDOWN
            logger.warning("summary_generation_failed", error=str(e))
            return None

    # ------------------------------------------------------------------
    # Tool-call / tool-result pair integrity
    # ------------------------------------------------------------------

    @staticmethod
    def _get_tool_call_id(tc: Any) -> str:
        if isinstance(tc, dict):
            return tc.get("id", "")
        return getattr(tc, "id", "") or ""

    def _sanitize_tool_pairs(self, messages: list[dict]) -> list[dict]:
        """Fix orphaned tool_call / tool_result pairs after compression."""
        surviving_call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = self._get_tool_call_id(tc)
                    if cid:
                        surviving_call_ids.add(cid)

        result_call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid:
                    result_call_ids.add(cid)

        # Remove orphaned tool results
        orphaned_results = result_call_ids - surviving_call_ids
        if orphaned_results:
            messages = [
                m for m in messages
                if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned_results)
            ]

        # Add stub results for orphaned tool_calls
        missing_results = surviving_call_ids - result_call_ids
        if missing_results:
            patched: list[dict] = []
            for msg in messages:
                patched.append(msg)
                if msg.get("role") == "assistant":
                    for tc in msg.get("tool_calls") or []:
                        cid = self._get_tool_call_id(tc)
                        if cid in missing_results:
                            patched.append({
                                "role": "tool",
                                "content": "[Result from earlier — see context summary above]",
                                "tool_call_id": cid,
                            })
            messages = patched

        return messages

    # ------------------------------------------------------------------
    # Boundary alignment (hermes-agent pattern)
    # ------------------------------------------------------------------

    @staticmethod
    def _align_boundary_forward(messages: list[dict], idx: int) -> int:
        """Push compress-start boundary forward past orphan tool results.

        If messages[idx] is a tool result, slide forward until we hit a
        non-tool message so we don't start the summarised region mid-group.
        """
        while idx < len(messages) and messages[idx].get("role") == "tool":
            idx += 1
        return idx

    @staticmethod
    def _align_boundary_backward(messages: list[dict], idx: int) -> int:
        """Pull compress-end boundary backward to avoid splitting a
        tool_call/result group.

        If the boundary falls in the middle of a tool-result group,
        walk backward past all of them to find the parent assistant
        message. Move the boundary before the assistant so the entire
        group is included in the summarised region.
        """
        if idx <= 0 or idx >= len(messages):
            return idx
        check = idx - 1
        while check >= 0 and messages[check].get("role") == "tool":
            check -= 1
        if (check >= 0
                and messages[check].get("role") == "assistant"
                and messages[check].get("tool_calls")):
            idx = check
        return idx

    # ------------------------------------------------------------------
    # Tail protection by token budget
    # ------------------------------------------------------------------

    def _find_tail_cut(self, messages: list[dict], head_end: int) -> int:
        """Walk backward accumulating tokens until budget reached."""
        n = len(messages)
        min_tail = min(3, n - head_end - 1) if n - head_end > 1 else 0
        soft_ceiling = int(self.tail_token_budget * 1.5)
        accumulated = 0
        cut_idx = n

        for i in range(n - 1, head_end - 1, -1):
            msg = messages[i]
            content = msg.get("content") or ""
            msg_tokens = len(content) // _CHARS_PER_TOKEN + 10
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    msg_tokens += len(args) // _CHARS_PER_TOKEN
            if accumulated + msg_tokens > soft_ceiling and (n - i) >= min_tail:
                break
            accumulated += msg_tokens
            cut_idx = i

        fallback_cut = n - min_tail
        if cut_idx > fallback_cut:
            cut_idx = fallback_cut
        if cut_idx <= head_end:
            cut_idx = max(fallback_cut, head_end + 1)

        # Align to avoid splitting tool groups
        cut_idx = self._align_boundary_backward(messages, cut_idx)

        return max(cut_idx, head_end + 1)

    # ------------------------------------------------------------------
    # Main compression entry point
    # ------------------------------------------------------------------

    async def compress(
        self,
        messages: list[dict],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> list[dict]:
        """Compress conversation messages by summarizing middle turns.

        Algorithm:
          1. Prune old tool results (cheap pre-pass)
          2. Protect head messages
          3. Find tail boundary by token budget
          4. Summarize middle turns with structured LLM prompt
          5. Sanitize orphaned tool pairs
        """
        n_messages = len(messages)
        _min_for_compress = self.protect_first_n + 4
        if n_messages <= _min_for_compress:
            return messages

        display_tokens = current_tokens or self.last_prompt_tokens or estimate_messages_tokens(messages)

        # Phase 1: Prune old tool results
        messages, pruned_count = self._prune_old_tool_results(
            messages, self.tail_token_budget
        )
        if pruned_count:
            logger.info("tool_results_pruned", count=pruned_count)

        # Phase 2: Determine boundaries with alignment
        compress_start = self.protect_first_n
        compress_start = self._align_boundary_forward(messages, compress_start)

        compress_end = self._find_tail_cut(messages, compress_start)

        if compress_start >= compress_end:
            return messages

        # Exclude protected messages from summarization (TWMS recovery, etc.)
        turns_to_summarize = [
            m for m in messages[compress_start:compress_end]
            if not m.get("_protected")
        ]
        # Protected messages go to tail (preserved verbatim)
        protected_messages = [
            m for m in messages[compress_start:compress_end]
            if m.get("_protected")
        ]

        logger.info(
            "context_compression_triggered",
            tokens=display_tokens,
            threshold=self.threshold_tokens,
            summarizing_turns=len(turns_to_summarize),
            head=compress_start,
            tail=n_messages - compress_end,
        )

        # Phase 3: Generate summary
        summary = await self._generate_summary(turns_to_summarize, focus_topic=focus_topic)

        # Phase 3b: Enhance summary with Smart Code Folding (preserve file signatures)
        if summary:
            try:
                from app.core.smart_condensation import SmartCondensation
                _sc = SmartCondensation(fold_budget=30_000)
                _files_from_turns = _sc.extract_files_from_messages(turns_to_summarize)
                if _files_from_turns:
                    # Reverse to get most-recent-first
                    _files_from_turns.reverse()
                    _folded = _sc.build_folded_context(_files_from_turns)
                    if _folded.strip():
                        summary = _sc.enhance_condensed_summary(summary, _folded)
            except Exception as _sc_err:
                logger.debug("smart_condensation_skipped", error=str(_sc_err))

        # Phase 4: Assemble compressed message list
        compressed = []
        for i in range(compress_start):
            msg = messages[i].copy()
            if i == 0 and msg.get("role") == "system" and self.compression_count == 0:
                msg["content"] = (
                    (msg.get("content") or "")
                    + "\n\n[Note: Some earlier conversation turns have been compacted into a handoff summary.]"
                )
            compressed.append(msg)

        # Fallback if summary failed
        if not summary:
            n_dropped = compress_end - compress_start
            summary = (
                f"{SUMMARY_PREFIX}\n"
                f"Summary generation unavailable. {n_dropped} turns removed. "
                f"Continue based on recent messages below."
            )

        # Determine role for summary message with merge-into-tail fallback
        _merge_summary_into_tail = False
        last_head_role = messages[compress_start - 1].get("role", "user") if compress_start > 0 else "user"
        first_tail_role = messages[compress_end].get("role", "user") if compress_end < n_messages else "user"

        summary_role = "user" if last_head_role in ("assistant", "tool") else "assistant"
        if summary_role == first_tail_role:
            flipped = "assistant" if summary_role == "user" else "user"
            if flipped != last_head_role:
                summary_role = flipped
            else:
                # Both roles would create consecutive same-role messages.
                # Merge summary into first tail message instead.
                _merge_summary_into_tail = True

        if not _merge_summary_into_tail:
            compressed.append({"role": summary_role, "content": summary})

        # Re-inject protected messages that were excluded from summarization
        for pm in protected_messages:
            compressed.append(pm)

        for i in range(compress_end, n_messages):
            msg = messages[i].copy()
            if _merge_summary_into_tail and i == compress_end:
                original = msg.get("content") or ""
                msg["content"] = (
                    summary
                    + "\n\n--- END OF CONTEXT SUMMARY — "
                    "respond to the message below, not the summary above ---\n\n"
                    + original
                )
                _merge_summary_into_tail = False
            compressed.append(msg)

        self.compression_count += 1
        compressed = self._sanitize_tool_pairs(compressed)

        new_estimate = estimate_messages_tokens(compressed)
        logger.info(
            "compression_complete",
            original=n_messages,
            compressed=len(compressed),
            tokens_saved=display_tokens - new_estimate,
            compression_number=self.compression_count,
        )

        return compressed

    def on_session_reset(self) -> None:
        """Reset per-session state."""
        self.last_prompt_tokens = 0
        self.compression_count = 0
        self._previous_summary = None


# Legacy compatibility alias
auto_compact_messages = None  # Replaced by ContextCompressor.compress()
