"""
Nexus - Context Engine (OpenClaw-inspired).

Token-budget-aware context assembly and compaction.

Inspired by:
- OpenClaw src/context-engine/ — token budget management
- OpenClaw context-engine compaction strategies
- OpenClaw AGENTS.md — context assembly rules

Features:
- Token budget allocation across context components
- Priority-based message inclusion
- Compaction strategies (summarize, sliding window, prune tools, hybrid)
- System prompt + active memory + conversation history + tool results
- Automatic compaction when budget exceeded
- Token counting (approximate or tiktoken-based)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Approximate tokens per character for various languages
TOKENS_PER_CHAR_ESTIMATES: dict[str, float] = {
    "en": 0.25,    # English: ~4 chars/token
    "vi": 0.5,     # Vietnamese: ~2 chars/token (diacritics)
    "zh": 0.7,     # Chinese: ~1.5 chars/token
    "default": 0.3,
}


class CompactionStrategy(str, Enum):
    """Strategies for compacting context when over budget."""
    SUMMARIZE = "summarize"         # LLM summarize oldest messages
    SLIDING_WINDOW = "sliding"      # Keep N most recent messages
    PRUNE_TOOLS = "prune_tools"     # Shorten tool outputs first
    HYBRID = "hybrid"               # Prune tools → slide → summarize
    TRUNCATE = "truncate"           # Hard truncate (fastest, dumbest)


class MessageRole(str, Enum):
    """Message roles in conversation."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class TokenBudget:
    """Token budget allocation for context assembly.

    Allocates total tokens across components with priorities.
    """
    total: int = 128_000          # Model max context
    system_prompt: int = 2_000     # Fixed system prompt
    active_memory: int = 4_000     # Active memory injection
    conversation: int = 0          # Calculated: total - others
    tool_results: int = 8_000      # Tool call results
    response_reserve: int = 4_000  # Reserved for model response

    def __post_init__(self):
        # Calculate conversation budget as remainder
        fixed = (self.system_prompt + self.active_memory +
                 self.tool_results + self.response_reserve)
        self.conversation = self.total - fixed

    @property
    def available_for_messages(self) -> int:
        """Tokens available for conversation messages."""
        return self.conversation

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "system_prompt": self.system_prompt,
            "active_memory": self.active_memory,
            "conversation": self.conversation,
            "tool_results": self.tool_results,
            "response_reserve": self.response_reserve,
        }


@dataclass
class ContextMessage:
    """A message with token count metadata."""
    role: str
    content: str
    token_count: int = 0
    priority: float = 1.0     # Higher = more important to keep
    is_pinned: bool = False   # Never remove pinned messages
    is_compacted: bool = False  # Already been through compaction
    metadata: dict[str, Any] = field(default_factory=dict)

    def estimate_tokens(self, chars_per_token: float = 0.3) -> int:
        """Estimate token count from content length."""
        if self.token_count > 0:
            return self.token_count
        # +4 for message framing overhead
        self.token_count = int(len(self.content) * chars_per_token) + 4
        return self.token_count


@dataclass
class ContextAssemblyResult:
    """Result of context assembly."""
    messages: list[dict[str, str]]    # Final messages list
    total_tokens: int = 0
    budget: Optional[TokenBudget] = None
    messages_included: int = 0
    messages_dropped: int = 0
    compacted: bool = False
    compaction_strategy: Optional[str] = None
    assembly_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "messages_included": self.messages_included,
            "messages_dropped": self.messages_dropped,
            "compacted": self.compacted,
            "compaction_strategy": self.compaction_strategy,
            "assembly_time_ms": round(self.assembly_time_ms, 1),
            "budget": self.budget.to_dict() if self.budget else None,
        }


@dataclass
class CompactedContext:
    """Result of context compaction."""
    messages: list[dict[str, str]]
    summary: str = ""
    original_count: int = 0
    compacted_count: int = 0
    tokens_saved: int = 0
    strategy_used: str = ""


