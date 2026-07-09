"""
MekongAI Nexus - Multi-Channel Gateway (OpenClaw-inspired).

Abstract channel interface + router for multi-channel messaging.
"""

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage
from app.channels.routing import MessageRouter, get_message_router
from app.channels.manager import ChannelManager, get_channel_manager

__all__ = [
    "AbstractChannel",
    "InboundMessage",
    "OutboundMessage",
    "MessageRouter",
    "get_message_router",
    "ChannelManager",
    "get_channel_manager",
]
