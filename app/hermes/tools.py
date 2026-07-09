"""
MekongAI Nexus - Hermes Agent Tools (IN-PROCESS).

All Hermes capabilities chạy trực tiếp trong MekongAI process — KHÔNG CẦN
subprocess, KHÔNG CẦN cài thêm bất kỳ package nào.

Sử dụng MekongAI infrastructure sẵn có:
- Terminal: asyncio.create_subprocess_shell (local) + optional Hermes backends
- Agent Chat: MekongAI LLMClient (multi-provider)
- Code Edit: MekongAI LLMClient + file I/O
- Session Search: MekongAI session_search (PostgreSQL FTS) + optional Hermes FTS5
- Batch Run: asyncio.gather in-process
- Multi-backend terminal: try import from Hermes, fallback to local

Tools:
    hermes_terminal:       Execute commands (local always works, Docker/SSH/Modal if Hermes installed)
    hermes_code_edit:      AI-powered code editing via LLM
    hermes_agent_chat:     Full LLM chat with tool calling
    hermes_session_search: Search sessions
    hermes_batch_run:      Parallel batch task execution
    hermes_list_tools:     List available Hermes capabilities
    hermes_call_tool:      Generic tool passthrough
    hermes_status:         Status check
"""

import asyncio
import json
import os
import sys
import time
from typing import Any, Optional

import structlog

from app.hermes.config import get_hermes_settings
from app.mcp.tool_registry import get_registry

logger = structlog.get_logger(__name__)

# Short category name consistent with registry
_TOOLSET = "hermes"
_CATEGORY = "hermes"

# Track available Hermes backends
_HERMES_AVAILABLE = False
_HERMES_PATH = ""

def _try_init_hermes() -> bool:
    """Try to add Hermes to sys.path for optional imports."""
    global _HERMES_AVAILABLE, _HERMES_PATH
    try:
        settings = get_hermes_settings()
        _HERMES_PATH = settings.hermes_abs_path
        if os.path.isdir(_HERMES_PATH) and _HERMES_PATH not in sys.path:
            sys.path.insert(0, _HERMES_PATH)
        # Test if Hermes core is importable
        import importlib
        importlib.import_module("hermes_state")
        _HERMES_AVAILABLE = True
    except Exception:
        _HERMES_AVAILABLE = False
    return _HERMES_AVAILABLE


# ------------------------------------------------------------------
# IN-PROCESS Tool Handlers (NO subprocess needed)
# ------------------------------------------------------------------


async def _handle_hermes_terminal(arguments: dict[str, Any]) -> str:
    """Execute a command — local always works, advanced backends if Hermes installed."""
    command = arguments.get("command", "")
    backend = arguments.get("backend", "local")
    logger.info("hermes_terminal_start", command=command[:100], backend=backend)
    if not command:
        return json.dumps({"error": "Missing required parameter: command"})

    working_dir = arguments.get("working_dir")
    timeout = arguments.get("timeout", 90)

    # Advanced backends (Docker, SSH, Modal, etc.) — try Hermes
    if backend != "local":
        if _HERMES_AVAILABLE:
            try:
                from agent.terminal import get_terminal
                terminal = get_terminal(backend)
                result = await asyncio.to_thread(
                    terminal.run, command, working_dir, timeout
                )
                return json.dumps({
                    "success": True,
                    "output": str(result),
                    "backend": backend,
                    "command": command,
                }, ensure_ascii=False)
            except ImportError:
                pass
            except Exception as e:
                return json.dumps({
                    "success": False,
                    "error": f"Backend '{backend}' error: {e}",
                    "backend": backend,
                })

        return json.dumps({
            "success": False,
            "error": (
                f"Backend '{backend}' requires Hermes Agent installation. "
                "Local execution is always available (use backend='local')."
            ),
            "available_backends": ["local"] + (
                ["docker", "ssh", "modal", "daytona", "singularity"]
                if _HERMES_AVAILABLE else []
            ),
        })

    # Local execution — always works, no external dependencies
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return json.dumps({
                "success": False,
                "error": f"Command timed out after {timeout}s",
                "command": command,
            })

        output = stdout.decode("utf-8", errors="replace")
        err_output = stderr.decode("utf-8", errors="replace")

        return json.dumps({
            "success": proc.returncode == 0,
            "output": output,
            "stderr": err_output if err_output else None,
            "return_code": proc.returncode,
            "command": command,
            "backend": "local",
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "error": f"Execution failed: {e}"})


