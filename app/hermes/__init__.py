"""
Nexus - Hermes Agent Integration.

Hermes Agent (by Nous Research) runs as a subcomponent inside Nexus.
Communication is via MCP (Model Context Protocol) bridge — stdio or HTTP.

Architecture:
    Nexus (Master)
        └── HermesProcessManager (lifecycle)
            └── HermesBridge (MCP client)
                └── Hermes Agent subprocess (MCP server via mcp_serve.py)

Unique capabilities exposed:
    - 6 terminal backends (Local, Docker, SSH, Modal, Daytona, Singularity)
    - 20+ LLM providers via Hermes transport layer
    - Batch Runner for RL trajectory generation
    - Code editing with full agent loop
    - Session management with FTS5 search
"""

from app.hermes.config import HermesSettings, get_hermes_settings
from app.hermes.manager import HermesProcessManager, get_hermes_manager
from app.hermes.bridge import HermesBridge, get_hermes_bridge

__all__ = [
    "HermesSettings",
    "get_hermes_settings",
    "HermesProcessManager",
    "get_hermes_manager",
    "HermesBridge",
    "get_hermes_bridge",
]
