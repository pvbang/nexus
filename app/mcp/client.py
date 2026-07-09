"""
MekongAI Nexus - MCP Client (Hermes-agent inspired).

Connect to external MCP servers for tool discovery and execution.
Supports stdio (subprocess) and HTTP/SSE transports.
OAuth 2.1 PKCE support for authenticated MCP servers.

Inspired by: hermes-agent MCP client integration
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import structlog

from app.mcp.oauth import (
    MCPOAuthClient,
    MCPTokenStore,
    OAuthClientConfig,
    OAuthToken,
    get_token_store,
)

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server connection."""

    name: str
    command: str = ""  # for stdio transport (e.g. "npx")
    args: list[str] = field(default_factory=list)  # e.g. ["-y", "@modelcontextprotocol/server-filesystem"]
    url: Optional[str] = None  # for HTTP transport (e.g. "http://localhost:3001")
    env: Optional[dict[str, str]] = None  # extra env vars for subprocess
    enabled: bool = True

    @property
    def transport(self) -> str:
        """Infer transport type from config."""
        if self.url:
            return "http"
        return "stdio"


# ------------------------------------------------------------------
# MCP Client (single server connection)
# ------------------------------------------------------------------


class MCPClient:
    """
    Client for a single MCP server.

    Supports two transports:
    - stdio: spawn a subprocess, communicate via stdin/stdout JSON-RPC
    - http: connect via HTTP POST for JSON-RPC calls

    OAuth 2.1 PKCE support:
    - Optional oauth_config for authenticated servers
    - Auto-injects Authorization header for HTTP requests
    - Auto-refreshes expired tokens
    """

    def __init__(
        self,
        config: MCPServerConfig,
        oauth_config: Optional[OAuthClientConfig] = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._process: Optional[asyncio.subprocess.Process] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._request_id: int = 0
        self._connected: bool = False
        self._tools: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

        # OAuth 2.1 PKCE support
        self._oauth_config = oauth_config
        self._oauth_client: Optional[MCPOAuthClient] = None
        self._token_store: MCPTokenStore = get_token_store()
        if oauth_config:
            self._oauth_client = MCPOAuthClient(oauth_config)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect using the appropriate transport."""
        if self.config.transport == "http":
            await self._connect_http()
        else:
            await self._connect_stdio()

    async def _connect_stdio(self) -> None:
        """Spawn subprocess and communicate via stdin/stdout JSON-RPC."""
        if not self.config.command:
            raise ValueError(f"MCP server '{self.name}': no command specified for stdio transport")

        env = {**os.environ}
        if self.config.env:
            env.update(self.config.env)

        cmd = [self.config.command, *self.config.args]
        logger.info("mcp_client_connecting_stdio", server=self.name, cmd=cmd)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._connected = True

            # Initialize: send initialize request
            init_result = await self._send_jsonrpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "MekongAI-Nexus", "version": "1.0.0"},
            })
            logger.info("mcp_client_initialized_stdio", server=self.name, result=str(init_result)[:200])

            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            # Discover tools
            await self._discover_tools()

        except Exception as e:
            logger.error("mcp_client_connect_failed", server=self.name, error=str(e))
            self._connected = False
            raise

    async def _connect_http(self) -> None:
        """Connect to MCP server via HTTP (with optional OAuth Bearer token)."""
        if not self.config.url:
            raise ValueError(f"MCP server '{self.name}': no URL specified for HTTP transport")

        logger.info("mcp_client_connecting_http", server=self.name, url=self.config.url)

        headers: dict[str, str] = {"Content-Type": "application/json"}

        # Inject OAuth Bearer token if available
        auth_header = await self._get_auth_header()
        if auth_header:
            headers["Authorization"] = auth_header

        self._http_client = httpx.AsyncClient(
            base_url=self.config.url,
            timeout=300.0,
            headers=headers,
        )

        try:
            # Initialize
            init_result = await self._send_jsonrpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "MekongAI-Nexus", "version": "1.0.0"},
            })
            self._connected = True
            logger.info("mcp_client_initialized_http", server=self.name, result=str(init_result)[:200])

            # Discover tools
            await self._discover_tools()

        except Exception as e:
            logger.error("mcp_client_connect_failed", server=self.name, error=str(e))
            self._connected = False
            raise

    # ------------------------------------------------------------------
    # JSON-RPC Communication
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_jsonrpc(self, method: str, params: Optional[dict] = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
        }
        if params is not None:
            request["params"] = params

        if self.config.transport == "http":
            return await self._send_http(request)
        else:
            return await self._send_stdio(request)

    async def _send_notification(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params

        if self.config.transport == "stdio" and self._process and self._process.stdin:
            msg = json.dumps(notification) + "\n"
            self._process.stdin.write(msg.encode())
            await self._process.stdin.drain()

    async def _send_stdio(self, request: dict) -> Any:
        """Send JSON-RPC via subprocess stdin/stdout."""
        async with self._lock:
            if not self._process or not self._process.stdin or not self._process.stdout:
                raise ConnectionError(f"MCP server '{self.name}' not connected")

            msg = json.dumps(request) + "\n"
            self._process.stdin.write(msg.encode())
            await self._process.stdin.drain()

            # Read response line
            try:
                line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=300.0,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"MCP server '{self.name}' response timeout")

            if not line:
                raise ConnectionError(f"MCP server '{self.name}' closed connection")

            response = json.loads(line.decode().strip())

            if "error" in response:
                err = response["error"]
                raise RuntimeError(
                    f"MCP error ({err.get('code', '?')}): {err.get('message', 'unknown')}"
                )

            return response.get("result")

    async def _send_http(self, request: dict) -> Any:
        """Send JSON-RPC via HTTP POST with OAuth token injection."""
        if not self._http_client:
            raise ConnectionError(f"MCP server '{self.name}' not connected")

        # Refresh OAuth token in headers if needed
        auth_header = await self._get_auth_header()
        extra_headers: dict[str, str] = {}
        if auth_header:
            extra_headers["Authorization"] = auth_header

        resp = await self._http_client.post("/", json=request, headers=extra_headers)
        resp.raise_for_status()
        response = resp.json()

        if "error" in response:
            err = response["error"]
            raise RuntimeError(
                f"MCP error ({err.get('code', '?')}): {err.get('message', 'unknown')}"
            )

        return response.get("result")

    # ------------------------------------------------------------------
    # OAuth Support
    # ------------------------------------------------------------------

    async def _get_auth_header(self) -> Optional[str]:
        """Get Authorization header value, auto-refreshing if expired."""
        token = self._token_store.get(self.name)
        if not token:
            return None

        # Auto-refresh expired token
        if token.is_expired and self._oauth_client:
            try:
                token = await self._oauth_client.ensure_valid_token(token)
                self._token_store.store(self.name, token)
            except Exception as e:
                logger.warning(
                    "oauth_token_refresh_failed",
                    server=self.name,
                    error=str(e),
                )
                return None

        return f"{token.token_type} {token.access_token}"

    async def authenticate_server(self) -> tuple[str, dict[str, str]]:
        """
        Start OAuth flow for this server.

        Returns:
            Tuple of (authorization_url, state_data) to redirect user.

        Raises:
            ValueError: If no OAuth config is set.
        """
        if not self._oauth_client:
            raise ValueError(
                f"Server '{self.name}' has no OAuth configuration"
            )

        auth_url, pkce = self._oauth_client.build_authorization_url()

        # Store PKCE for later code exchange (keyed by server name)
        state_data = {
            "server_name": self.name,
            "code_verifier": pkce.code_verifier,
            "code_challenge": pkce.code_challenge,
        }

        logger.info("oauth_flow_started", server=self.name)
        return auth_url, state_data

    async def complete_oauth(self, code: str, code_verifier: str) -> OAuthToken:
        """
        Complete OAuth flow by exchanging authorization code.

        Args:
            code: Authorization code from callback.
            code_verifier: PKCE code_verifier from initial flow.

        Returns:
            OAuthToken stored for this server.
        """
        if not self._oauth_client:
            raise ValueError(
                f"Server '{self.name}' has no OAuth configuration"
            )

        from app.mcp.oauth import PKCEChallenge

        pkce = PKCEChallenge(
            code_verifier=code_verifier,
            code_challenge="",  # Not needed for exchange
        )

        token = await self._oauth_client.exchange_code(code, pkce)
        self._token_store.store(self.name, token)

        logger.info("oauth_flow_completed", server=self.name)
        return token

    # ------------------------------------------------------------------
    # Tool Discovery & Execution
    # ------------------------------------------------------------------

    async def _discover_tools(self) -> None:
        """Call tools/list to discover available tools."""
        try:
            result = await self._send_jsonrpc("tools/list")
            self._tools = result.get("tools", []) if result else []
            logger.info(
                "mcp_tools_discovered",
                server=self.name,
                count=len(self._tools),
                tools=[t.get("name") for t in self._tools[:10]],
            )
        except Exception as e:
            logger.warning("mcp_tool_discovery_failed", server=self.name, error=str(e))
            self._tools = []

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return discovered tools from this server."""
        if not self._tools:
            await self._discover_tools()
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server."""
        if not self._connected:
            return json.dumps({"error": f"Server '{self.name}' not connected"})

        try:
            result = await self._send_jsonrpc("tools/call", {
                "name": name,
                "arguments": arguments,
            })

            # MCP spec: result.content is an array of text/image/audio/resource
            # blocks, plus optional structuredContent. Extract them all (the
            # legacy path dropped everything but text).
            from app.mcp.content import extract_tool_result

            return extract_tool_result(result)

        except Exception as e:
            logger.error("mcp_tool_call_failed", server=self.name, tool=name, error=str(e))
            return json.dumps({"error": f"Tool call failed: {e}"})

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        self._connected = False
        self._tools = []

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None
            logger.info("mcp_client_disconnected_stdio", server=self.name)

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            logger.info("mcp_client_disconnected_http", server=self.name)


# ------------------------------------------------------------------
# MCP Client Manager (manages multiple servers)
# ------------------------------------------------------------------


class MCPClientManager:
    """
    Manages connections to multiple MCP servers.

    Aggregates tools from all connected servers and routes
    tool calls to the correct server.
    Supports OAuth 2.1 PKCE authentication flows.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._oauth_configs: dict[str, OAuthClientConfig] = {}
        self._pending_pkce: dict[str, dict[str, str]] = {}  # state → pkce data

    @property
    def clients(self) -> dict[str, MCPClient]:
        return dict(self._clients)

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------

    async def connect_all(self, configs: list[MCPServerConfig]) -> dict[str, bool]:
        """
        Connect to all configured MCP servers.

        Returns: {server_name: connected_bool}
        """
        results: dict[str, bool] = {}

        for config in configs:
            if not config.enabled:
                logger.info("mcp_server_disabled", server=config.name)
                results[config.name] = False
                continue

            try:
                client = MCPClient(config)
                await client.connect()
                self._clients[config.name] = client
                results[config.name] = True
                logger.info("mcp_server_connected", server=config.name, tools=len(client.tools))
            except Exception as e:
                logger.error("mcp_server_connect_failed", server=config.name, error=str(e))
                results[config.name] = False

        return results

    async def connect_server(self, config: MCPServerConfig) -> bool:
        """Connect to a single MCP server."""
        try:
            client = MCPClient(config)
            await client.connect()
            self._clients[config.name] = client
            logger.info("mcp_server_connected", server=config.name, tools=len(client.tools))
            return True
        except Exception as e:
            logger.error("mcp_server_connect_failed", server=config.name, error=str(e))
            return False

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect a specific server."""
        client = self._clients.pop(name, None)
        if client:
            await client.disconnect()
            return True
        return False

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for name in list(self._clients.keys()):
            await self.disconnect_server(name)
        logger.info("mcp_all_servers_disconnected")

    # ------------------------------------------------------------------
    # Tool Aggregation
    # ------------------------------------------------------------------

    def get_all_tools(self) -> list[dict[str, Any]]:
        """Aggregate tools from all connected servers with server info."""
        all_tools: list[dict[str, Any]] = []
        for name, client in self._clients.items():
            if not client.connected:
                continue
            for tool in client.tools:
                all_tools.append({
                    "server": name,
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {}),
                })
        return all_tools

    async def call_tool(self, server_name: str, tool_name: str, args: dict[str, Any]) -> str:
        """Call a tool on a specific server."""
        client = self._clients.get(server_name)
        if not client:
            return json.dumps({"error": f"Server '{server_name}' not found"})
        if not client.connected:
            return json.dumps({"error": f"Server '{server_name}' not connected"})
        return await client.call_tool(tool_name, args)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return connection status for monitoring."""
        token_store = get_token_store()
        servers: list[dict[str, Any]] = []
        for name, client in self._clients.items():
            token = token_store.get(name)
            servers.append({
                "name": name,
                "connected": client.connected,
                "transport": client.config.transport,
                "tool_count": len(client.tools),
                "tools": [t.get("name") for t in client.tools],
                "oauth_configured": client._oauth_client is not None,
                "oauth_token_present": token is not None,
                "oauth_token_expired": token.is_expired if token else None,
            })

        return {
            "total_servers": len(self._clients),
            "connected": sum(1 for c in self._clients.values() if c.connected),
            "total_tools": sum(len(c.tools) for c in self._clients.values() if c.connected),
            "servers": servers,
        }

    # ------------------------------------------------------------------
    # OAuth Management
    # ------------------------------------------------------------------

    def register_oauth_config(
        self, server_name: str, config: OAuthClientConfig
    ) -> None:
        """Register OAuth config for a server."""
        self._oauth_configs[server_name] = config
        logger.info("oauth_config_registered", server=server_name)

    async def start_oauth_flow(self, server_name: str) -> str:
        """
        Start OAuth flow for a server.

        Returns authorization URL for user to visit.

        Raises:
            ValueError: If server not found or no OAuth config.
        """
        client = self._clients.get(server_name)
        oauth_config = self._oauth_configs.get(server_name)

        if not client and not oauth_config:
            raise ValueError(
                f"Server '{server_name}' not found and no OAuth config registered"
            )

        # If client exists but has no OAuth, inject config
        if client and not client._oauth_client and oauth_config:
            client._oauth_config = oauth_config
            client._oauth_client = MCPOAuthClient(oauth_config)

        if client and client._oauth_client:
            auth_url, state_data = await client.authenticate_server()
            # Store pending PKCE data keyed by server_name
            self._pending_pkce[server_name] = state_data
            return auth_url

        # No client yet, create OAuth client standalone
        if oauth_config:
            oauth_client = MCPOAuthClient(oauth_config)
            auth_url, pkce = oauth_client.build_authorization_url()
            self._pending_pkce[server_name] = {
                "server_name": server_name,
                "code_verifier": pkce.code_verifier,
                "code_challenge": pkce.code_challenge,
            }
            return auth_url

        raise ValueError(f"No OAuth configuration for server '{server_name}'")

    async def complete_oauth_flow(
        self, server_name: str, code: str
    ) -> OAuthToken:
        """
        Complete OAuth flow with authorization code.

        Args:
            server_name: Server name.
            code: Authorization code from callback.

        Returns:
            OAuthToken.
        """
        pkce_data = self._pending_pkce.pop(server_name, None)
        if not pkce_data:
            raise ValueError(
                f"No pending OAuth flow for server '{server_name}'"
            )

        client = self._clients.get(server_name)
        if client and client._oauth_client:
            return await client.complete_oauth(
                code, pkce_data["code_verifier"]
            )

        # Standalone exchange
        oauth_config = self._oauth_configs.get(server_name)
        if not oauth_config:
            raise ValueError(
                f"No OAuth config for server '{server_name}'"
            )

        from app.mcp.oauth import PKCEChallenge

        oauth_client = MCPOAuthClient(oauth_config)
        pkce = PKCEChallenge(
            code_verifier=pkce_data["code_verifier"],
            code_challenge=pkce_data["code_challenge"],
        )
        token = await oauth_client.exchange_code(code, pkce)

        token_store = get_token_store()
        token_store.store(server_name, token)

        return token


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_manager: Optional[MCPClientManager] = None


def get_mcp_client_manager() -> MCPClientManager:
    """Get or create the global MCP client manager."""
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager


def parse_mcp_server_configs(json_str: str) -> list[MCPServerConfig]:
    """Parse MCP_SERVERS env var (JSON array) into config objects.

    Example JSON::

        [
            {
                "name": "filesystem",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "enabled": true
            },
            {
                "name": "github",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "ghp_xxx"},
                "enabled": true
            },
            {
                "name": "custom-api",
                "url": "http://localhost:3001",
                "enabled": true
            }
        ]
    """
    if not json_str or not json_str.strip():
        return []

    try:
        raw = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("mcp_server_config_parse_error", error=str(e))
        return []

    configs: list[MCPServerConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        configs.append(MCPServerConfig(
            name=item.get("name", "unknown"),
            command=item.get("command", ""),
            args=item.get("args", []),
            url=item.get("url"),
            env=item.get("env"),
            enabled=item.get("enabled", True),
        ))

    return configs
