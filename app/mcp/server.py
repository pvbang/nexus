"""
Nexus - MCP Server Mode (Hermes-agent inspired).

Expose Nexus tools and conversations to MCP clients (IDE, other agents).
Implements MCP protocol (JSON-RPC over stdio) for tool discovery and execution.

Inspired by: hermes-agent/mcp_serve.py

Usage::

    python -m app.mcp.server
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# MCP Protocol version
MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """MCP Server that exposes Nexus tools via JSON-RPC over stdio.

    Handles MCP protocol methods:
    - ``initialize`` — handshake with client
    - ``tools/list`` — list all available tools from ToolRegistry
    - ``tools/call`` — execute a tool via ToolExecutor
    - ``resources/list`` — list available resources (conversations, etc.)
    - ``resources/read`` — read a specific resource
    - ``notifications/initialized`` — client ready notification
    """

    def __init__(self) -> None:
        self._initialized = False
        self._client_info: dict[str, Any] = {}
        self._tool_executor: Any = None
        self._tool_registry: Any = None

    def _ensure_registry(self) -> None:
        """Lazy-load the tool registry and executor to avoid circular imports."""
        if self._tool_registry is None:
            from app.mcp.tool_registry import registry
            self._tool_registry = registry
        if self._tool_executor is None:
            from app.mcp.tool_executor import ToolExecutor
            self._tool_executor = ToolExecutor()

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _jsonrpc_response(id: Any, result: Any) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 success response."""
        return {"jsonrpc": "2.0", "id": id, "result": result}

    @staticmethod
    def _jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 error response."""
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": id, "error": error}

    # ------------------------------------------------------------------
    # MCP method handlers
    # ------------------------------------------------------------------

    async def _handle_initialize(self, id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Handle MCP 'initialize' handshake."""
        self._client_info = params.get("clientInfo", {})
        self._initialized = True

        logger.info("mcp_server_initialized",
                     client_name=self._client_info.get("name", "unknown"),
                     client_version=self._client_info.get("version", "unknown"))

        return self._jsonrpc_response(id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {
                "name": "nexus",
                "version": "1.0.0",
            },
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
        })

    async def _handle_tools_list(self, id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Handle 'tools/list' — aggregate all tools from ToolRegistry."""
        self._ensure_registry()
        definitions = self._tool_registry.get_definitions()

        tools = []
        for defn in definitions:
            func = defn.get("function", {})
            tool_schema: dict[str, Any] = {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
            }
            if "parameters" in func:
                tool_schema["inputSchema"] = func["parameters"]
            tools.append(tool_schema)

        return self._jsonrpc_response(id, {"tools": tools})

    async def _handle_tools_call(self, id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Handle 'tools/call' — dispatch via ToolExecutor."""
        self._ensure_registry()
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not tool_name:
            return self._jsonrpc_error(id, -32602, "Missing tool name")

        try:
            result = await self._tool_executor.execute(tool_name, arguments)
            content_text = result.get("output", str(result))
            is_error = not result.get("success", True)

            return self._jsonrpc_response(id, {
                "content": [
                    {"type": "text", "text": content_text},
                ],
                "isError": is_error,
            })
        except Exception as exc:
            logger.error("mcp_tool_call_error", tool=tool_name, error=str(exc))
            return self._jsonrpc_response(id, {
                "content": [
                    {"type": "text", "text": f"Tool execution failed: {exc}"},
                ],
                "isError": True,
            })

    async def _handle_resources_list(self, id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Handle 'resources/list' — list available conversation resources."""
        resources = [
            {
                "uri": "nexus://conversations/recent",
                "name": "Recent Conversations",
                "description": "Access recent conversation history from Nexus",
                "mimeType": "application/json",
            },
            {
                "uri": "nexus://tools/status",
                "name": "Tools Status",
                "description": "Current status and availability of all Nexus tools",
                "mimeType": "application/json",
            },
        ]
        return self._jsonrpc_response(id, {"resources": resources})

    async def _handle_resources_read(self, id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Handle 'resources/read' — read a specific resource."""
        uri = params.get("uri", "")

        if uri == "nexus://tools/status":
            self._ensure_registry()
            status = self._tool_registry.get_available_toolsets()
            content = json.dumps(status, indent=2, ensure_ascii=False)
            return self._jsonrpc_response(id, {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": content},
                ],
            })

        if uri == "nexus://conversations/recent":
            # Return a placeholder — real implementation would query DB
            content = json.dumps({
                "message": "Conversation access requires database connection. "
                           "Use the Nexus API for full conversation history.",
            })
            return self._jsonrpc_response(id, {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": content},
                ],
            })

        return self._jsonrpc_error(id, -32602, f"Unknown resource URI: {uri}")

    # ------------------------------------------------------------------
    # Request dispatcher
    # ------------------------------------------------------------------

    async def _handle_request(self, request: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Dispatch a JSON-RPC request to the appropriate handler."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        # Notifications (no id) — acknowledge silently
        if req_id is None:
            if method == "notifications/initialized":
                logger.info("mcp_client_ready")
            elif method == "notifications/cancelled":
                logger.info("mcp_request_cancelled", params=params)
            return None

        handlers = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "resources/list": self._handle_resources_list,
            "resources/read": self._handle_resources_read,
        }

        handler = handlers.get(method)
        if handler is None:
            return self._jsonrpc_error(req_id, -32601, f"Method not found: {method}")

        try:
            return await handler(req_id, params)
        except Exception as exc:
            logger.error("mcp_handler_error", method=method, error=str(exc))
            return self._jsonrpc_error(req_id, -32603, f"Internal error: {exc}")

    # ------------------------------------------------------------------
    # stdio transport
    # ------------------------------------------------------------------

    async def start_stdio(self) -> None:
        """Run the MCP server over stdin/stdout.

        Reads JSON-RPC messages (one per line) from stdin and writes
        responses to stdout.  Designed for use as an MCP subprocess.
        """
        logger.info("mcp_server_starting", transport="stdio")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        # Wrap stdout for async writing
        write_transport, write_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout,
        )
        writer = asyncio.StreamWriter(write_transport, write_protocol, reader, asyncio.get_event_loop())

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break  # EOF

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    request = json.loads(line_str)
                except json.JSONDecodeError as exc:
                    error_resp = self._jsonrpc_error(None, -32700, f"Parse error: {exc}")
                    writer.write((json.dumps(error_resp) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                response = await self._handle_request(request)
                if response is not None:
                    writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                    await writer.drain()

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("mcp_server_shutting_down")
        except Exception as exc:
            logger.error("mcp_server_error", error=str(exc))
        finally:
            writer.close()
            logger.info("mcp_server_stopped")


# ------------------------------------------------------------------
# Entry point: python -m app.mcp.server
# ------------------------------------------------------------------

if __name__ == "__main__":
    server = MCPServer()
    asyncio.run(server.start_stdio())
