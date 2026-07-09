"""
MekongAI Nexus - Conversation Context Manager.

Lấy cảm hứng từ claude-mem: ghi nhớ lịch sử hội thoại, nén context cũ,
inject vào mỗi lượt trả lời để agent hiểu liên tục yêu cầu của user.

3-layer context strategy:
1. Recent messages (last N) → inject nguyên bản vào messages array
2. Older messages → nén thành summary paragraph
3. XSkill knowledge → đã có sẵn trong orchestrator

Enhanced (Phase 2 - Hermes-agent):
4. Compression continuation → when compressed, link child conversation_id via parent
5. User memory injection → persistent per-user facts loaded into every session
6. Session resume → full history + compression chain restoration

Solves the "goldfish memory" problem: agent forgets previous turns.
"""

import structlog
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ConversationMessageModel

logger = structlog.get_logger(__name__)

# Configuration
MAX_RECENT_MESSAGES = 20       # Keep last N messages verbatim
MAX_SUMMARY_MESSAGES = 40      # Summarize up to N older messages
MAX_CONTEXT_CHARS = 12_000     # Hard cap on injected context characters
SUMMARY_TRIGGER = 6            # Start compressing when history > this many


async def load_conversation_history(
    db: AsyncSession,
    conversation_id: str,
    exclude_current: bool = True,
    current_message_content: Optional[str] = None,
) -> list[dict[str, str]]:
    """
    Load lịch sử hội thoại từ DB cho conversation_id.

    Uses the SAME DB session as the caller (simple SELECT, no writes).
    This avoids connection pool exhaustion that can block SSE streaming.

    Returns empty list on any failure — pipeline degrades gracefully.

    PIPELINE-AUDIT MEDIUM-1 FIX: Added current_message_content parameter
    for precise exclusion. Previously used position-based exclusion (last
    user message) which could exclude the WRONG message under rapid-fire
    concurrent messages from the same user.

    Args:
        db: AsyncSession from the caller (orchestrator).
        conversation_id: Session/conversation ID to load.
        exclude_current: If True, exclude the most recent user message
            (since it will be added separately by the pipeline).
        current_message_content: If provided, use content-based exclusion
            instead of position-based. More reliable under concurrency.
    """
    if not conversation_id:
        return []

    try:
        return await _query_history(db, conversation_id, exclude_current, current_message_content)
    except Exception as e:
        logger.warning(
            "conversation_history_load_failed",
            conversation_id=conversation_id,
            error=str(e),
        )
        return []


async def _query_history(
    db: AsyncSession,
    conversation_id: str,
    exclude_current: bool,
    current_message_content: Optional[str] = None,
) -> list[dict[str, str]]:
    """Execute the actual history query on the given session."""
    # Only load user + assistant messages — skip title markers, system errors,
    # and tool messages which would confuse the LLM or break exclude_current.
    stmt = (
        select(ConversationMessageModel)
        .where(
            ConversationMessageModel.session_id == conversation_id,
            ConversationMessageModel.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessageModel.created_at.asc())
        .limit(MAX_RECENT_MESSAGES + MAX_SUMMARY_MESSAGES)
    )
    result = await db.execute(stmt)
    messages_raw = result.scalars().all()

    if not messages_raw:
        return []

    messages = [
        {"role": msg.role, "content": msg.content}
        for msg in messages_raw
        # Skip empty content
        if msg.content and msg.content.strip()
    ]

    # Exclude the current user message if requested
    # (it will be added separately by the calling pipeline)
    # PIPELINE-AUDIT MEDIUM-1 FIX: Use content-based exclusion when available
    # to prevent excluding the WRONG message under rapid-fire concurrent messages.
    if exclude_current:
        if current_message_content:
            # Content-based: remove the LAST occurrence of this exact message
            _excluded = False
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "user" and messages[i]["content"] == current_message_content:
                    messages = messages[:i] + messages[i + 1:]
                    _excluded = True
                    break
            # Fallback to position-based if content not found (message not yet committed)
            if not _excluded and messages and messages[-1]["role"] == "user":
                messages = messages[:-1]
        elif messages and messages[-1]["role"] == "user":
            # Position-based fallback (legacy behavior)
            messages = messages[:-1]

    logger.debug(
        "conversation_history_loaded",
        conversation_id=conversation_id,
        message_count=len(messages),
    )

    return messages


