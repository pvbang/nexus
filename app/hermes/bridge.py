"""
MekongAI Nexus - Hermes Agent MCP Bridge.

Communicates with the Hermes Agent subprocess via MCP protocol.
Supports stdio (JSON-RPC over stdin/stdout) and HTTP transports.

This bridge wraps the raw MCP protocol into high-level async methods
that MekongAI tools and services can call directly.
"""

import asyncio
import json
import time
from typing import Any, Optional

import structlog

from app.hermes.config import HermesSettings, get_hermes_settings
from app.hermes.manager import (
    HermesProcessManager,
    HermesStatus,
    get_hermes_manager,
)

logger = structlog.get_logger(__name__)


class HermesBridge:
    """
    MCP bridge to Hermes Agent.

    Provides async methods to:
    - Discover available Hermes tools
    - Execute tools on Hermes (terminal, code edit, search, etc.)
    - Send chat messages through Hermes agent loop
    - Manage Hermes sessions

    Connection modes:
    - stdio: JSON-RPC over stdin/stdout of the Hermes subprocess
    - http:  JSON-RPC over HTTP POST to Hermes MCP server
    """

    def __init__(
        self,
        settings: Optional[HermesSettings] = None,
        manager: Optional[HermesProcessManager] = None,
    ) -> None:
        self._settings = settings or get_hermes_settings()
        self._manager = manager or get_hermes_manager()
        self._request_id: int = 0
        self._lock = asyncio.Lock()
        self._connected: bool = False
        self._tools: list[dict[str, Any]] = []
        self._http_client: Optional[Any] = None  # httpx.AsyncClient (lazy import)
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected and self._manager.status == HermesStatus.RUNNING

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    # ------------------------------------------------------------------
    # Connection Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to Hermes Agent via MCP protocol.

        Ensures the Hermes process is running, then initializes MCP handshake.
        """
        if not self._settings.enabled:
            logger.info("hermes_bridge_disabled")
            return False

        # Ensure process is running
        if self._manager.status != HermesStatus.RUNNING:
            started = await self._manager.start()
            if not started:
                logger.error("hermes_bridge_process_not_started")
                return False

        # Wait a moment for process to be ready
        await asyncio.sleep(1.0)

        try:
            if self._settings.transport == "http":
                await self._connect_http()
            else:
                await self._connect_stdio()

            self._connected = True
            logger.info(
                "hermes_bridge_connected",
                transport=self._settings.transport,
                tools_count=len(self._tools),
            )
            return True

        except Exception as exc:
            logger.error("hermes_bridge_connect_failed", error=str(exc), exc_info=True)
            self._connected = False
            return False

    async def _connect_stdio(self) -> None:
        """Initialize MCP handshake over stdio."""
        # Send initialize
        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "MekongAI-Nexus-Hermes-Bridge", "version": "1.0.0"},
        })
        logger.debug("hermes_mcp_initialized", result=str(result)[:300])

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        # Discover tools
        await self._discover_tools()
        self._initialized = True

    async def _connect_http(self) -> None:
        """Initialize MCP handshake over HTTP."""
        import httpx

        self._http_client = httpx.AsyncClient(
            base_url=self._settings.http_url,
            timeout=self._settings.request_timeout,
            headers={"Content-Type": "application/json"},
        )

        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "MekongAI-Nexus-Hermes-Bridge", "version": "1.0.0"},
        })
        logger.debug("hermes_mcp_initialized_http", result=str(result)[:300])

        await self._discover_tools()
        self._initialized = True

    async def disconnect(self) -> None:
        """Disconnect from Hermes Agent."""
        self._connected = False
        self._initialized = False
        self._tools = []

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        logger.info("hermes_bridge_disconnected")

    # ------------------------------------------------------------------
    # JSON-RPC Transport
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_jsonrpc(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Send a JSON-RPC request and return the result."""
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
        }
        if params is not None:
            request["params"] = params

        effective_timeout = timeout or self._settings.request_timeout

        if self._settings.transport == "http":
            return await self._send_http(request, effective_timeout)
        else:
            return await self._send_stdio(request, effective_timeout)

    async def _send_notification(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            notification["params"] = params

        if self._settings.transport == "stdio":
            stdin = self._manager.stdin
            if stdin:
                msg = json.dumps(notification) + "\n"
                stdin.write(msg.encode())
                await stdin.drain()

    async def _send_stdio(self, request: dict, timeout: float) -> Any:
        """Send JSON-RPC via subprocess stdin/stdout."""
        async with self._lock:
            stdin = self._manager.stdin
            stdout = self._manager.stdout

            if not stdin or not stdout:
                raise ConnectionError("Hermes process stdin/stdout not available")

            msg = json.dumps(request) + "\n"
            stdin.write(msg.encode())
            await stdin.drain()

            try:
                line = await asyncio.wait_for(stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Hermes MCP response timeout ({timeout}s) for {request.get('method')}"
                )

            if not line:
                raise ConnectionError("Hermes process closed connection")

            response = json.loads(line.decode("utf-8", errors="replace").strip())

            if "error" in response:
                err = response["error"]
                raise RuntimeError(
                    f"Hermes MCP error ({err.get('code', '?')}): {err.get('message', 'unknown')}"
                )

            return response.get("result")

    async def _send_http(self, request: dict, timeout: float) -> Any:
        """Send JSON-RPC via HTTP POST."""
        if not self._http_client:
            raise ConnectionError("Hermes HTTP client not initialized")

        resp = await self._http_client.post(
            "/",
            json=request,
            timeout=timeout,
        )
        resp.raise_for_status()
        response = resp.json()

        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"Hermes MCP error ({err.get('code', '?')}): {err.get('message', 'unknown')}"
            )

        return response.get("result")

    # ------------------------------------------------------------------
    # Tool Discovery
    # ------------------------------------------------------------------

    async def _discover_tools(self) -> None:
        """Discover available tools from Hermes MCP server."""
        try:
            result = await self._send_jsonrpc("tools/list", timeout=15.0)
            self._tools = result.get("tools", []) if result else []
            logger.info(
                "hermes_tools_discovered",
                count=len(self._tools),
                tools=[t.get("name") for t in self._tools[:20]],
            )
        except Exception as exc:
            logger.warning("hermes_tool_discovery_failed", error=str(exc))
            self._tools = []

    async def refresh_tools(self) -> list[dict[str, Any]]:
        """Re-discover tools (after Hermes config change)."""
        await self._discover_tools()
        return self._tools

    # ------------------------------------------------------------------
    # High-Level API: Tool Execution
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: Optional[float] = None,
    ) -> str:
        """Call a tool on Hermes Agent.

        Args:
            tool_name: Name of the Hermes tool to call.
            arguments: Tool arguments dict.
            timeout: Override timeout (seconds).

        Returns:
            Tool result as string (text content from MCP response).
        """
        if not self.connected:
            return json.dumps({"error": "Hermes Agent not connected"})

        try:
            start = time.monotonic()
            result = await self._send_jsonrpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=timeout,
            )
            elapsed = time.monotonic() - start

            logger.info(
                "hermes_tool_called",
                tool=tool_name,
                elapsed_ms=round(elapsed * 1000),
            )

            return self._extract_text(result)

        except Exception as exc:
            logger.error(
                "hermes_tool_call_failed",
                tool=tool_name,
                error=str(exc),
            )
            return json.dumps({"error": f"Hermes tool call failed: {exc}"})

    @staticmethod
    def _extract_text(result: Any) -> str:
        """Extract text content from MCP tool result."""
        if isinstance(result, dict) and "content" in result:
            texts = []
            for block in result["content"]:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
            return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)

        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)

        return str(result) if result is not None else ""

    # ------------------------------------------------------------------
    # High-Level API: Terminal Execution
    # ------------------------------------------------------------------

    async def execute_terminal(
        self,
        command: str,
        backend: Optional[str] = None,
        working_dir: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Execute a command via Hermes terminal backend.

        Args:
            command: Shell command to execute.
            backend: Terminal backend (local|docker|ssh|modal|daytona|singularity).
                     If None, uses the configured default.
            working_dir: Working directory for command.
            timeout: Command timeout in seconds.

        Returns:
            Command output as string.
        """
        args: dict[str, Any] = {"command": command}
        if backend:
            args["backend"] = backend
        if working_dir:
            args["working_dir"] = working_dir

        return await self.call_tool(
            "terminal",
            args,
            timeout=timeout or self._settings.request_timeout,
        )

    # ------------------------------------------------------------------
    # High-Level API: Code Editing
    # ------------------------------------------------------------------

    async def code_edit(
        self,
        file_path: str,
        instruction: str,
        timeout: Optional[float] = None,
    ) -> str:
        """Edit code via Hermes agent loop.

        Args:
            file_path: Path to the file to edit.
            instruction: Natural language edit instruction.
            timeout: Override timeout.

        Returns:
            Edit result/diff as string.
        """
        return await self.call_tool(
            "code_edit",
            {"file": file_path, "instruction": instruction},
            timeout=timeout or 180.0,  # Code edit can be slow
        )

    # ------------------------------------------------------------------
    # High-Level API: Agent Chat
    # ------------------------------------------------------------------

    async def agent_chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Send a message through Hermes agent loop.

        This uses Hermes' full conversation capability with its own
        tool calling, context management, and LLM providers.

        Args:
            message: User message.
            session_id: Hermes session ID (None = new session).
            model: Override LLM model.
            provider: Override LLM provider.
            timeout: Override timeout.

        Returns:
            Agent response as string.
        """
        args: dict[str, Any] = {"message": message}
        if session_id:
            args["session_id"] = session_id
        if model:
            args["model"] = model
        if provider:
            args["provider"] = provider

        return await self.call_tool(
            "chat",
            args,
            timeout=timeout or 300.0,  # Agent chat can be very long
        )

    # ------------------------------------------------------------------
    # High-Level API: Session Search
    # ------------------------------------------------------------------

    async def search_sessions(
        self,
        query: str,
        limit: int = 20,
    ) -> str:
        """Search Hermes sessions via FTS5.

        Args:
            query: Search query.
            limit: Max results.

        Returns:
            Search results as JSON string.
        """
        return await self.call_tool(
            "session_search",
            {"query": query, "limit": limit},
        )

    # ------------------------------------------------------------------
    # High-Level API: Batch Runner
    # ------------------------------------------------------------------

    async def batch_run(
        self,
        tasks: list[dict[str, Any]],
        parallel: Optional[int] = None,
        checkpoint_dir: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Run batch tasks via Hermes Batch Runner.

        Args:
            tasks: List of task definitions.
            parallel: Max parallel tasks.
            checkpoint_dir: Directory for checkpoints.
            timeout: Override timeout.

        Returns:
            Batch results as JSON string.
        """
        args: dict[str, Any] = {"tasks": tasks}
        if parallel:
            args["parallel"] = parallel
        if checkpoint_dir:
            args["checkpoint_dir"] = checkpoint_dir

        return await self.call_tool(
            "batch_run",
            args,
            timeout=timeout or 600.0,  # Batch runs can be very long
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """Return bridge status including manager health."""
        manager_health = await self._manager.health_check()
        return {
            "connected": self.connected,
            "initialized": self._initialized,
            "transport": self._settings.transport,
            "tools_count": len(self._tools),
            "tools": [t.get("name") for t in self._tools],
            "process": manager_health,
        }


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_bridge: Optional[HermesBridge] = None


def get_hermes_bridge() -> HermesBridge:
    """Get or create the global Hermes bridge."""
    global _bridge
    if _bridge is None:
        _bridge = HermesBridge()
    return _bridge
