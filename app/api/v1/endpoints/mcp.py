"""
MekongAI Nexus - MCP Server Management API.

Endpoints for connecting/disconnecting external MCP servers,
discovering their tools, and OAuth 2.1 PKCE authentication.
"""

from typing import Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.mcp.client import (
    MCPServerConfig,
    get_mcp_client_manager,
)
from app.mcp.oauth import (
    OAuthClientConfig,
    get_token_store,
)

logger = structlog.get_logger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class MCPServerConnectRequest(BaseModel):
    """Request to connect to an MCP server."""

    name: str = Field(..., description="Unique server name")
    command: str = Field("", description="Command for stdio transport (e.g. 'npx')")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    url: Optional[str] = Field(None, description="URL for HTTP transport")
    env: Optional[dict[str, str]] = Field(None, description="Extra environment variables")


class MCPToolCallRequest(BaseModel):
    """Request to call a tool on an MCP server."""

    server_name: str = Field(..., description="MCP server name")
    tool_name: str = Field(..., description="Tool name to call")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/servers", summary="List connected MCP servers and their tools")
async def list_servers() -> dict[str, Any]:
    """Return status of all connected MCP servers with their tools."""
    manager = get_mcp_client_manager()
    return manager.get_status()


@router.get("/tools", summary="List all tools from connected MCP servers")
async def list_mcp_tools() -> list[dict[str, Any]]:
    """Aggregate and return all tools from connected MCP servers."""
    manager = get_mcp_client_manager()
    return manager.get_all_tools()


@router.post("/connect", summary="Connect to an MCP server")
async def connect_server(request: MCPServerConnectRequest) -> dict[str, Any]:
    """Connect to a new MCP server (stdio or HTTP transport)."""
    manager = get_mcp_client_manager()

    # Check if already connected
    if request.name in manager.clients:
        existing = manager.clients[request.name]
        if existing.connected:
            raise HTTPException(
                status_code=409,
                detail=f"Server '{request.name}' is already connected",
            )

    config = MCPServerConfig(
        name=request.name,
        command=request.command,
        args=request.args,
        url=request.url,
        env=request.env,
        enabled=True,
    )

    success = await manager.connect_server(config)
    if not success:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to MCP server '{request.name}'",
        )

    client = manager.clients.get(request.name)
    return {
        "status": "connected",
        "server": request.name,
        "transport": config.transport,
        "tools": [t.get("name") for t in (client.tools if client else [])],
    }


@router.post("/disconnect/{name}", summary="Disconnect an MCP server")
async def disconnect_server(name: str) -> dict[str, Any]:
    """Disconnect from a specific MCP server."""
    manager = get_mcp_client_manager()
    success = await manager.disconnect_server(name)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Server '{name}' not found",
        )
    return {"status": "disconnected", "server": name}