def build_context_messages(
    history: list[dict[str, str]],
    llm: Any = None,
) -> tuple[list[dict[str, str]], Optional[str]]:
    """
    Xây dựng messages array từ lịch sử, với compression cho context dài.

    Strategy (lấy cảm hứng từ claude-mem progressive disclosure):
    - Nếu <= SUMMARY_TRIGGER messages → trả về nguyên bản
    - Nếu > SUMMARY_TRIGGER → chia thành:
      * Older messages → nén thành 1 summary block (text)
      * Recent messages → giữ nguyên

    Returns:
        (recent_messages, summary_text)
        - recent_messages: List of message dicts to inject directly
        - summary_text: Optional compressed summary of older messages
    """
    if not history:
        return [], None

    if len(history) <= SUMMARY_TRIGGER:
        # Short history → inject as-is
        return history, None

    # Split into older + recent
    recent_count = min(MAX_RECENT_MESSAGES, len(history))
    older = history[:-recent_count]
    recent = history[-recent_count:]

    # Compress older messages into a summary
    summary = _compress_messages_to_summary(older)

    return recent, summary


def _compress_messages_to_summary(messages: list[dict[str, str]]) -> str:
    """
    Nén danh sách messages cũ thành summary text ngắn gọn.

    Claude-mem approach: Thay vì gọi LLM để nén (tốn token + latency),
    ta dùng heuristic extraction — lấy các user requests + assistant key points.

    Format output giống claude-mem observation summary:
    "[Earlier conversation summary]\\n- User asked about X\\n- Assistant provided Y\\n..."
    """
    if not messages:
        return ""

    summary_parts: list[str] = []
    char_budget = MAX_CONTEXT_CHARS // 2  # Reserve half for recent messages
    chars_used = 0

    for msg in messages:
        role = msg["role"]
        content = msg["content"].strip()

        if not content:
            continue

        if role == "user":
            # Extract user's request — keep generous 300 chars so LLM understands
            # what was asked (users often phrase complex multi-part requests)
            snippet = content[:300].replace("\n", " ")
            line = f"- User: {snippet}"
        elif role == "assistant":
            # Extract assistant's key response — 500 chars to capture the core
            # answer including key data/numbers. 150 was far too short: an answer
            # like "GDP VN 2024 tăng 7.09%, driven by..." was truncated to useless.
            snippet = content[:500].replace("\n", " ")
            line = f"- Assistant: {snippet}"
        elif role == "system":
            # Skip system messages in summary
            continue
        else:
            continue

        if chars_used + len(line) > char_budget:
            summary_parts.append("- ... (earlier messages omitted)")
            break

        summary_parts.append(line)
        chars_used += len(line)

    if not summary_parts:
        return ""

    return (
        "[Conversation history summary — earlier in this session]\n"
        + "\n".join(summary_parts)
    )