async def _handle_hermes_code_edit(arguments: dict[str, Any]) -> str:
    """AI code editing — uses search/replace pattern for safe edits.

    HERMES-02 fix: Uses search/replace pattern instead of full file rewrite.
    LLM generates targeted search/replace blocks, not the entire file content.
    This is much safer for large files and prevents accidental data loss.
    """
    file_path = arguments.get("file_path", "")
    instruction = arguments.get("instruction", "")
    logger.info("hermes_code_edit_start", file_path=file_path, instruction=instruction[:100])
    if not file_path or not instruction:
        return json.dumps({"error": "Missing required parameters: file_path, instruction"})

    # Read file
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: {file_path}"})
    except Exception as e:
        return json.dumps({"error": f"Cannot read file: {e}"})

    # Use MekongAI's LLM to generate search/replace edits
    try:
        from app.services.llm_client import get_core_llm_with_fallback
        llm = get_core_llm_with_fallback()

        # Truncate large files for context
        max_chars = 30000
        truncated = content[:max_chars]
        if len(content) > max_chars:
            truncated += f"\n\n... [truncated, {len(content) - max_chars} more chars]"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert code editor. Given a file and an edit instruction, "
                    "return ONE OR MORE search/replace blocks.\n\n"
                    "FORMAT (each block):\n"
                    "<<<<<<< SEARCH\n"
                    "[exact text to find in the file]\n"
                    "=======\n"
                    "[replacement text]\n"
                    ">>>>>>> REPLACE\n\n"
                    "RULES:\n"
                    "- SEARCH text MUST match the file content EXACTLY (including whitespace)\n"
                    "- Include enough context lines to make the match unique\n"
                    "- Use multiple blocks for multiple changes\n"
                    "- Return ONLY the search/replace blocks, no explanations"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"File: {file_path}\n\n"
                    f"Edit instruction: {instruction}\n\n"
                    f"Current content:\n{truncated}"
                ),
            },
        ]

        response = await llm.chat(messages=messages)
        edit_response = response.get("content", "") if isinstance(response, dict) else str(response)

        if not edit_response:
            return json.dumps({"error": "LLM returned empty response"})

        # Parse search/replace blocks
        import re
        blocks = re.findall(
            r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE',
            edit_response,
            re.DOTALL,
        )

        if not blocks:
            # Fallback: if LLM didn't use search/replace format, try full rewrite
            # but only for small files (< 5000 chars) to be safe
            if len(content) < 5000:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(edit_response)
                return json.dumps({
                    "success": True,
                    "file": file_path,
                    "instruction": instruction,
                    "mode": "full_rewrite_fallback",
                    "original_size": len(content),
                    "edited_size": len(edit_response),
                }, ensure_ascii=False)
            return json.dumps({
                "error": "LLM did not return search/replace blocks and file is too large for full rewrite"
            })

        # Apply search/replace blocks sequentially
        edited_content = content
        applied = 0
        failed = []
        for search_text, replace_text in blocks:
            if search_text in edited_content:
                edited_content = edited_content.replace(search_text, replace_text, 1)
                applied += 1
            else:
                failed.append(search_text[:80])

        if applied == 0:
            return json.dumps({
                "error": f"No search blocks matched the file content. Tried {len(blocks)} blocks.",
                "failed_searches": failed,
            })

        # Write back
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(edited_content)

        return json.dumps({
            "success": True,
            "file": file_path,
            "instruction": instruction,
            "mode": "search_replace",
            "blocks_applied": applied,
            "blocks_failed": len(failed),
            "original_size": len(content),
            "edited_size": len(edited_content),
            "diff_chars": abs(len(edited_content) - len(content)),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"Code edit failed: {e}"})


