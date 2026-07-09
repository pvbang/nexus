#!/usr/bin/env python3
"""
MekongAI Nexus - Hermes Agent MCP Wrapper.

Custom MCP server that exposes Hermes Agent's actual capabilities
(terminal execution, agent chat, batch runner, session search)
as MCP tools, bridging MekongAI's needs with Hermes internals.

This wrapper is started by HermesProcessManager instead of mcp_serve.py,
because mcp_serve.py only exposes messaging channel tools.

Usage:
    python -m app.hermes.mcp_wrapper           (stdio mode)
    python -m app.hermes.mcp_wrapper --http     (HTTP mode, port 9100)
"""

from __future__ import annotations

import json
import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hermes-mcp-wrapper")

# Ensure Hermes is importable
HERMES_PATH = os.environ.get("HERMES_PATH", "")
if HERMES_PATH and HERMES_PATH not in sys.path:
    sys.path.insert(0, HERMES_PATH)


# ------------------------------------------------------------------
# Tool Definitions (what we expose to MekongAI via MCP)
# ------------------------------------------------------------------

TOOLS = [
    {
        "name": "terminal",
        "description": (
            "Execute a shell command. Supports multiple backends: "
            "local (default), docker, ssh, modal, daytona, singularity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "backend": {
                    "type": "string",
                    "description": "Terminal backend",
                    "enum": ["local", "docker", "ssh", "modal", "daytona", "singularity"],
                    "default": "local",
                },
                "working_dir": {"type": "string", "description": "Working directory"},
                "timeout": {"type": "number", "description": "Timeout in seconds", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "chat",
        "description": (
            "Send a message through Hermes Agent's full conversation loop. "
            "Hermes has 20+ LLM providers and tool calling capability."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "User message"},
                "session_id": {"type": "string", "description": "Session ID for continuity"},
                "model": {"type": "string", "description": "LLM model override"},
                "provider": {"type": "string", "description": "LLM provider override"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "code_edit",
        "description": "Edit a file using AI. Reads the file, understands context, applies edits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Path to the file"},
                "instruction": {"type": "string", "description": "Edit instruction"},
            },
            "required": ["file", "instruction"],
        },
    },
    {
        "name": "session_search",
        "description": "Search past Hermes sessions using full-text search (FTS5).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "batch_run",
        "description": "Run multiple tasks in parallel with checkpointing for RL trajectory generation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "Task definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "model": {"type": "string"},
                        },
                    },
                },
                "parallel": {"type": "integer", "description": "Max parallel tasks", "default": 4},
                "checkpoint_dir": {"type": "string", "description": "Checkpoint directory"},
            },
            "required": ["tasks"],
        },
    },
]


# ------------------------------------------------------------------
# Tool Handlers
# ------------------------------------------------------------------


