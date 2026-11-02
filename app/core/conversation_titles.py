"""
Nexus - Conversation Title Auto-Generation.

Auto-generates conversation titles from the first user message using LLM summarization.
Implements caching to avoid re-generating titles for existing sessions.

Hermes-agent pattern: Every conversation gets a meaningful title for quick identification
in the session history browser.

Features:
- LLM-based title generation from first user message
- Fallback to truncated first message (if LLM unavailable)
- Background generation (non-blocking)
- Title caching (in-memory + DB persistence via 'title' role messages)
"""

import asyncio
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Configuration
MAX_TITLE_LENGTH = 80
TITLE_GENERATION_TIMEOUT = 10.0  # seconds


async def generate_title_from_message(
    message: str,
    llm_client: Optional[Any] = None,
) -> str:
    """
    Generate a concise, descriptive title from a user message.

    Uses LLM to create a short title summarizing the intent/topic.
    Falls back to truncation if LLM is unavailable.

    Args:
        message: First user message content.
        llm_client: Optional LLMClient instance for smart title generation.

    Returns:
        Generated title string (max MAX_TITLE_LENGTH chars).
    """
    # Clean the message
    clean_message = message.strip()
    if not clean_message:
        return "New Conversation"

    # If message is already short enough, use it directly
    if len(clean_message) <= MAX_TITLE_LENGTH and "\n" not in clean_message:
        return clean_message

    # Try LLM-based generation
    if llm_client is not None:
        try:
            title = await asyncio.wait_for(
                _llm_generate_title(clean_message, llm_client),
                timeout=TITLE_GENERATION_TIMEOUT,
            )
            if title:
                return title[:MAX_TITLE_LENGTH]
        except asyncio.TimeoutError:
            logger.debug("title_generation_timeout", message_len=len(clean_message))
        except Exception as e:
            logger.debug("title_generation_failed", error=str(e))

    # Fallback: extractive title from first line/sentence
    return _fallback_title(clean_message)


async def _llm_generate_title(message: str, llm_client: Any) -> str:
    """Generate title using LLM summarization."""
    # Truncate input to avoid wasting tokens on very long messages
    truncated = message[:2000] if len(message) > 2000 else message

    messages = [
        {
            "role": "system",
            "content": (
                "Generate a short, descriptive title (max 60 chars) for a conversation "
                "that starts with the user message below. The title should capture the "
                "main topic or intent. Return ONLY the title text, no quotes, no prefix. "
                "If the message is in Vietnamese, generate the title in Vietnamese. "
                "If in English, generate in English."
            ),
        },
        {
            "role": "user",
            "content": truncated,
        },
    ]

    result = await llm_client.chat(
        messages=messages,
        temperature=0.3,
        max_tokens=100,
    )

    title = (result.get("content") or "").strip()
    # Remove quotes if the LLM wrapped it
    if title.startswith('"') and title.endswith('"'):
        title = title[1:-1]
    if title.startswith("'") and title.endswith("'"):
        title = title[1:-1]

    return title


def _fallback_title(message: str) -> str:
    """Generate title by extracting first meaningful line/sentence."""
    # Take first line
    first_line = message.split("\n")[0].strip()

    # If first line is reasonable length, use it
    if len(first_line) <= MAX_TITLE_LENGTH:
        return first_line

    # Truncate at word boundary
    truncated = first_line[:MAX_TITLE_LENGTH]
    last_space = truncated.rfind(" ")
    if last_space > MAX_TITLE_LENGTH // 2:
        truncated = truncated[:last_space]

    return truncated + "..."


async def auto_generate_session_title(
    session_id: str,
    first_message: str,
    llm_client: Optional[Any] = None,
) -> str:
    """
    Auto-generate and persist a session title.

    Called when:
    1. A new conversation is created (first message arrives)
    2. Session is listed and has no title yet

    Args:
        session_id: Conversation/session ID.
        first_message: First user message content.
        llm_client: Optional LLM client for smart generation.

    Returns:
        Generated title.
    """
    title = await generate_title_from_message(first_message, llm_client)

    # Persist title to DB as a 'title' role message
    try:
        await _persist_title(session_id, title)
    except Exception as e:
        logger.warning(
            "title_persist_failed",
            session_id=session_id,
            error=str(e),
        )

    logger.info(
        "conversation_title_generated",
        session_id=session_id,
        title=title,
        used_llm=llm_client is not None,
    )

    return title


async def _persist_title(session_id: str, title: str) -> None:
    """Persist title to database as a 'title' role message."""
    try:
        from app.core.dependencies import _session_factory as _sf
        from app.models.database import ConversationMessageModel
        from sqlalchemy import select

        if not _sf:
            return

        async with _sf() as db:
            # Check if title already exists
            existing = await db.execute(
                select(ConversationMessageModel)
                .where(
                    ConversationMessageModel.session_id == session_id,
                    ConversationMessageModel.role == "title",
                )
                .limit(1)
            )
            title_msg = existing.scalar_one_or_none()

            if title_msg:
                title_msg.content = title
            else:
                db.add(ConversationMessageModel(
                    session_id=session_id,
                    role="title",
                    content=title,
                ))
            await db.commit()
    except Exception as e:
        logger.debug("title_db_persist_error", error=str(e))


async def get_or_generate_title(
    session_id: str,
    llm_client: Optional[Any] = None,
) -> Optional[str]:
    """
    Get existing title or generate one from session's first message.

    Checks DB for existing title first. If not found, loads first user
    message and generates a title.

    Args:
        session_id: Session/conversation ID.
        llm_client: Optional LLM client.

    Returns:
        Title string or None if session has no messages.
    """
    try:
        from app.core.dependencies import _session_factory as _sf
        from app.models.database import ConversationMessageModel
        from sqlalchemy import select

        if not _sf:
            return None

        async with _sf() as db:
            # Check for existing title
            title_result = await db.execute(
                select(ConversationMessageModel.content)
                .where(
                    ConversationMessageModel.session_id == session_id,
                    ConversationMessageModel.role == "title",
                )
                .limit(1)
            )
            existing_title = title_result.scalar_one_or_none()
            if existing_title:
                return existing_title

            # No title — get first user message
            first_msg_result = await db.execute(
                select(ConversationMessageModel.content)
                .where(
                    ConversationMessageModel.session_id == session_id,
                    ConversationMessageModel.role == "user",
                )
                .order_by(ConversationMessageModel.created_at.asc())
                .limit(1)
            )
            first_message = first_msg_result.scalar_one_or_none()

            if not first_message:
                return None

            # Generate and persist
            title = await generate_title_from_message(first_message, llm_client)
            db.add(ConversationMessageModel(
                session_id=session_id,
                role="title",
                content=title,
            ))
            await db.commit()
            return title

    except Exception as e:
        logger.warning("get_or_generate_title_failed", session_id=session_id, error=str(e))
        return None


def schedule_title_generation(
    session_id: str,
    first_message: str,
    llm_client: Optional[Any] = None,
) -> None:
    """
    Schedule title generation as a background task (fire-and-forget).

    Use this when you don't need the title immediately but want it
    generated asynchronously (e.g., after first message in a new session).
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            auto_generate_session_title(session_id, first_message, llm_client),
            name=f"title_gen:{session_id[:8]}",
        )
    except RuntimeError:
        # No running loop — skip
        logger.debug("title_generation_no_loop", session_id=session_id)
