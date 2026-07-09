"""
MekongAI Nexus - Prompt Cache Stability + Anthropic Cache Injection.

Ensure deterministic system prompt assembly for provider cache hits.
Inject Anthropic cache_control breakpoints for ~75% input cost reduction.

Inspired by:
- OpenClaw AGENTS.md — "Prompt Cache Stability" section
- hermes-agent/agent/prompt_caching.py — Anthropic cache injection
"""

import copy
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class PromptComponent:
    """A named section of the system prompt with stable ordering key."""
    key: str            # Unique key for ordering, e.g. "10-identity", "20-tools"
    content: str
    cacheable: bool = True  # False for dynamic per-turn content


@dataclass
class CacheObservation:
    """Tracks prompt cache hit/miss patterns."""
    cache_read: int = 0
    cache_write: int = 0
    total_input: int = 0
    broke: bool = False
    changes: list[str] = field(default_factory=list)


class PromptCacheOptimizer:
    """
    Ensure deterministic system prompt assembly for cache stability.

    Key rules from OpenClaw:
    1. Ordering from maps/sets/registries must be deterministic
    2. Don't rewrite older transcript bytes on every turn
    3. If truncation needed, prefer mutating tail content first
    4. Keep static prefix stable and byte-identical across turns
    """

    def __init__(self) -> None:
        self._last_prefix_hash: str = ""
        self._last_observation: Optional[CacheObservation] = None
        self._cache_touch_at: float = 0.0

    def assemble_system_prompt(self, components: list[PromptComponent]) -> str:
        """
        Assemble system prompt from components in deterministic order.

        Static/cacheable components go first (prefix), dynamic at the end (tail).
        """
        # Separate cacheable (prefix) from dynamic (tail)
        cacheable = sorted(
            [c for c in components if c.cacheable],
            key=lambda c: c.key,
        )
        dynamic = sorted(
            [c for c in components if not c.cacheable],
            key=lambda c: c.key,
        )

        # Build prompt: cacheable first, then dynamic
        parts = [c.content for c in cacheable if c.content.strip()]
        parts.extend(c.content for c in dynamic if c.content.strip())
        assembled = "\n\n".join(parts)

        # Track prefix hash for cache stability observation
        prefix_text = "\n\n".join(c.content for c in cacheable if c.content.strip())
        current_hash = hashlib.sha256(prefix_text.encode()).hexdigest()[:16]
        if self._last_prefix_hash and current_hash != self._last_prefix_hash:
            logger.debug("prompt_cache_prefix_changed",
                         old=self._last_prefix_hash, new=current_hash)
        self._last_prefix_hash = current_hash

        return assembled

    def sort_tool_definitions(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Deterministic ordering of tool definitions for cache stability.

        Tools sorted by name ensures byte-identical prefix across turns.
        """
        return sorted(tools, key=lambda t: t.get("function", {}).get("name", ""))

    def observe_cache_usage(self, usage: dict[str, Any]) -> CacheObservation:
        """
        Observe cache hit/miss from API response usage data.

        Anthropic: cache_creation_input_tokens, cache_read_input_tokens
        OpenAI: cached_tokens in prompt_tokens_details
        """
        obs = CacheObservation()

        # Anthropic format
        obs.cache_read = usage.get("cache_read_input_tokens", 0)
        obs.cache_write = usage.get("cache_creation_input_tokens", 0)
        obs.total_input = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)

        # OpenAI format fallback
        if not obs.cache_read:
            details = usage.get("prompt_tokens_details", {})
            obs.cache_read = details.get("cached_tokens", 0)

        # Check if cache broke
        if self._last_observation and self._last_observation.cache_read > 0:
            if obs.cache_read == 0 and obs.total_input > 1000:
                obs.broke = True
                obs.changes.append("cache_dropped")

        if obs.cache_read > 0:
            self._cache_touch_at = time.time()

        self._last_observation = obs
        return obs

    @property
    def last_cache_touch_age(self) -> float:
        """Seconds since last cache hit."""
        if self._cache_touch_at == 0:
            return float("inf")
        return time.time() - self._cache_touch_at


# ---------------------------------------------------------------------------
# Anthropic Prompt Cache Control Injection
# Ported from: hermes-agent/agent/prompt_caching.py
# ---------------------------------------------------------------------------

def _apply_cache_marker(msg: dict, cache_marker: dict) -> None:
    """Add cache_control to a single message, handling all format variations."""
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        # Tool messages: add at message level
        msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_marker


def apply_anthropic_cache_control(
    api_messages: list[dict[str, Any]],
    cache_ttl: str = "5m",
) -> list[dict[str, Any]]:
    """Apply system_and_3 caching strategy to messages for Anthropic models.

    Places up to 4 cache_control breakpoints:
    - System prompt (stable across all turns)
    - Last 3 non-system messages (rolling window)

    This reduces input token costs by ~75% on multi-turn conversations.

    Returns deep copy of messages with cache_control breakpoints injected.
    """
    messages = copy.deepcopy(api_messages)
    if not messages:
        return messages

    marker: dict[str, str] = {"type": "ephemeral"}
    if cache_ttl == "1h":
        marker["ttl"] = "1h"

    breakpoints_used = 0

    # Cache system prompt (most stable content)
    if messages[0].get("role") == "system":
        _apply_cache_marker(messages[0], marker)
        breakpoints_used += 1

    # Cache last N non-system messages (rolling window)
    remaining = 4 - breakpoints_used
    non_sys = [i for i in range(len(messages)) if messages[i].get("role") != "system"]
    for idx in non_sys[-remaining:]:
        _apply_cache_marker(messages[idx], marker)

    return messages


def is_anthropic_model(model: str, provider: str) -> bool:
    """Check if a model is Anthropic-based and supports prompt caching."""
    model_lower = (model or "").lower()
    provider_lower = (provider or "").lower()

    if provider_lower in ("bedrock", "anthropic"):
        return "claude" in model_lower or "anthropic" in model_lower

    # OpenRouter: check model slug
    if provider_lower == "openrouter":
        return "anthropic/" in model_lower or "claude" in model_lower

    return False