async def handle_terminal(args: dict[str, Any]) -> str:
    """Execute a shell command."""
    command = args.get("command", "")
    backend = args.get("backend", "local")
    working_dir = args.get("working_dir")
    timeout = args.get("timeout", 60)

    if not command:
        return json.dumps({"error": "Missing command"})

    if backend != "local":
        # For non-local backends, try to use Hermes terminal
        try:
            from agent.terminal import get_terminal
            terminal = get_terminal(backend)
            result = await terminal.run(command, cwd=working_dir, timeout=timeout)
            return json.dumps({"output": result, "backend": backend})
        except ImportError:
            return json.dumps({
                "error": f"Backend '{backend}' requires Hermes agent package. "
                "Install with: pip install -e . in hermes directory."
            })
        except Exception as e:
            return json.dumps({"error": f"Terminal error: {e}"})

    # Local execution
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
            return json.dumps({"error": f"Command timed out after {timeout}s", "command": command})

        output = stdout.decode("utf-8", errors="replace")
        err_output = stderr.decode("utf-8", errors="replace")

        return json.dumps({
            "output": output,
            "stderr": err_output if err_output else None,
            "return_code": proc.returncode,
            "command": command,
            "backend": "local",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Execution failed: {e}"})


async def handle_chat(args: dict[str, Any]) -> str:
    """Send message through Hermes agent loop."""
    message = args.get("message", "")
    if not message:
        return json.dumps({"error": "Missing message"})

    try:
        # Try to import Hermes AIAgent
        from run_agent import AIAgent
        agent = AIAgent(
            model=args.get("model", ""),
            provider=args.get("provider", ""),
        )
        response = await asyncio.to_thread(agent.chat, message)
        return json.dumps({"response": response}, ensure_ascii=False)
    except ImportError:
        # Fallback: use hermes CLI subprocess
        try:
            cmd = ["hermes", "chat", "--message", message]
            if args.get("model"):
                cmd.extend(["--model", args["model"]])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            return json.dumps({
                "response": stdout.decode("utf-8", errors="replace"),
                "via": "cli",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": f"Hermes chat failed: {e}. "
                "Ensure Hermes is installed: pip install -e . in hermes directory."
            })
    except Exception as e:
        return json.dumps({"error": f"Chat error: {e}"})


async def handle_code_edit(args: dict[str, Any]) -> str:
    """AI code editing."""
    file_path = args.get("file", "")
    instruction = args.get("instruction", "")

    if not file_path or not instruction:
        return json.dumps({"error": "Missing file or instruction"})

    # Read file content
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: {file_path}"})
    except Exception as e:
        return json.dumps({"error": f"Cannot read file: {e}"})

    # Use Hermes agent chat with file context
    edit_prompt = (
        f"Edit the following file according to the instruction.\n\n"
        f"File: {file_path}\n"
        f"Instruction: {instruction}\n\n"
        f"Current content:\n```\n{content[:10000]}\n```\n\n"
        f"Return ONLY the modified file content, nothing else."
    )

    result = await handle_chat({"message": edit_prompt, **{k: v for k, v in args.items() if k not in ("file", "instruction")}})

    try:
        parsed = json.loads(result)
        if "response" in parsed:
            return json.dumps({
                "file": file_path,
                "instruction": instruction,
                "result": parsed["response"][:5000],
                "status": "completed",
            }, ensure_ascii=False)
    except json.JSONDecodeError:
        pass

    return result


async def handle_session_search(args: dict[str, Any]) -> str:
    """Search Hermes sessions via FTS5."""
    query = args.get("query", "")
    limit = args.get("limit", 20)

    if not query:
        return json.dumps({"error": "Missing query"})

    try:
        from hermes_state import SessionDB
        db = SessionDB()
        results = db.search(query, limit=limit)
        return json.dumps({
            "query": query,
            "count": len(results),
            "results": results[:limit],
        }, ensure_ascii=False)
    except ImportError:
        return json.dumps({
            "error": "SessionDB not available. Install Hermes: pip install -e ."
        })
    except Exception as e:
        return json.dumps({"error": f"Search failed: {e}"})


async def handle_batch_run(args: dict[str, Any]) -> str:
    """Run batch tasks."""
    tasks = args.get("tasks", [])
    if not tasks:
        return json.dumps({"error": "Missing tasks"})

    try:
        from batch_runner import BatchRunner
        runner = BatchRunner(
            max_parallel=args.get("parallel", 4),
            checkpoint_dir=args.get("checkpoint_dir", ""),
        )
        results = await asyncio.to_thread(runner.run, tasks)
        return json.dumps({
            "task_count": len(tasks),
            "completed": len(results),
            "results": results,
        }, ensure_ascii=False)
    except ImportError:
        return json.dumps({
            "error": "BatchRunner not available. Install Hermes: pip install -e ."
        })
    except Exception as e:
        return json.dumps({"error": f"Batch run failed: {e}"})


# Handler dispatch
TOOL_HANDLERS = {
    "terminal": handle_terminal,
    "chat": handle_chat,
    "code_edit": handle_code_edit,
    "session_search": handle_session_search,
    "batch_run": handle_batch_run,
}


# ------------------------------------------------------------------
# MCP Server (stdio JSON-RPC)
# ------------------------------------------------------------------


class HermesMCPServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0)."""

    def __init__(self) -> None:
        self._initialized = False

    async def handle_request(self, request: dict) -> Optional[dict]:
        """Process a single JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        # Notifications (no id) — no response expected
        if req_id is None:
            if method == "notifications/initialized":
                self._initialized = True
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "hermes-mcp-wrapper",
                        "version": "1.0.0",
                    },
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            handler = TOOL_HANDLERS.get(tool_name)
            if not handler:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unknown tool: {tool_name}",
                    },
                }

            try:
                result_text = await handler(arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                    },
                }
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e}")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                        "isError": True,
                    },
                }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    async def run_stdio(self) -> None:
        """Run MCP server over stdin/stdout."""
        logger.info("Hermes MCP wrapper starting (stdio mode)")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout.buffer
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, asyncio.get_event_loop())

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break

                try:
                    request = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue

                response = await self.handle_request(request)
                if response is not None:
                    msg = json.dumps(response) + "\n"
                    writer.write(msg.encode("utf-8"))
                    await writer.drain()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Server error: {e}")

        logger.info("Hermes MCP wrapper stopped")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    server = HermesMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