def inject_history_into_messages(
    system_message: dict[str, str],
    current_user_message: dict[str, str],
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Xây dựng messages array hoàn chỉnh với lịch sử hội thoại.

    Layout (giống claude-mem context injection):
    1. System prompt (with optional history summary appended)
    2. [Older history summary as system context — if exists]
    3. Recent conversation messages (user/assistant alternating)
    4. Current user message

    This gives the LLM full continuity — it "remembers" the conversation.

    Args:
        system_message: {"role": "system", "content": "..."}
        current_user_message: {"role": "user", "content": "current question"}
        history: Raw history from load_conversation_history()

    Returns:
        Complete messages array ready for LLM.chat()
    """
    recent_messages, summary = build_context_messages(history)

    messages: list[dict[str, str]] = []

    # 1. System prompt
    if summary:
        # Append summary to system prompt (claude-mem style: context injection)
        enhanced_system = (
            system_message["content"]
            + "\n\n## CONVERSATION CONTEXT (from earlier in this session)\n"
            + summary
            + "\n\nUse this context to maintain conversation continuity. "
            "The user may refer to things discussed earlier."
        )
        messages.append({"role": "system", "content": enhanced_system})
    else:
        messages.append(system_message)

    # 2. Recent history messages (verbatim)
    for msg in recent_messages:
        # Only include user and assistant messages (skip system/tool)
        if msg["role"] in ("user", "assistant"):
            messages.append(msg)

    # 3. Current user message
    messages.append(current_user_message)

    logger.info(
        "history_injected",
        recent_count=len(recent_messages),
        has_summary=summary is not None,
        total_messages=len(messages),
    )

    return messages


def build_swarm_context_from_history(
    history: list[dict[str, str]],
    current_message: str,
) -> str:
    """
    Xây dựng context string cho swarm pipeline từ lịch sử hội thoại.

    Swarm pipeline không dùng messages array mà dùng plain text context.
    Tạo context paragraph chứa summary + current question.

    Args:
        history: Raw history from load_conversation_history()
        current_message: Current user message

    Returns:
        Enhanced task description with conversation context
    """
    if not history:
        return current_message

    # Build summary of what was discussed — generous truncation so swarm
    # agents understand the full conversation arc (not just 200-char snippets)
    summary_parts: list[str] = []
    for msg in history[-10:]:  # Last 10 messages for swarm context
        role = msg["role"]
        content = msg["content"].strip()
        if role == "user":
            summary_parts.append(f"User: {content[:400]}")
        elif role == "assistant":
            summary_parts.append(f"Assistant: {content[:600]}")

    if not summary_parts:
        return current_message

    context = "\n".join(summary_parts)

    return (
        f"{current_message}\n\n"
        f"--- Conversation History (earlier in this session) ---\n"
        f"{context}\n"
        f"--- End of History ---\n\n"
        f"The user's current request may refer to the conversation above. "
        f"Use this context to understand what the user is asking for."
    )


# ── Phase 2: Compression Continuation ──


async def create_compression_continuation(
    parent_session_id: str,
    child_session_id: str,
    compression_summary: str,
    db: Optional[AsyncSession] = None,
) -> bool:
    """
    Link a child conversation to a parent via compression.

    When a conversation is compressed, a new child conversation_id can be
    created that starts with the compression summary. This maintains the
    chain of context across session boundaries.

    The link is stored as a special message (role='compression_link') in
    the child session, containing the parent ID and summary.

    Args:
        parent_session_id: The original conversation that was compressed.
        child_session_id: The new continuation conversation.
        compression_summary: The compression summary text.
        db: Optional DB session. If None, creates its own.

    Returns:
        True if link was created successfully.
    """
    import json

    link_data = json.dumps({
        "parent_session_id": parent_session_id,
        "compression_summary": compression_summary,
        "linked_at": __import__("time").time(),
    }, ensure_ascii=False)

    try:
        if db is not None:
            db.add(ConversationMessageModel(
                session_id=child_session_id,
                role="compression_link",
                content=link_data,
            ))
            await db.commit()
        else:
            from app.core.dependencies import _session_factory as _sf
            if _sf:
                async with _sf() as _db:
                    _db.add(ConversationMessageModel(
                        session_id=child_session_id,
                        role="compression_link",
                        content=link_data,
                    ))
                    await _db.commit()
            else:
                return False

        logger.info(
            "compression_continuation_created",
            parent=parent_session_id,
            child=child_session_id,
        )
        return True

    except Exception as e:
        logger.warning(
            "compression_continuation_failed",
            parent=parent_session_id,
            child=child_session_id,
            error=str(e),
        )
        return False


async def load_compression_chain_context(
    session_id: str,
    db: AsyncSession,
) -> Optional[str]:
    """
    Load compression chain context for a session.

    If this session is a continuation of a compressed conversation,
    load the compression summary from the parent link.

    Returns the compression summary text or None if no chain exists.
    """
    import json

    try:
        stmt = (
            select(ConversationMessageModel.content)
            .where(
                ConversationMessageModel.session_id == session_id,
                ConversationMessageModel.role == "compression_link",
            )
            .order_by(ConversationMessageModel.created_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        link_content = result.scalar_one_or_none()

        if not link_content:
            return None

        data = json.loads(link_content)
        summary = data.get("compression_summary", "")

        if summary:
            logger.debug(
                "compression_chain_loaded",
                session_id=session_id,
                parent=data.get("parent_session_id"),
            )

        return summary or None

    except Exception as e:
        logger.debug("compression_chain_load_failed", session_id=session_id, error=str(e))
        return None


# ── Phase 2+4: User Memory Integration ──


async def inject_user_memory_into_messages(
    messages: list[dict[str, str]],
    user_id: str,
) -> list[dict[str, str]]:
    """
    Inject persistent user memory into the message context.

    Loads user's stored preferences/facts and appends them to the system
    prompt. This gives the agent personalized context about the user
    across all sessions.

    Args:
        messages: Current messages array (system + history + user).
        user_id: User ID to load memory for.

    Returns:
        Enhanced messages with user memory injected into system prompt.
    """
    if not user_id or not messages:
        return messages

    try:
        from app.memory.user_memory import get_user_memory

        memory = await get_user_memory(user_id)
        injection = memory.get_injection_context()

        if not injection:
            return messages

        # Inject into system message (first message)
        if messages[0].get("role") == "system":
            messages[0] = {
                **messages[0],
                "content": messages[0]["content"] + f"\n\n{injection}",
            }
        else:
            # No system message — prepend one with user memory
            messages.insert(0, {
                "role": "system",
                "content": injection,
            })

        logger.debug(
            "user_memory_injected",
            user_id=user_id,
            injection_chars=len(injection),
        )

    except Exception as e:
        logger.debug("user_memory_injection_failed", user_id=user_id, error=str(e))

    return messages


async def load_full_session_context(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
    current_message: str,
    system_prompt: str,
) -> list[dict[str, str]]:
    """
    Load complete session context for resuming a conversation.

    Combines:
    1. System prompt
    2. User memory (persistent across sessions)
    3. Compression chain summary (if from a continued session)
    4. Conversation history
    5. Current user message

    This is the "full resume" path used when a user reopens a previous
    conversation. It ensures full continuity.

    Args:
        db: Database session.
        conversation_id: Session/conversation ID.
        user_id: User ID for memory injection.
        current_message: Current user message.
        system_prompt: Base system prompt.

    Returns:
        Complete messages array ready for LLM.
    """
    # 1. Load history
    history = await load_conversation_history(db, conversation_id)

    # 2. Load compression chain context (if session is a continuation)
    chain_summary = await load_compression_chain_context(conversation_id, db)

    # 3. Build system message with optional chain context
    system_content = system_prompt
    if chain_summary:
        system_content += (
            "\n\n## COMPRESSED CONVERSATION CONTEXT (from previous session)\n"
            f"{chain_summary}\n"
            "\nThis conversation continues from a previous compressed session. "
            "Use the summary above for continuity."
        )

    system_message = {"role": "system", "content": system_content}
    user_message = {"role": "user", "content": current_message}

    # 4. Build messages with history
    messages = inject_history_into_messages(system_message, user_message, history)

    # 5. Inject user memory
    messages = await inject_user_memory_into_messages(messages, user_id)

    return messages
