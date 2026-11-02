"""
Nexus - Hermes Agent API Endpoints.

REST API for Hermes Agent capabilities — ALL chạy IN-PROCESS.
Không cần subprocess, không cần cài thêm gì.

Endpoints:
    GET  /hermes/status       - Get status & capabilities
    GET  /hermes/health       - Quick health check
    GET  /hermes/tools        - List Hermes tool capabilities
    POST /hermes/terminal     - Execute terminal command
    POST /hermes/code-edit    - AI code editing
    POST /hermes/chat         - LLM chat with model/provider override
    POST /hermes/search       - Session search
    POST /hermes/batch        - Batch parallel LLM tasks
    POST /hermes/tools/call   - Call any Hermes tool by name
"""

import json
from typing import Any, Optional

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hermes.config import get_hermes_settings

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/hermes", tags=["Hermes Agent"])


# ------------------------------------------------------------------
# Request/Response Schemas
# ------------------------------------------------------------------


class HermesToolCallRequest(BaseModel):
    tool_name: str = Field(..., description="Name of the tool")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class HermesTerminalRequest(BaseModel):
    command: str = Field(..., description="Shell command to execute")
    backend: Optional[str] = Field("local", description="Backend: local|docker|ssh|modal|daytona|singularity")
    working_dir: Optional[str] = Field(None, description="Working directory")
    timeout: Optional[float] = Field(90, description="Timeout in seconds")


class HermesCodeEditRequest(BaseModel):
    file_path: str = Field(..., description="Path to the file")
    instruction: str = Field(..., description="Edit instruction")


class HermesChatRequest(BaseModel):
    message: str = Field(..., description="User message")
    model: Optional[str] = Field(None, description="Override LLM model")
    provider: Optional[str] = Field(None, description="Override LLM provider")


class HermesSearchRequest(BaseModel):
    query: str = Field(..., description="Search query")
    limit: int = Field(20, description="Max results", ge=1, le=100)


class HermesBatchRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(..., description="Task definitions [{prompt, model?}]")
    parallel: Optional[int] = Field(4, description="Max parallel (1-10)", ge=1, le=10)


# ------------------------------------------------------------------
# Helper: import handlers from tools.py
# ------------------------------------------------------------------

def _get_handlers():
    """Lazy import to avoid circular deps."""
    from app.hermes.tools import (
        _handle_hermes_terminal,
        _handle_hermes_code_edit,
        _handle_hermes_agent_chat,
        _handle_hermes_session_search,
        _handle_hermes_batch_run,
        _handle_hermes_list_tools,
        _handle_hermes_call_tool,
        _handle_hermes_status,
    )
    return {
        "terminal": _handle_hermes_terminal,
        "code_edit": _handle_hermes_code_edit,
        "chat": _handle_hermes_agent_chat,
        "search": _handle_hermes_session_search,
        "batch": _handle_hermes_batch_run,
        "list_tools": _handle_hermes_list_tools,
        "call_tool": _handle_hermes_call_tool,
        "status": _handle_hermes_status,
    }


def _check_enabled():
    """Raise 503 if Hermes is disabled."""
    settings = get_hermes_settings()
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="Hermes Agent is disabled in config (HERMES_ENABLED=false)")


def _parse_result(result_str: str) -> dict[str, Any]:
    """Parse JSON result from handler."""
    try:
        return json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return {"result": result_str}


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/status")
async def hermes_status() -> dict[str, Any]:
    """Get Hermes Agent status, capabilities, and configuration."""
    handlers = _get_handlers()
    result = await handlers["status"]({})
    return _parse_result(result)


@router.get("/health")
async def hermes_health() -> dict[str, Any]:
    """Quick health check."""
    settings = get_hermes_settings()
    return {
        "status": "active" if settings.enabled else "disabled",
        "mode": "in-process",
        "enabled": settings.enabled,
    }


@router.get("/tools")
async def hermes_list_tools() -> dict[str, Any]:
    """List Hermes tool capabilities and status."""
    _check_enabled()
    handlers = _get_handlers()
    result = await handlers["list_tools"]({})
    return _parse_result(result)


@router.post("/tools/call")
async def hermes_call_tool(req: HermesToolCallRequest) -> dict[str, Any]:
    """Call any Hermes tool by name."""
    _check_enabled()
    handlers = _get_handlers()
    result = await handlers["call_tool"]({"tool_name": req.tool_name, "arguments": req.arguments})
    return {"tool": req.tool_name, "result": _parse_result(result)}


@router.post("/terminal")
async def hermes_terminal(req: HermesTerminalRequest) -> dict[str, Any]:
    """Execute a shell command via Hermes terminal."""
    _check_enabled()
    settings = get_hermes_settings()
    if not settings.expose_terminal_tools:
        raise HTTPException(status_code=403, detail="Terminal tools disabled")

    handlers = _get_handlers()
    result = await handlers["terminal"]({
        "command": req.command,
        "backend": req.backend,
        "working_dir": req.working_dir,
        "timeout": req.timeout,
    })
    return _parse_result(result)


@router.post("/code-edit")
async def hermes_code_edit(req: HermesCodeEditRequest) -> dict[str, Any]:
    """AI-powered code editing."""
    _check_enabled()
    settings = get_hermes_settings()
    if not settings.expose_code_edit:
        raise HTTPException(status_code=403, detail="Code edit disabled")

    handlers = _get_handlers()
    result = await handlers["code_edit"]({
        "file_path": req.file_path,
        "instruction": req.instruction,
    })
    return _parse_result(result)


@router.post("/chat")
async def hermes_chat(req: HermesChatRequest) -> dict[str, Any]:
    """LLM chat with optional model/provider override."""
    _check_enabled()
    handlers = _get_handlers()
    result = await handlers["chat"]({
        "message": req.message,
        "model": req.model,
        "provider": req.provider,
    })
    return _parse_result(result)


@router.post("/search")
async def hermes_search(req: HermesSearchRequest) -> dict[str, Any]:
    """Search sessions via full-text search."""
    _check_enabled()
    settings = get_hermes_settings()
    if not settings.expose_session_search:
        raise HTTPException(status_code=403, detail="Session search disabled")

    handlers = _get_handlers()
    result = await handlers["search"]({"query": req.query, "limit": req.limit})
    return _parse_result(result)


@router.post("/batch")
async def hermes_batch(req: HermesBatchRequest) -> dict[str, Any]:
    """Run batch LLM tasks in parallel."""
    _check_enabled()
    settings = get_hermes_settings()
    if not settings.expose_batch_runner:
        raise HTTPException(status_code=403, detail="Batch runner disabled")

    handlers = _get_handlers()
    result = await handlers["batch"]({
        "tasks": req.tasks,
        "parallel": req.parallel,
    })
    return _parse_result(result)