@router.post("/reload", summary="Hot-reload MCP servers from current config")
async def reload_servers() -> dict[str, Any]:
    """Reconcile live MCP connections with the current config without restart.

    Reads the desired server list (``.mcp.json`` file if present, else the
    ``MCP_SERVERS`` env), diffs it against connected servers, and connects
    added/changed + disconnects removed/disabled ones. Tools are re-bridged.
    """
    from app.config import get_settings
    from app.mcp.client import parse_mcp_server_configs
    from app.mcp.config_reload import reload_mcp_servers
    from app.mcp.tool_registry import registry as _tool_registry
    import json as _json
    import os as _os

    settings = get_settings()
    desired = []
    # Prefer a .mcp.json file (hot-editable) over the env var.
    cfg_file = _os.path.join(settings.mcp_servers_dir, ".mcp.json")
    try:
        if _os.path.exists(cfg_file):
            with open(cfg_file, "r", encoding="utf-8") as fh:
                desired = parse_mcp_server_configs(fh.read())
        else:
            desired = parse_mcp_server_configs(settings.mcp_servers)
    except (OSError, _json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid MCP config: {exc}")

    manager = get_mcp_client_manager()
    diff = await reload_mcp_servers(manager, desired, _tool_registry)
    return {
        "status": "reloaded",
        "added": diff.added,
        "removed": diff.removed,
        "changed": diff.changed,
    }


@router.post("/call", summary="Call a tool on an MCP server")
async def call_tool(request: MCPToolCallRequest) -> dict[str, Any]:
    """Execute a tool on a specific MCP server."""
    manager = get_mcp_client_manager()
    result = await manager.call_tool(
        server_name=request.server_name,
        tool_name=request.tool_name,
        args=request.arguments,
    )
    return {"server": request.server_name, "tool": request.tool_name, "result": result}


# ------------------------------------------------------------------
# OAuth 2.1 PKCE Endpoints
# ------------------------------------------------------------------


class OAuthConfigRequest(BaseModel):
    """Request to register OAuth config for an MCP server."""

    server_name: str = Field(..., description="MCP server name")
    client_id: str = Field(..., description="OAuth client ID")
    authorization_endpoint: str = Field(..., description="OAuth authorization URL")
    token_endpoint: str = Field(..., description="OAuth token exchange URL")
    redirect_uri: str = Field(
        "http://localhost:8000/api/v1/mcp/oauth/callback",
        description="OAuth redirect URI",
    )
    scope: str = Field("mcp", description="OAuth scope")


@router.post("/oauth/register", summary="Register OAuth config for an MCP server")
async def register_oauth_config(request: OAuthConfigRequest) -> dict[str, Any]:
    """Register OAuth 2.1 configuration for an MCP server."""
    manager = get_mcp_client_manager()
    config = OAuthClientConfig(
        client_id=request.client_id,
        authorization_endpoint=request.authorization_endpoint,
        token_endpoint=request.token_endpoint,
        redirect_uri=request.redirect_uri,
        scope=request.scope,
    )
    manager.register_oauth_config(request.server_name, config)
    return {
        "status": "registered",
        "server_name": request.server_name,
        "client_id": request.client_id,
    }


@router.get(
    "/oauth/authorize/{server_name}",
    summary="Start OAuth flow for an MCP server",
)
async def oauth_authorize(server_name: str) -> dict[str, Any]:
    """
    Start OAuth 2.1 PKCE flow for a specific MCP server.

    Returns authorization URL for user to visit.
    """
    manager = get_mcp_client_manager()
    try:
        auth_url = await manager.start_oauth_flow(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "authorization_url": auth_url,
        "server_name": server_name,
        "instructions": "Visit the authorization_url to authenticate, then the callback will complete the flow.",
    }


@router.get("/oauth/callback", summary="Handle OAuth callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code"),
    state: str = Query("", description="State parameter"),
    server_name: str = Query("", description="Server name (passed via state or query)"),
) -> dict[str, Any]:
    """
    Handle OAuth 2.1 callback — exchange authorization code for tokens.

    This endpoint receives the redirect from the OAuth provider.
    """
    if not server_name and state:
        server_name = state  # Use state as server_name fallback

    if not server_name:
        raise HTTPException(
            status_code=400,
            detail="server_name is required (via query param or state)",
        )

    manager = get_mcp_client_manager()
    try:
        token = await manager.complete_oauth_flow(server_name, code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("oauth_callback_failed", server=server_name, error=str(e))
        raise HTTPException(
            status_code=502,
            detail=f"OAuth token exchange failed: {e}",
        )

    return {
        "status": "authenticated",
        "server_name": server_name,
        "token_type": token.token_type,
        "expires_in": token.expires_in,
        "scope": token.scope,
        "has_refresh_token": token.refresh_token is not None,
    }


@router.get("/oauth/status", summary="List OAuth status for all servers")
async def oauth_status() -> dict[str, Any]:
    """Return OAuth authentication status for all MCP servers."""
    token_store = get_token_store()
    return {
        "servers": token_store.get_status(),
        "total_authenticated": len(token_store.list_servers()),
    }
