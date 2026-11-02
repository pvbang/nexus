"""
Nexus - OpenClaw Bridge Package.

Communication layer between Nexus (Intelligence) and OpenClaw (Execution).

Architecture:
- Nexus = Brain (orchestration, memory, RAG, swarm, LLM)
- OpenClaw = Arms (channels, tool execution, UI, sandbox)

Communication via HTTP API (OpenAI-compatible) on port 18789.
"""

from app.openclaw.bridge import OpenClawBridge, get_openclaw_bridge
from app.openclaw.config import OpenClawConfig, get_openclaw_config
from app.openclaw.models import (
    InboundMessage,
    OutboundResponse,
    ToolInvokeRequest,
    ToolInvokeResult,
)

__all__ = [
    "OpenClawBridge",
    "get_openclaw_bridge",
    "OpenClawConfig",
    "get_openclaw_config",
    "InboundMessage",
    "OutboundResponse",
    "ToolInvokeRequest",
    "ToolInvokeResult",
]