class ContextEngine:
    """
    Token-budget-aware context assembly engine.

    Assembles the final message list for LLM calls, ensuring
    all components fit within the model's context window.

    Components (in order):
    1. System prompt (fixed, always included)
    2. Active memory injection (from MEMORY.md)
    3. Conversation history (sliding window, oldest compacted first)
    4. Tool results (truncated if oversized)
    5. Latest user message (always included)

    Usage:
        engine = ContextEngine(model="claude-3", max_tokens=200_000)
        result = await engine.assemble(
            session_id="abc123",
            system_prompt="You are helpful...",
            messages=[...],
            active_memory="<memory>...</memory>",
            tool_results=[...],
        )
        # result.messages is ready for LLM
    """

    def __init__(
        self,
        model: str = "default",
        max_tokens: int = 128_000,
        budget: Optional[TokenBudget] = None,
        language: str = "default",
        llm_client: Any = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._budget = budget or TokenBudget(total=max_tokens)
        self._chars_per_token = TOKENS_PER_CHAR_ESTIMATES.get(
            language, TOKENS_PER_CHAR_ESTIMATES["default"]
        )
        self._llm = llm_client
        self._compaction_count = 0

    @property
    def budget(self) -> TokenBudget:
        return self._budget

    def count_tokens(self, text: str) -> int:
        """Count tokens in text (approximate or tiktoken).

        Uses tiktoken if available, otherwise falls back to
        character-based estimation.
        """
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self._model)
            return len(enc.encode(text))
        except (ImportError, KeyError):
            return int(len(text) * self._chars_per_token)

    def count_message_tokens(self, messages: list[dict[str, str]]) -> int:
        """Count total tokens in a message list."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.count_tokens(content) + 4  # +4 for framing
        return total

    async def assemble(
        self,
        session_id: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        active_memory: str = "",
        tool_results: Optional[list[dict[str, str]]] = None,
        response_reserve: int = 4096,
    ) -> ContextAssemblyResult:
        """Assemble context under token budget.

        Args:
            session_id: Session identifier.
            system_prompt: Base system prompt.
            messages: Conversation history (role + content dicts).
            active_memory: Active memory text to inject.
            tool_results: Recent tool results to include.
            response_reserve: Tokens to reserve for response.

        Returns:
            ContextAssemblyResult with final messages list.
        """
        t0 = time.perf_counter()
        tool_results = tool_results or []

        # Build system message with active memory
        system_content = system_prompt
        if active_memory:
            system_content += f"\n\n{active_memory}"

        system_tokens = self.count_tokens(system_content)

        # Count tool result tokens
        tool_tokens = 0
        truncated_tools: list[dict[str, str]] = []
        for tool_msg in tool_results:
            content = tool_msg.get("content", "")
            t_tokens = self.count_tokens(content)
            if tool_tokens + t_tokens > self._budget.tool_results:
                # Truncate this tool result
                remaining_chars = int(
                    (self._budget.tool_results - tool_tokens) / self._chars_per_token
                )
                if remaining_chars > 100:
                    truncated_content = content[:remaining_chars] + "\n[... output truncated]"
                    truncated_tools.append({**tool_msg, "content": truncated_content})
                    tool_tokens += self.count_tokens(truncated_content)
                break
            else:
                truncated_tools.append(tool_msg)
                tool_tokens += t_tokens

        # Calculate remaining budget for conversation
        used = system_tokens + tool_tokens + response_reserve
        conversation_budget = self._max_tokens - used

        # Select messages that fit within budget
        selected_messages, dropped = self._select_messages(
            messages, conversation_budget,
        )

        # If we had to drop messages, try compaction
        compacted = False
        strategy_used = None
        if dropped > 0 and self._llm:
            try:
                compact_result = await self.compact(
                    messages, conversation_budget,
                    strategy=CompactionStrategy.HYBRID,
                )
                selected_messages = [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in compact_result.messages
                ]
                dropped = compact_result.original_count - compact_result.compacted_count
                compacted = True
                strategy_used = compact_result.strategy_used
            except Exception as exc:
                logger.warning("context_compaction_failed", error=str(exc))

        # Build final message list
        final_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]
        final_messages.extend(selected_messages)
        final_messages.extend(truncated_tools)

        total_tokens = self.count_message_tokens(final_messages)
        elapsed = (time.perf_counter() - t0) * 1000

        result = ContextAssemblyResult(
            messages=final_messages,
            total_tokens=total_tokens,
            budget=self._budget,
            messages_included=len(selected_messages),
            messages_dropped=dropped,
            compacted=compacted,
            compaction_strategy=strategy_used,
            assembly_time_ms=elapsed,
        )

        logger.debug(
            "context_assembled",
            session=session_id[:8],
            total_tokens=total_tokens,
            messages=len(final_messages),
            dropped=dropped,
            compacted=compacted,
            time_ms=round(elapsed, 1),
        )

        return result

    def _select_messages(
        self,
        messages: list[dict[str, str]],
        token_budget: int,
    ) -> tuple[list[dict[str, str]], int]:
        """Select messages that fit within budget (recent first).

        Strategy: Always keep first message (may be important context)
        and last N messages that fit.

        Returns:
            (selected_messages, dropped_count)
        """
        if not messages:
            return [], 0

        # Calculate token costs
        msg_tokens: list[int] = []
        for msg in messages:
            tokens = self.count_tokens(msg.get("content", "")) + 4
            msg_tokens.append(tokens)

        total_needed = sum(msg_tokens)
        if total_needed <= token_budget:
            return list(messages), 0

        # Need to drop messages — keep most recent
        # Always try to keep the first user message for context
        selected: list[dict[str, str]] = []
        selected_tokens = 0
        dropped = 0

        # Process from most recent to oldest
        for i in range(len(messages) - 1, -1, -1):
            if selected_tokens + msg_tokens[i] <= token_budget:
                selected.insert(0, messages[i])
                selected_tokens += msg_tokens[i]
            else:
                dropped += 1

        return selected, dropped

    async def compact(
        self,
        messages: list[dict[str, str]],
        target_tokens: int,
        strategy: CompactionStrategy = CompactionStrategy.HYBRID,
        force: bool = False,
    ) -> CompactedContext:
        """Compact context when over budget.

        Args:
            messages: Messages to compact.
            target_tokens: Target token count to fit within.
            strategy: Compaction strategy to use.
            force: Force compaction even if under budget.

        Returns:
            CompactedContext with compacted messages.
        """
        current_tokens = self.count_message_tokens(messages)
        if current_tokens <= target_tokens and not force:
            return CompactedContext(
                messages=messages,
                original_count=len(messages),
                compacted_count=len(messages),
                strategy_used="none",
            )

        self._compaction_count += 1

        if strategy == CompactionStrategy.PRUNE_TOOLS:
            return self._compact_prune_tools(messages, target_tokens)
        elif strategy == CompactionStrategy.SLIDING_WINDOW:
            return self._compact_sliding_window(messages, target_tokens)
        elif strategy == CompactionStrategy.SUMMARIZE:
            return await self._compact_summarize(messages, target_tokens)
        elif strategy == CompactionStrategy.HYBRID:
            return await self._compact_hybrid(messages, target_tokens)
        else:  # TRUNCATE
            return self._compact_truncate(messages, target_tokens)

    def _compact_prune_tools(
        self,
        messages: list[dict[str, str]],
        target_tokens: int,
    ) -> CompactedContext:
        """Prune tool outputs: keep first 200 chars of each tool message."""
        result: list[dict[str, str]] = []
        tokens_saved = 0

        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if len(content) > 300:
                    old_tokens = self.count_tokens(content)
                    pruned = content[:200] + "\n[... pruned for context budget]"
                    new_tokens = self.count_tokens(pruned)
                    tokens_saved += old_tokens - new_tokens
                    result.append({**msg, "content": pruned})
                else:
                    result.append(msg)
            else:
                result.append(msg)

        return CompactedContext(
            messages=result,
            original_count=len(messages),
            compacted_count=len(result),
            tokens_saved=tokens_saved,
            strategy_used="prune_tools",
        )

    def _compact_sliding_window(
        self,
        messages: list[dict[str, str]],
        target_tokens: int,
    ) -> CompactedContext:
        """Keep only recent messages within budget."""
        selected, dropped = self._select_messages(messages, target_tokens)
        original_tokens = self.count_message_tokens(messages)
        new_tokens = self.count_message_tokens(selected)

        return CompactedContext(
            messages=selected,
            original_count=len(messages),
            compacted_count=len(selected),
            tokens_saved=original_tokens - new_tokens,
            strategy_used="sliding_window",
        )

    async def _compact_summarize(
        self,
        messages: list[dict[str, str]],
        target_tokens: int,
    ) -> CompactedContext:
        """LLM-summarize oldest messages."""
        if not self._llm:
            # Fallback to sliding window if no LLM
            return self._compact_sliding_window(messages, target_tokens)

        # Split: summarize oldest half, keep recent half
        split_point = len(messages) // 2
        to_summarize = messages[:split_point]
        to_keep = messages[split_point:]

        # Generate summary
        summary_prompt = [
            {"role": "system", "content": (
                "Summarize the following conversation concisely, "
                "preserving key decisions, facts, and context. "
                "Keep it under 500 words."
            )},
            {"role": "user", "content": "\n".join(
                f"[{m.get('role', '?')}]: {m.get('content', '')[:500]}"
                for m in to_summarize
            )},
        ]

        try:
            resp = await self._llm.chat(summary_prompt, max_tokens=1000, temperature=0.3)
            summary_text = resp.get("content", "")
        except Exception:
            summary_text = f"[Context summary: {len(to_summarize)} earlier messages]"

        # Build compacted messages
        summary_msg = {
            "role": "system",
            "content": f"<earlier_context_summary>\n{summary_text}\n</earlier_context_summary>",
        }
        result = [summary_msg] + to_keep

        original_tokens = self.count_message_tokens(messages)
        new_tokens = self.count_message_tokens(result)

        return CompactedContext(
            messages=result,
            summary=summary_text,
            original_count=len(messages),
            compacted_count=len(result),
            tokens_saved=original_tokens - new_tokens,
            strategy_used="summarize",
        )

    async def _compact_hybrid(
        self,
        messages: list[dict[str, str]],
        target_tokens: int,
    ) -> CompactedContext:
        """Hybrid: prune tools → check budget → slide → summarize."""
        # Step 1: Prune tool outputs
        pruned = self._compact_prune_tools(messages, target_tokens)
        if self.count_message_tokens(pruned.messages) <= target_tokens:
            pruned.strategy_used = "hybrid:prune_tools"
            return pruned

        # Step 2: Sliding window
        slid = self._compact_sliding_window(pruned.messages, target_tokens)
        if self.count_message_tokens(slid.messages) <= target_tokens:
            slid.strategy_used = "hybrid:sliding_window"
            slid.tokens_saved += pruned.tokens_saved
            return slid

        # Step 3: Summarize if LLM available
        if self._llm:
            summarized = await self._compact_summarize(slid.messages, target_tokens)
            summarized.strategy_used = "hybrid:summarize"
            summarized.tokens_saved += pruned.tokens_saved + slid.tokens_saved
            return summarized

        # Final fallback: truncate
        truncated = self._compact_truncate(slid.messages, target_tokens)
        truncated.strategy_used = "hybrid:truncate"
        truncated.tokens_saved += pruned.tokens_saved + slid.tokens_saved
        return truncated

    def _compact_truncate(
        self,
        messages: list[dict[str, str]],
        target_tokens: int,
    ) -> CompactedContext:
        """Hard truncate messages from oldest until within budget."""
        result = list(messages)
        while result and self.count_message_tokens(result) > target_tokens:
            result.pop(0)

        original_tokens = self.count_message_tokens(messages)
        new_tokens = self.count_message_tokens(result)

        return CompactedContext(
            messages=result,
            original_count=len(messages),
            compacted_count=len(result),
            tokens_saved=original_tokens - new_tokens,
            strategy_used="truncate",
        )

    # ── Stats ────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "compaction_count": self._compaction_count,
            "budget": self._budget.to_dict(),
        }


# ─────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────

_engines: dict[str, ContextEngine] = {}


def get_context_engine(
    model: str = "default",
    max_tokens: int = 128_000,
) -> ContextEngine:
    """Get or create a ContextEngine for a model."""
    key = f"{model}:{max_tokens}"
    if key not in _engines:
        _engines[key] = ContextEngine(model=model, max_tokens=max_tokens)
    return _engines[key]