async def _handle_hermes_agent_chat(arguments: dict[str, Any]) -> str:
    """Agent chat — uses MekongAI's LLMClient with any configured provider.

    HERMES-03 fix: Wires event_callback from execution context so frontend
    can see Hermes agent activity (thinking, tool calls, etc.).
    """
    message = arguments.get("message", "")
    logger.info("hermes_agent_chat_start", message_len=len(message))
    if not message:
        return json.dumps({"error": "Missing required parameter: message"})

    try:
        from app.services.llm_client import get_core_llm_with_fallback
        llm = get_core_llm_with_fallback()

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Hermes, a powerful AI assistant with deep knowledge "
                    "and analytical capabilities. Provide thorough, well-reasoned responses."
                ),
            },
            {"role": "user", "content": message},
        ]

        # HERMES-03: Emit event so frontend sees Hermes agent activity
        try:
            from app.core.execution_context import current_event_callback
            _ev_cb = current_event_callback.get(None)
            if _ev_cb is not None:
                await _ev_cb({
                    "event_type": "agent_thinking",
                    "agent_name": "Hermes Agent",
                    "agent_role": "hermes",
                    "action": "thinking",
                    "content": f"Hermes processing: {message[:200]}",
                    "preview": f"Hermes: {message[:100]}",
                })
        except Exception:
            pass  # Event emission is best-effort

        # Override model/provider if specified
        model_override = arguments.get("model")
        provider_override = arguments.get("provider")

        if model_override or provider_override:
            try:
                from app.services.llm_client import LLMClient
                from app.config import get_settings
                settings = get_settings()
                override_llm = LLMClient(
                    provider=provider_override or settings.core_llm_provider,
                    model=model_override or settings.core_llm_model,
                    api_key=settings.core_llm_api_key,
                    base_url=settings.core_llm_base_url,
                )
                response = await override_llm.chat(messages=messages)
            except Exception:
                # Fallback to default
                response = await llm.chat(messages=messages)
        else:
            response = await llm.chat(messages=messages)

        content = response.get("content", "") if isinstance(response, dict) else str(response)

        # HERMES-03: Emit completion event
        try:
            from app.core.execution_context import current_event_callback as _ev_cb2
            _cb = _ev_cb2.get(None)
            if _cb is not None:
                await _cb({
                    "event_type": "agent_response",
                    "agent_name": "Hermes Agent",
                    "agent_role": "hermes",
                    "action": "responded",
                    "content": content[:500],
                    "preview": content[:200],
                })
        except Exception:
            pass

        return json.dumps({
            "success": True,
            "response": content,
            "model": model_override or "default",
            "provider": provider_override or "default",
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"Chat failed: {e}"})


async def _handle_hermes_session_search(arguments: dict[str, Any]) -> str:
    """Search sessions — uses MekongAI PostgreSQL FTS, optional Hermes FTS5."""
    query = arguments.get("query", "")
    logger.info("hermes_session_search_start", query=query[:100])
    if not query:
        return json.dumps({"error": "Missing required parameter: query"})

    limit = arguments.get("limit", 20)
    results = []

    # Try MekongAI's session search first (PostgreSQL FTS)
    try:
        from app.core.session_search import search_sessions
        mekong_results = await search_sessions(query, limit=limit)
        if mekong_results:
            results.extend(mekong_results)
    except Exception as e:
        logger.debug("mekong_session_search_fallback", error=str(e))

    # Also try Hermes FTS5 if available
    if _HERMES_AVAILABLE:
        try:
            from hermes_state import SessionDB
            db = SessionDB()
            hermes_results = db.search(query, limit=limit)
            if hermes_results:
                for r in hermes_results:
                    r["source"] = "hermes"
                results.extend(hermes_results)
        except Exception as e:
            logger.debug("hermes_session_search_fallback", error=str(e))

    return json.dumps({
        "success": True,
        "query": query,
        "count": len(results),
        "results": results[:limit],
    }, ensure_ascii=False)


