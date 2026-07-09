"""
MekongAI Nexus - Abstract Channel Interface (OpenClaw-inspired).

Base classes for all messaging channels.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class InboundMessage:
    """A message received from an external channel."""
    channel_type: str           # "telegram", "slack", "discord", "websocket", etc.
    channel_id: str             # chat/channel/room id on the platform
    sender_id: str              # user id on the platform
    sender_name: str = ""
    content: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    reply_to: str = ""          # message id being replied to
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class OutboundMessage:
    """A message to send to an external channel."""
    channel_type: str
    channel_id: str
    content: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    reply_to: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AbstractChannel(ABC):
    """
    Base interface for messaging channels.

    Mirrors OpenClaw channel plugin pattern — connect/disconnect/send/receive.
    """

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Unique channel type identifier, e.g. 'telegram'."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the channel service."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection and cleanup."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Send a message. Returns platform-specific result."""

    async def send_typing(self, channel_id: str) -> None:
        """Send typing indicator (optional)."""

    @abstractmethod
    def set_message_handler(self, handler) -> None:
        """
        Register a callback for inbound messages.
        handler signature: async def handler(msg: InboundMessage) -> None
        """

    async def health_check(self) -> dict[str, Any]:
        return {"channel": self.channel_type, "status": "ok"}
