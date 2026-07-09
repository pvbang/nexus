"""
MekongAI Nexus - Translation Tool.

Translate text between languages using the LLM.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


async def _handle_translate(args: dict[str, Any]) -> str:
    """Translate text to target language using LLM."""
    text = args.get("text", "").strip()
    target_language = args.get("target_language", "English").strip()
    source_language = args.get("source_language", "auto").strip()

    if not text:
        return tool_error("Text to translate is required")

    if len(text) > 10_000:
        return tool_error("Text too long (max 10,000 chars)")

    try:
        from app.services.llm_client import get_core_llm

        llm = get_core_llm()

        source_hint = f" from {source_language}" if source_language != "auto" else ""
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are a professional translator. Translate the following text{source_hint} "
                    f"to {target_language}. Preserve formatting, tone, and meaning. "
                    "Return ONLY the translated text, no explanations."
                ),
            },
            {"role": "user", "content": text},
        ]

        result = await llm.chat(messages=messages, max_tokens=4096)
        translated = result.get("content", "")

        if not translated:
            return tool_error("Translation returned empty result")

        return tool_result(
            success=True,
            output=f"🌐 Translated to {target_language}:\n\n{translated}",
            data={
                "source_language": source_language,
                "target_language": target_language,
                "original_length": len(text),
                "translated_length": len(translated),
                "translated_text": translated,
            },
        )
    except ImportError:
        return tool_error("LLM client not available for translation")
    except Exception as e:
        logger.error("translate_error", error=str(e))
        return tool_error(f"Translation failed: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="translate_text",
    toolset="utility",
    schema={
        "description": (
            "Translate text to any language using AI. "
            "Supports all major languages. Auto-detects source language."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to translate"},
                "target_language": {
                    "type": "string",
                    "description": "Target language (e.g., 'Vietnamese', 'English', 'Japanese', 'French')",
                },
                "source_language": {
                    "type": "string",
                    "description": "Source language (default: auto-detect)",
                },
            },
            "required": ["text", "target_language"],
        },
    },
    handler=_handle_translate,
    is_async=True,
    emoji="🌐",
)
