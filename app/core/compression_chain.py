"""
Nexus - Context Compression Chain.

Manages context compression for long conversations (Hermes-agent style).
When a session's context approaches the model's token limit, the middle
turns are compressed into a summary using a cheap/fast model.

Chain links allow tracing compressed context back to its original source
across session boundaries (parent_session → child_session).

Features:
- Monitor token usage per session
- Compress when approaching threshold (50% of context limit)
- Create compression summary using cheap/fast model
- Maintain chain links for auditability
- should_compress(), compress_context(), get_full_chain()
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

def _detect_context_limit() -> int:
    """Detect context limit from configured core model (auto-detect via litellm or builtin registry)."""
    try:
        from app.core.model_context import get_model_context_limit
        return get_model_context_limit()
    except Exception:
        return 200_000  # Conservative default


@dataclass
class ChainLink:
    """A single link in the compression chain."""

    session_key: str
    compressed_at: float = field(default_factory=time.time)
    original_token_count: int = 0
    compressed_token_count: int = 0
    summary: str = ""
    messages_compressed: int = 0
    parent_link: Optional["ChainLink"] = None

    @property
    def compression_ratio(self) -> float:
        """How much the context was reduced (0-1, lower = more compression)."""
        if self.original_token_count == 0:
            return 1.0
        return self.compressed_token_count / self.original_token_count


@dataclass
class CompressionResult:
    """Result of a compression operation."""

    messages: list[dict[str, Any]]
    tokens_before: int
    tokens_after: int
    messages_before: int
    messages_after: int
    summary_generated: str
    chain_link: ChainLink


class CompressionChain:
    """
    Context compression manager for a single session.

    Monitors token usage and compresses context when approaching limits.
    Maintains a chain of compression summaries for auditability.

    Usage:
        chain = CompressionChain(session_key="user:conv")

        # In the react loop, before each LLM call:
        if chain.should_compress(messages):
            result = await chain.compress_context(messages, llm_client)
            messages = result.messages

        # To get full history of compressions:
        links = chain.get_full_chain()
    """

    def __init__(
        self,
        session_key: str,
        context_limit: int = 0,
        threshold_percent: float = 0.50,
    ) -> None:
        """
        Initialize compression chain.

        Args:
            session_key: Session identifier.
            context_limit: Token limit for the model. 0 = auto-detect.
            threshold_percent: Compress when usage exceeds this % of limit.
        """
        self._session_key = session_key
        self._context_limit = context_limit if context_limit > 0 else _detect_context_limit()
        self._threshold_percent = threshold_percent
        self._threshold_tokens = int(self._context_limit * threshold_percent)
        self._chain_links: list[ChainLink] = []
        self._compression_count = 0
        self._lock = asyncio.Lock()

    @property
    def session_key(self) -> str:
        return self._session_key

    @property
    def context_limit(self) -> int:
        return self._context_limit

    @property
    def threshold_tokens(self) -> int:
        return self._threshold_tokens

    @property
    def compression_count(self) -> int:
        return self._compression_count

    @property
    def chain_links(self) -> list[ChainLink]:
        """Get all chain links (immutable view)."""
        return list(self._chain_links)

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        """
        Check if context should be compressed.

        Uses character-based token estimation (chars / 4).
        Returns True if estimated tokens exceed threshold.
        """
        estimated_tokens = _estimate_tokens(messages)
        return estimated_tokens > self._threshold_tokens

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token count for messages."""
        return _estimate_tokens(messages)

    async def compress_context(
        self,
        messages: list[dict[str, Any]],
        llm_client: Any,
        *,
        preserve_head: int = 2,
        preserve_tail: int = 4,
    ) -> CompressionResult:
        """
        Compress middle turns of conversation context.

        Strategy:
        1. Keep first N messages (system prompt + initial context) = head
        2. Keep last M messages (recent context) = tail
        3. Compress everything in between into a structured summary
        4. Insert summary as a "system" message between head and tail

        Args:
            messages: Current message list.
            llm_client: LLM client for generating summary (uses chat method).
            preserve_head: Number of messages to keep at the start.
            preserve_tail: Number of messages to keep at the end.

        Returns:
            CompressionResult with new messages and metadata.
        """
        async with self._lock:
            tokens_before = _estimate_tokens(messages)
            messages_before = len(messages)

            # If not enough messages to compress, return as-is
            if len(messages) <= preserve_head + preserve_tail + 2:
                return CompressionResult(
                    messages=messages,
                    tokens_before=tokens_before,
                    tokens_after=tokens_before,
                    messages_before=messages_before,
                    messages_after=messages_before,
                    summary_generated="",
                    chain_link=ChainLink(session_key=self._session_key),
                )

            head = messages[:preserve_head]
            tail = messages[-preserve_tail:]
            middle = messages[preserve_head:-preserve_tail]

            # Generate compression summary
            summary = await self._generate_summary(middle, llm_client)

            # Build compressed message list
            summary_message = {
                "role": "user",
                "content": (
                    f"[CONTEXT COMPRESSION #{self._compression_count + 1}]\n"
                    f"The following is a summary of {len(middle)} previous messages "
                    f"that have been compressed to save context space:\n\n"
                    f"{summary}\n\n"
                    "[END COMPRESSION SUMMARY — Recent messages follow below]"
                ),
            }

            compressed_messages = head + [summary_message] + tail
            tokens_after = _estimate_tokens(compressed_messages)

            # Create chain link
            parent_link = self._chain_links[-1] if self._chain_links else None
            chain_link = ChainLink(
                session_key=self._session_key,
                original_token_count=tokens_before,
                compressed_token_count=tokens_after,
                summary=summary,
                messages_compressed=len(middle),
                parent_link=parent_link,
            )
            self._chain_links.append(chain_link)
            self._compression_count += 1

            logger.info(
                "compression_chain_compressed",
                session_key=self._session_key,
                compression_number=self._compression_count,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                messages_compressed=len(middle),
                ratio=round(chain_link.compression_ratio, 3),
            )

            return CompressionResult(
                messages=compressed_messages,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                messages_before=messages_before,
                messages_after=len(compressed_messages),
                summary_generated=summary,
                chain_link=chain_link,
            )

    async def _generate_summary(
        self,
        messages: list[dict[str, Any]],
        llm_client: Any,
    ) -> str:
        """
        Generate a structured summary of messages using a cheap/fast model.

        The summary preserves:
        - Key decisions made
        - Tool call results and data gathered
        - Important context/facts discovered
        - Current progress state
        """
        # Build content for summarization
        content_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])

            if role == "assistant" and tool_calls:
                tool_names = [
                    tc.get("function", {}).get("name", "unknown")
                    for tc in tool_calls
                    if isinstance(tc, dict)
                ]
                content_parts.append(
                    f"[Assistant called tools: {', '.join(tool_names)}]"
                )
                if content:
                    content_parts.append(f"[Assistant reasoning: {content[:500]}]")
            elif role == "tool":
                tool_output = content[:1000] if content else "(empty)"
                content_parts.append(f"[Tool result: {tool_output}]")
            elif role == "user":
                content_parts.append(f"[User/System: {content[:500]}]")
            elif role == "assistant":
                content_parts.append(f"[Assistant: {content[:500]}]")

        conversation_text = "\n".join(content_parts)

        # Truncate if too long for summarization
        if len(conversation_text) > 30000:
            conversation_text = conversation_text[:30000] + "\n... (truncated)"

        summarize_messages = [
            {
                "role": "system",
                "content": (
                    "You are a context compression assistant. Summarize the following "
                    "conversation segment into a structured, information-dense summary. "
                    "Preserve ALL important information:\n"
                    "1. Key decisions and conclusions\n"
                    "2. Data/facts gathered from tool calls\n"
                    "3. Current progress and remaining tasks\n"
                    "4. Any errors encountered and how they were resolved\n"
                    "5. Important context needed for future actions\n\n"
                    "Be concise but complete. Use bullet points. "
                    "Do NOT lose any critical information."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Summarize this conversation segment ({len(messages)} messages):\n\n"
                    f"{conversation_text}"
                ),
            },
        ]

        try:
            result = await asyncio.wait_for(
                llm_client.chat(
                    messages=summarize_messages,
                    temperature=0.2,
                    max_tokens=4096,
                ),
                timeout=30.0,
            )
            return result.get("content", "") or "(compression failed — no summary generated)"
        except asyncio.TimeoutError:
            logger.warning(
                "compression_summary_timeout",
                session_key=self._session_key,
                messages_count=len(messages),
            )
            return self._fallback_summary(messages)
        except Exception as e:
            logger.warning(
                "compression_summary_failed",
                session_key=self._session_key,
                error=str(e),
            )
            return self._fallback_summary(messages)

    @staticmethod
    def _fallback_summary(messages: list[dict[str, Any]]) -> str:
        """Generate a basic extractive summary without LLM (fallback)."""
        parts: list[str] = []
        parts.append(f"[Compressed {len(messages)} messages — extractive summary]")

        tool_calls_seen: list[str] = []
        key_content: list[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])

            if role == "assistant" and tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        name = tc.get("function", {}).get("name", "?")
                        tool_calls_seen.append(name)

            if role == "tool" and content and len(content) > 50:
                key_content.append(content[:200])

        if tool_calls_seen:
            parts.append(f"Tools used: {', '.join(set(tool_calls_seen))}")
        if key_content:
            parts.append("Key findings:")
            for kc in key_content[:5]:
                parts.append(f"  - {kc}")

        return "\n".join(parts)

    def get_full_chain(self) -> list[dict[str, Any]]:
        """
        Get the full compression chain as a list of dicts.

        Useful for auditing and debugging compression history.
        """
        return [
            {
                "session_key": link.session_key,
                "compressed_at": link.compressed_at,
                "original_tokens": link.original_token_count,
                "compressed_tokens": link.compressed_token_count,
                "messages_compressed": link.messages_compressed,
                "compression_ratio": round(link.compression_ratio, 3),
                "summary_preview": link.summary[:200] if link.summary else "",
            }
            for link in self._chain_links
        ]


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count using chars/4 heuristic."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(block.get("text", ""))
        # Count tool_calls content too
        for tc in msg.get("tool_calls", []):
            if isinstance(tc, dict):
                args = tc.get("function", {}).get("arguments", "")
                if isinstance(args, str):
                    total_chars += len(args)
    return total_chars // 4


# ── Module-level registry of compression chains ──

_active_chains: dict[str, CompressionChain] = {}
_chains_lock = asyncio.Lock()


async def get_or_create_chain(
    session_key: str,
    context_limit: int = 0,
    threshold_percent: float = 0.50,
) -> CompressionChain:
    """Get or create a compression chain for a session."""
    async with _chains_lock:
        if session_key not in _active_chains:
            _active_chains[session_key] = CompressionChain(
                session_key=session_key,
                context_limit=context_limit,
                threshold_percent=threshold_percent,
            )
        return _active_chains[session_key]


async def remove_chain(session_key: str) -> Optional[CompressionChain]:
    """Remove a compression chain. Returns the removed chain or None."""
    async with _chains_lock:
        return _active_chains.pop(session_key, None)
