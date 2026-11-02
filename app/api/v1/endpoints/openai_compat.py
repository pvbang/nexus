"""
Nexus - OpenAI-Compatible API Endpoint.

Allows Nexus to be used as a drop-in LLM provider.
Implements ``/v1/chat/completions`` and ``/v1/models`` following the
OpenAI API specification.

Inspired by: hermes-agent openai-compat server

Routes are registered under ``/v1/`` (not ``/api/v1/``) so external tools
(e.g. Cursor, Continue.dev, LangChain) can point at Nexus directly::

    OPENAI_BASE_URL=http://localhost:8000/v1
    OPENAI_API_KEY=nexus
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(tags=["OpenAI Compatible"])


# ======================================================================
# Pydantic schemas (OpenAI-compatible)
# ======================================================================


class ChatMessage(BaseModel):
    """Single message in the conversation."""

    role: str = "user"
    content: Any = ""  # str or list[dict] for vision
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI ``/v1/chat/completions`` request body."""

    model: str = "nexus"
    messages: list[ChatMessage]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = 4096
    stream: bool = False
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[list[str] | str] = None
    user: Optional[str] = None


# ======================================================================
# Helpers
# ======================================================================


def _make_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _now() -> int:
    return int(time.time())


def _build_completion_response(
    completion_id: str,
    model: str,
    content: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> dict[str, Any]:
    """Build an OpenAI-compatible ChatCompletion JSON object."""
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": _now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": "nexus-v1",
    }


def _build_chunk(
    completion_id: str,
    model: str,
    delta_content: Optional[str] = None,
    delta_role: Optional[str] = None,
    finish_reason: Optional[str] = None,
) -> str:
    """Build a single SSE chunk for streaming responses."""
    delta: dict[str, Any] = {}
    if delta_role:
        delta["role"] = delta_role
    if delta_content is not None:
        delta["content"] = delta_content

    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": _now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def _resolve_model(requested: str) -> str:
    """Map the requested model name to an actual provider model.

    ``"nexus"`` or ``"nexus-core"`` → core LLM.
    ``"nexus-agent"`` → agent LLM.
    Otherwise pass through to core LLM.
    """
    if requested in ("nexus", "nexus-core", "mekongai-nexus"):
        return "core"
    if requested in ("nexus-agent", "nexus-fast"):
        return "agent"
    return "core"


def _messages_to_dicts(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Convert Pydantic models to plain dicts for the LLM client."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.name:
            entry["name"] = msg.name
        result.append(entry)
    return result


# ======================================================================
# Endpoints
# ======================================================================


@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request) -> Any:
    """OpenAI-compatible chat completion endpoint.

    Supports both regular and streaming responses.
    """
    completion_id = _make_id()
    model_target = _resolve_model(body.model)
    messages = _messages_to_dicts(body.messages)
    max_tokens = body.max_tokens or 4096

    try:
        from app.services.llm_client import get_agent_llm, get_core_llm

        llm = get_core_llm() if model_target == "core" else get_agent_llm()
    except Exception as exc:
        logger.error("openai_compat_llm_init_failed", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"LLM initialization failed: {exc}",
                    "type": "server_error",
                    "code": "internal_error",
                }
            },
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    if body.stream:

        async def _stream_generator():
            """Yield SSE chunks."""
            # Initial chunk with role
            yield _build_chunk(completion_id, body.model, delta_role="assistant")

            try:
                async for token in llm.chat_stream(
                    messages=messages,
                    temperature=body.temperature,
                    max_tokens=max_tokens,
                ):
                    yield _build_chunk(completion_id, body.model, delta_content=token)

                # Final chunk
                yield _build_chunk(completion_id, body.model, finish_reason="stop")
                yield "data: [DONE]\n\n"

            except Exception as exc:
                logger.error("openai_compat_stream_error", error=str(exc))
                error_chunk = {
                    "error": {
                        "message": str(exc),
                        "type": "server_error",
                        "code": "stream_error",
                    }
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            _stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------
    try:
        result = await llm.chat(
            messages=messages,
            temperature=body.temperature,
            max_tokens=max_tokens,
        )

        content = result.get("content", "")
        usage = result.get("usage", {})

        return JSONResponse(
            content=_build_completion_response(
                completion_id=completion_id,
                model=body.model,
                content=content,
                finish_reason=result.get("finish_reason", "stop"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        )
    except Exception as exc:
        logger.error("openai_compat_chat_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": str(exc),
                    "type": "server_error",
                    "code": "internal_error",
                }
            },
        )


@router.get("/models")
async def list_models() -> JSONResponse:
    """List available models — OpenAI ``/v1/models`` compatible."""
    models = [
        {
            "id": "nexus",
            "object": "model",
            "created": 1700000000,
            "owned_by": "nexus",
            "permission": [],
            "root": "nexus",
            "parent": None,
        },
        {
            "id": "nexus-core",
            "object": "model",
            "created": 1700000000,
            "owned_by": "nexus",
            "permission": [],
            "root": "nexus-core",
            "parent": None,
        },
        {
            "id": "nexus-agent",
            "object": "model",
            "created": 1700000000,
            "owned_by": "nexus",
            "permission": [],
            "root": "nexus-agent",
            "parent": None,
        },
    ]

    return JSONResponse(content={"object": "list", "data": models})
