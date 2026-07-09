"""
MekongAI Nexus - Smart Model Routing (Hermes-agent inspired).

Route simple messages to cheaper/faster models, complex ones to strongest.
Conservative by design: if uncertain, keep the primary model.

Uses ONLY structural heuristics (format-based): length, code blocks, URLs, multiline.
No keyword/semantic matching — that's LLM's job, and calling LLM to decide model
routing defeats the purpose (you'd already be paying for the expensive model).
"""

from __future__ import annotations

import re
from typing import Any, Dict

import structlog

logger = structlog.get_logger(__name__)

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def should_use_cheap_model(
    user_message: str,
    *,
    max_chars: int = 160,
    max_words: int = 28,
) -> bool:
    """Determine if a message is simple enough for a cheaper model.

    Uses ONLY structural signals (format-based, language-agnostic):
    - Message length (short = likely simple)
    - Multiline content (complex structure)
    - Code blocks / inline code (technical work)
    - URLs (needs web interaction)
    - Special characters density (code, commands)

    Conservative: returns True only for very short, structurally simple messages.
    """
    text = (user_message or "").strip()
    if not text:
        return False

    # Length checks — short messages in ANY language are likely simple
    if len(text) > max_chars:
        return False
    if len(text.split()) > max_words:
        return False

    # Multi-line = structured content = probably complex
    if text.count("\n") > 1:
        return False

    # Code indicators (language-agnostic — these are format markers)
    if "```" in text or "`" in text:
        return False

    # URL = probably needs web interaction
    if _URL_RE.search(text):
        return False

    # High special character density = likely code/commands/technical
    special_chars = sum(1 for c in text if c in "{}[]()<>|&;$#@!=/\\^~")
    if len(text) > 0 and special_chars / len(text) > 0.1:
        return False

    return True


def choose_model_for_turn(
    user_message: str,
    *,
    core_provider: str,
    core_model: str,
    agent_provider: str,
    agent_model: str,
    routing_enabled: bool = False,
    max_chars: int = 160,
    max_words: int = 28,
) -> Dict[str, Any]:
    """Resolve which model to use for this turn.

    Returns dict with provider, model, and routing_reason.
    """
    if routing_enabled and should_use_cheap_model(
        user_message, max_chars=max_chars, max_words=max_words,
    ):
        logger.debug(
            "smart_routing_cheap",
            message_preview=user_message[:50],
            provider=agent_provider,
            model=agent_model,
        )
        return {
            "provider": agent_provider,
            "model": agent_model,
            "routing_reason": "simple_turn",
            "is_cheap": True,
        }

    return {
        "provider": core_provider,
        "model": core_model,
        "routing_reason": "complex_or_default",
        "is_cheap": False,
    }
