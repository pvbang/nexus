"""
MekongAI Nexus - Plugin SDK Base Classes.

Abstract base classes that plugins extend.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class PluginBase(ABC):
    """Base class all plugins must extend."""

    @abstractmethod
    async def activate(self) -> None:
        """Called when plugin is loaded and ready."""

    async def deactivate(self) -> None:
        """Called on shutdown. Override for cleanup."""

    async def health_check(self) -> dict[str, Any]:
        """Return plugin health status."""
        return {"status": "ok"}


class ChannelPlugin(PluginBase):
    """Base for messaging channel plugins (Telegram, Slack, etc.)."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the channel."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close channel connection."""

    @abstractmethod
    async def send_message(self, channel_id: str, content: str,
                           attachments: Optional[list] = None) -> dict[str, Any]:
        """Send a message to a channel target."""

    async def send_typing(self, channel_id: str) -> None:
        """Send typing indicator (optional)."""

    async def deactivate(self) -> None:
        await self.disconnect()


class MemoryPlugin(PluginBase):
    """Base for memory provider plugins."""

    @abstractmethod
    async def store(self, session_id: str, key: str, value: Any) -> None:
        """Store a memory entry."""

    @abstractmethod
    async def retrieve(self, session_id: str, query: str,
                       limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve relevant memories."""

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search across all sessions."""

    async def on_turn_end(self, session_id: str, user_msg: str,
                          assistant_msg: str) -> None:
        """Hook called after each conversation turn."""


class ContextEnginePlugin(PluginBase):
    """Base for context engine plugins."""

    @abstractmethod
    async def assemble(self, session_id: str, messages: list[dict],
                       token_budget: int) -> dict[str, Any]:
        """Assemble context under token budget. Returns {messages, estimated_tokens}."""

    @abstractmethod
    async def compact(self, session_id: str, messages: list[dict],
                      token_budget: int, force: bool = False) -> dict[str, Any]:
        """Compact context. Returns {ok, compacted, summary}."""

    async def ingest(self, session_id: str, message: dict) -> bool:
        """Ingest a message. Returns True if ingested."""
        return True


class ToolProviderPlugin(PluginBase):
    """Base for plugins that provide additional tools."""

    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return tool definitions for LLM function calling."""

    @abstractmethod
    async def execute_tool(self, tool_name: str,
                           args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call. Returns {success, output, data}."""
