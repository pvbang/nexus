"""
MekongAI Nexus - Smart Wait Strategy.

When a preferred provider (like Kilo) hits rate limit, intelligently decide:
  - WAIT for preferred provider to reset (for hard tasks)
  - FALLBACK immediately to weaker free providers (for easy tasks)

Key insight: for long-running tasks (days), waiting 20 minutes for a good model
is far better than getting garbage from a weak fallback on complex reasoning.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class TaskDifficulty(Enum):
    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class WaitDecision(Enum):
    WAIT = "wait"
    FALLBACK = "fallback"


@dataclass
class WaitVerdict:
    decision: WaitDecision
    wait_seconds: float = 0.0
    reason: str = ""
    task_difficulty: TaskDifficulty = TaskDifficulty.MEDIUM


# Structural complexity signals (language-agnostic, no semantic analysis needed)
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_URL_RE = re.compile(r"https?://\S+")
_JSON_LIKE_RE = re.compile(r"\{[^}]{20,}\}")
_MULTI_STEP_RE = re.compile(r"(?:^|\n)\s*(?:\d+[\.\)]\s|[-*]\s)", re.MULTILINE)


def estimate_task_difficulty(
    messages: list[dict],
    *,
    tools: Optional[list] = None,
) -> TaskDifficulty:
    """Estimate task difficulty from conversation structure.

    Uses ONLY structural heuristics (no LLM call needed):
    - Message count & total length (conversation depth)
    - Code blocks, JSON structures (technical complexity)
    - Tool usage (agentic multi-step work)
    - Multi-step indicators (numbered lists, bullets)
    - System prompt complexity
    """
    if not messages:
        return TaskDifficulty.TRIVIAL

    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    msg_count = len(messages)
    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "") or ""
            break

    all_content = " ".join(m.get("content", "") or "" for m in messages)

    # Scoring system: accumulate complexity points
    score = 0

    # Conversation depth signals
    if msg_count > 20:
        score += 3
    elif msg_count > 8:
        score += 2
    elif msg_count > 3:
        score += 1

    # Total content volume
    if total_chars > 20000:
        score += 3
    elif total_chars > 8000:
        score += 2
    elif total_chars > 2000:
        score += 1

    # Code complexity
    code_blocks = len(_CODE_BLOCK_RE.findall(all_content))
    if code_blocks > 5:
        score += 3
    elif code_blocks > 2:
        score += 2
    elif code_blocks > 0:
        score += 1

    # Tool usage = agentic work = complex
    has_tool_messages = any(
        m.get("role") == "tool" or m.get("tool_calls")
        for m in messages
    )
    if has_tool_messages:
        score += 2
    if tools and len(tools) > 3:
        score += 1

    # Multi-step indicators in the last user message
    steps = len(_MULTI_STEP_RE.findall(last_user_msg))
    if steps > 3:
        score += 2
    elif steps > 0:
        score += 1

    # JSON/structured data = likely technical
    if _JSON_LIKE_RE.search(last_user_msg):
        score += 1

    # Last user message length
    if len(last_user_msg) > 1000:
        score += 2
    elif len(last_user_msg) > 300:
        score += 1

    # Map score to difficulty
    if score <= 1:
        return TaskDifficulty.TRIVIAL
    elif score <= 4:
        return TaskDifficulty.EASY
    elif score <= 8:
        return TaskDifficulty.MEDIUM
    else:
        return TaskDifficulty.HARD


def decide_wait_or_fallback(
    difficulty: TaskDifficulty,
    estimated_reset_seconds: float,
    *,
    max_wait_seconds: float = 1200.0,
    provider_key: str = "",
) -> WaitVerdict:
    """Decide whether to wait for preferred provider or fallback immediately.

    Strategy per difficulty:
      TRIVIAL: always fallback (weak model is fine)
      EASY:    fallback if wait > 60s, else wait
      MEDIUM:  fallback if wait > 5min, else wait
      HARD:    wait up to max_wait (20min default), only fallback if longer

    The idea: complex reasoning tasks deserve a good model. A 10-minute wait
    for Kilo to reset is nothing compared to hours of wrong output from g4f.
    """
    # Difficulty -> max tolerable wait
    thresholds = {
        TaskDifficulty.TRIVIAL: 0.0,
        TaskDifficulty.EASY: 60.0,
        TaskDifficulty.MEDIUM: 300.0,
        TaskDifficulty.HARD: max_wait_seconds,
    }

    max_tolerable = thresholds[difficulty]
    effective_wait = min(estimated_reset_seconds, max_wait_seconds)

    if difficulty == TaskDifficulty.TRIVIAL:
        return WaitVerdict(
            decision=WaitDecision.FALLBACK,
            wait_seconds=0.0,
            reason=f"trivial task, fallback immediately",
            task_difficulty=difficulty,
        )

    if estimated_reset_seconds <= max_tolerable:
        return WaitVerdict(
            decision=WaitDecision.WAIT,
            wait_seconds=effective_wait,
            reason=(
                f"{difficulty.value} task, waiting {effective_wait:.0f}s "
                f"for {provider_key or 'preferred provider'} to reset"
            ),
            task_difficulty=difficulty,
        )

    return WaitVerdict(
        decision=WaitDecision.FALLBACK,
        wait_seconds=0.0,
        reason=(
            f"{difficulty.value} task but wait too long "
            f"({estimated_reset_seconds:.0f}s > {max_tolerable:.0f}s threshold), fallback"
        ),
        task_difficulty=difficulty,
    )


def estimate_reset_seconds(
    *,
    retry_after_header: Optional[float] = None,
    rate_limit_remaining: Optional[int] = None,
    rate_limit_reset_seconds: Optional[float] = None,
    last_rate_limit_at: float = 0.0,
    provider: str = "",
) -> float:
    """Estimate seconds until rate limit resets.

    Priority:
      1. Retry-After header (exact, from API response)
      2. x-ratelimit-reset-* header (parsed by rate_limit_tracker)
      3. Known provider defaults (Kilo = 1h window, so ~30min avg)
      4. Conservative fallback: 600s (10 minutes)
    """
    if retry_after_header is not None and retry_after_header > 0:
        return retry_after_header

    if rate_limit_reset_seconds is not None and rate_limit_reset_seconds > 0:
        return rate_limit_reset_seconds

    # Provider-specific heuristics
    if provider == "kilo":
        # Kilo: 200 req/hr with rolling window. If we just hit limit,
        # oldest request falls off in ~30 minutes on average.
        if last_rate_limit_at > 0:
            elapsed = time.time() - last_rate_limit_at
            # If we hit it recently, estimate ~18 minutes (1080s) for some slots to free up
            if elapsed < 60:
                return 1080.0
            # If some time passed, less wait needed
            return max(60.0, 1080.0 - elapsed)
        return 1080.0

    if provider == "openrouter":
        return 60.0

    return 600.0


async def smart_wait(
    wait_seconds: float,
    *,
    provider_key: str = "",
    check_interval: float = 30.0,
    is_available_fn=None,
) -> bool:
    """Wait for provider to become available, with periodic health checks.

    Instead of sleeping the full duration blindly, checks every `check_interval`
    seconds whether the provider recovered early (via circuit breaker / cooldown).

    Returns True if wait completed (provider should be available).
    Returns False if cancelled or interrupted.
    """
    if wait_seconds <= 0:
        return True

    logger.info(
        "smart_wait_start",
        provider=provider_key,
        wait_seconds=round(wait_seconds, 1),
    )

    elapsed = 0.0
    while elapsed < wait_seconds:
        chunk = min(check_interval, wait_seconds - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk

        # Early exit if provider recovered
        if is_available_fn is not None:
            try:
                if is_available_fn():
                    logger.info(
                        "smart_wait_early_recovery",
                        provider=provider_key,
                        waited_seconds=round(elapsed, 1),
                    )
                    return True
            except Exception:
                pass

        # Progress log every 60s
        if elapsed > 0 and int(elapsed) % 60 < check_interval:
            remaining = wait_seconds - elapsed
            logger.debug(
                "smart_wait_progress",
                provider=provider_key,
                waited=round(elapsed, 1),
                remaining=round(remaining, 1),
            )

    logger.info(
        "smart_wait_complete",
        provider=provider_key,
        total_waited=round(wait_seconds, 1),
    )
    return True