async def _handle_hermes_batch_run(arguments: dict[str, Any]) -> str:
    """Batch run — executes tasks in parallel using asyncio.gather.

    HERMES-04 fix: Added per-task timeout and overall batch timeout.
    """
    tasks = arguments.get("tasks", [])
    logger.info("hermes_batch_run_start", task_count=len(tasks))
    if not tasks:
        return json.dumps({"error": "Missing required parameter: tasks (non-empty array)"})

    parallel = min(arguments.get("parallel", 4), 10)  # Cap at 10
    per_task_timeout = arguments.get("timeout", 1200)  # HERMES-04: per-task timeout (seconds)

    try:
        from app.services.llm_client import get_core_llm_with_fallback
        llm = get_core_llm_with_fallback()

        semaphore = asyncio.Semaphore(parallel)
        results = []

        async def _run_one(task_def: dict, idx: int) -> dict:
            async with semaphore:
                prompt = task_def.get("prompt", "")
                if not prompt:
                    return {"index": idx, "error": "Missing prompt", "success": False}

                start = time.monotonic()
                try:
                    messages = [
                        {"role": "system", "content": "Complete the following task concisely."},
                        {"role": "user", "content": prompt},
                    ]
                    # HERMES-04: Per-task timeout
                    response = await asyncio.wait_for(
                        llm.chat(messages=messages),
                        timeout=per_task_timeout,
                    )
                    content = response.get("content", "") if isinstance(response, dict) else str(response)

                    elapsed = time.monotonic() - start
                    return {
                        "index": idx,
                        "prompt": prompt[:100],
                        "response": content[:2000],
                        "success": True,
                        "elapsed_seconds": round(elapsed, 2),
                    }
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - start
                    return {
                        "index": idx,
                        "prompt": prompt[:100],
                        "error": f"Task timed out after {per_task_timeout}s",
                        "success": False,
                        "elapsed_seconds": round(elapsed, 2),
                    }
                except Exception as e:
                    elapsed = time.monotonic() - start
                    return {
                        "index": idx,
                        "prompt": prompt[:100],
                        "error": str(e),
                        "success": False,
                        "elapsed_seconds": round(elapsed, 2),
                    }

        # HERMES-04: Overall batch timeout (5 min max)
        batch_timeout = min(per_task_timeout * len(tasks), 3000)
        try:
            batch_results = await asyncio.wait_for(
                asyncio.gather(
                    *[_run_one(t, i) for i, t in enumerate(tasks)],
                    return_exceptions=True,
                ),
                timeout=batch_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("hermes_batch_timeout", timeout=batch_timeout, tasks=len(tasks))
            batch_results = [{"index": i, "error": "Batch timed out", "success": False} for i in range(len(tasks))]
        results = list(batch_results)

        succeeded = sum(1 for r in results if r.get("success"))

        return json.dumps({
            "success": True,
            "task_count": len(tasks),
            "completed": succeeded,
            "failed": len(tasks) - succeeded,
            "parallel": parallel,
            "results": results,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"Batch run failed: {e}"})


async def _handle_hermes_list_tools(arguments: dict[str, Any]) -> str:
    """List available Hermes capabilities and their status."""
    logger.info("hermes_list_tools_called")
    settings = get_hermes_settings()

    tools_info = [
        {"name": "hermes_terminal", "status": "active", "note": "Local always works. Docker/SSH/Modal need Hermes installed."},
        {"name": "hermes_code_edit", "status": "active" if settings.expose_code_edit else "disabled", "note": "Uses MekongAI LLM"},
        {"name": "hermes_agent_chat", "status": "active", "note": "Uses MekongAI LLM (multi-provider)"},
        {"name": "hermes_session_search", "status": "active" if settings.expose_session_search else "disabled", "note": "PostgreSQL FTS + optional Hermes FTS5"},
        {"name": "hermes_batch_run", "status": "active" if settings.expose_batch_runner else "disabled", "note": "asyncio.gather in-process"},
    ]

    return json.dumps({
        "count": len(tools_info),
        "hermes_installed": _HERMES_AVAILABLE,
        "hermes_path": _HERMES_PATH,
        "advanced_backends": ["docker", "ssh", "modal", "daytona", "singularity"] if _HERMES_AVAILABLE else [],
        "tools": tools_info,
    }, ensure_ascii=False)


async def _handle_hermes_call_tool(arguments: dict[str, Any]) -> str:
    """Generic dispatcher — route to the correct handler by tool name."""
    tool_name = arguments.get("tool_name", "")
    logger.info("hermes_call_tool_start", tool_name=tool_name)
    if not tool_name:
        return json.dumps({"error": "Missing required parameter: tool_name"})

    tool_args = arguments.get("arguments", {})

    handler_map = {
        "terminal": _handle_hermes_terminal,
        "hermes_terminal": _handle_hermes_terminal,
        "code_edit": _handle_hermes_code_edit,
        "hermes_code_edit": _handle_hermes_code_edit,
        "agent_chat": _handle_hermes_agent_chat,
        "hermes_agent_chat": _handle_hermes_agent_chat,
        "chat": _handle_hermes_agent_chat,
        "session_search": _handle_hermes_session_search,
        "hermes_session_search": _handle_hermes_session_search,
        "batch_run": _handle_hermes_batch_run,
        "hermes_batch_run": _handle_hermes_batch_run,
    }

    handler = handler_map.get(tool_name)
    if handler:
        return await handler(tool_args)

    return json.dumps({"error": f"Unknown tool: {tool_name}. Use hermes_list_tools to see available tools."})


async def _handle_hermes_status(arguments: dict[str, Any]) -> str:
    """Get Hermes integration status."""
    logger.info("hermes_status_called")
    settings = get_hermes_settings()

    return json.dumps({
        "enabled": settings.enabled,
        "hermes_installed": _HERMES_AVAILABLE,
        "hermes_path": _HERMES_PATH,
        "mode": "in-process",  # Always in-process now
        "capabilities": {
            "terminal_local": True,
            "terminal_docker": _HERMES_AVAILABLE,
            "terminal_ssh": _HERMES_AVAILABLE,
            "terminal_modal": _HERMES_AVAILABLE,
            "code_edit": settings.expose_code_edit,
            "agent_chat": True,
            "session_search": settings.expose_session_search,
            "batch_run": settings.expose_batch_runner,
        },
        "note": (
            "All core features work in-process using MekongAI infrastructure. "
            "Advanced terminal backends (Docker/SSH/Modal) require Hermes installation."
        ),
    }, ensure_ascii=False)


# ------------------------------------------------------------------
# Availability Check
# ------------------------------------------------------------------


def _hermes_available() -> bool:
    """Always available — runs in-process using MekongAI infrastructure."""
    try:
        settings = get_hermes_settings()
        return settings.enabled
    except Exception:
        return False


# ------------------------------------------------------------------
# Tool Registration
# ------------------------------------------------------------------


def register_hermes_tools() -> int:
    """Register all Hermes tools in the MekongAI tool registry.

    All core Hermes tools run IN-PROCESS using MekongAI infrastructure.
    No subprocess or external Hermes installation required for basic features.
    Advanced backends (Docker, SSH, Modal) require Hermes Agent installation.

    Returns number of tools registered.
    """
    settings = get_hermes_settings()
    if not settings.enabled:
        logger.info("hermes_tools_skip_disabled")
        return 0

    # Try to init Hermes for advanced features (optional — failures are non-fatal)
    try:
        _try_init_hermes()
    except Exception as _init_err:
        logger.info("hermes_advanced_init_skipped", error=str(_init_err)[:200])

    registry = get_registry()
    count = 0

    # --- hermes_terminal ---
    if settings.expose_terminal_tools:
        registry.register(
            name="hermes_terminal",
            toolset=_TOOLSET,
            category=_CATEGORY,
            schema={
                "name": "hermes_terminal",
                "description": (
                    "Execute a shell command. Local backend always available. "
                    "Advanced backends (docker, ssh, modal, daytona, singularity) "
                    "available when Hermes Agent is installed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute",
                        },
                        "backend": {
                            "type": "string",
                            "description": "Terminal backend: local|docker|ssh|modal|daytona|singularity",
                            "enum": ["local", "docker", "ssh", "modal", "daytona", "singularity"],
                        },
                        "working_dir": {
                            "type": "string",
                            "description": "Working directory for the command",
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Timeout in seconds (default 90)",
                        },
                    },
                    "required": ["command"],
                },
            },
            handler=_handle_hermes_terminal,
            check_fn=_hermes_available,
            is_async=True,
            description="Execute shell command (local + optional remote backends)",
            emoji="🖥️",
        )
        count += 1

    # --- hermes_code_edit ---
    if settings.expose_code_edit:
        registry.register(
            name="hermes_code_edit",
            toolset=_TOOLSET,
            category=_CATEGORY,
            schema={
                "name": "hermes_code_edit",
                "description": (
                    "AI-powered code editing. Reads the file, understands context, "
                    "and applies the edit instruction using LLM intelligence."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file to edit",
                        },
                        "instruction": {
                            "type": "string",
                            "description": "Natural language instruction for the edit",
                        },
                    },
                    "required": ["file_path", "instruction"],
                },
            },
            handler=_handle_hermes_code_edit,
            check_fn=_hermes_available,
            is_async=True,
            description="AI-powered code editing via LLM",
            emoji="✏️",
        )
        count += 1

    # --- hermes_agent_chat ---
    registry.register(
        name="hermes_agent_chat",
        toolset=_TOOLSET,
        category=_CATEGORY,
        schema={
            "name": "hermes_agent_chat",
            "description": (
                "Send a message to an AI assistant with configurable model/provider. "
                "Can use any LLM configured in MekongAI (Bedrock, OpenAI, Gemini, OpenRouter, local)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message to send",
                    },
                    "model": {
                        "type": "string",
                        "description": "Override LLM model (e.g. claude-sonnet-4-20250514, gpt-4o)",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Override LLM provider (e.g. bedrock, openai, openrouter, gemini)",
                    },
                },
                "required": ["message"],
            },
        },
        handler=_handle_hermes_agent_chat,
        check_fn=_hermes_available,
        is_async=True,
        description="Chat with configurable LLM model/provider",
        emoji="💬",
    )
    count += 1

    # --- hermes_session_search ---
    if settings.expose_session_search:
        registry.register(
            name="hermes_session_search",
            toolset=_TOOLSET,
            category=_CATEGORY,
            schema={
                "name": "hermes_session_search",
                "description": "Search past sessions using full-text search.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 20)",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            },
            handler=_handle_hermes_session_search,
            check_fn=_hermes_available,
            is_async=True,
            description="Search sessions via full-text search",
            emoji="🔍",
        )
        count += 1

    # --- hermes_batch_run ---
    if settings.expose_batch_runner:
        registry.register(
            name="hermes_batch_run",
            toolset=_TOOLSET,
            category=_CATEGORY,
            schema={
                "name": "hermes_batch_run",
                "description": (
                    "Run multiple LLM tasks in parallel. "
                    "Uses asyncio.gather for concurrent execution with configurable parallelism."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "description": "Array of task definitions",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prompt": {"type": "string"},
                                    "model": {"type": "string"},
                                },
                            },
                        },
                        "parallel": {
                            "type": "integer",
                            "description": "Max parallel tasks (default 4, max 10)",
                        },
                    },
                    "required": ["tasks"],
                },
            },
            handler=_handle_hermes_batch_run,
            check_fn=_hermes_available,
            is_async=True,
            description="Parallel batch LLM tasks",
            emoji="📦",
        )
        count += 1

    # --- hermes_list_tools ---
    registry.register(
        name="hermes_list_tools",
        toolset=_TOOLSET,
        category=_CATEGORY,
        schema={
            "name": "hermes_list_tools",
            "description": "List all available Hermes capabilities and their status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        handler=_handle_hermes_list_tools,
        check_fn=_hermes_available,
        is_async=True,
        description="List Hermes capabilities",
        emoji="📋",
    )
    count += 1

    # --- hermes_call_tool ---
    registry.register(
        name="hermes_call_tool",
        toolset=_TOOLSET,
        category=_CATEGORY,
        schema={
            "name": "hermes_call_tool",
            "description": (
                "Call any Hermes tool by name. "
                "Use hermes_list_tools first to discover available tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool to call",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments to pass to the tool",
                    },
                },
                "required": ["tool_name"],
            },
        },
        handler=_handle_hermes_call_tool,
        check_fn=_hermes_available,
        is_async=True,
        description="Call any Hermes tool by name",
        emoji="🔧",
    )
    count += 1

    # --- hermes_status ---
    registry.register(
        name="hermes_status",
        toolset=_TOOLSET,
        category=_CATEGORY,
        schema={
            "name": "hermes_status",
            "description": "Get Hermes integration status, capabilities, and configuration.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        handler=_handle_hermes_status,
        check_fn=_hermes_available,
        is_async=True,
        description="Get Hermes status",
        emoji="📊",
    )
    count += 1

    logger.info(
        "hermes_tools_registered",
        count=count,
        mode="in-process",
        hermes_available=_HERMES_AVAILABLE,
    )
    return count
