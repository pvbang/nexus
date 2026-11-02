"""
Nexus - Channel Manager (OpenClaw-inspired).

Lifecycle management for all connected messaging channels.
"""

import asyncio
import structlog
from typing import Any, Optional

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage
from app.channels.routing import get_message_router

logger = structlog.get_logger(__name__)

# Per-channel connect timeout. A single slow/unreachable channel (e.g. a WhatsApp
# bridge that's down) must not eat the whole startup budget — it's bounded here
# and the other channels still connect concurrently.
_CHANNEL_CONNECT_TIMEOUT = 10.0


class ChannelManager:
    """
    Manages all channel instances and their lifecycle.

    OpenClaw pattern: Gateway manages channel connections as a control plane.
    """

    def __init__(self) -> None:
        self._channels: dict[str, AbstractChannel] = {}

    def register(self, channel: AbstractChannel) -> None:
        """Register a channel instance."""
        self._channels[channel.channel_type] = channel
        logger.info("channel_registered", type=channel.channel_type)

    async def connect_all(self) -> list[str]:
        """Connect all registered channels concurrently. Returns connected types.

        Channels connect in parallel (not sequentially) so one slow channel does
        not delay the others, and each connect is bounded by a per-channel timeout
        so an unreachable channel fails fast instead of stalling startup.
        """
        router = get_message_router()

        async def _connect_one(ch_type: str, channel: AbstractChannel) -> Optional[str]:
            try:
                channel.set_message_handler(router.route)
                await asyncio.wait_for(channel.connect(), timeout=_CHANNEL_CONNECT_TIMEOUT)
                logger.info("channel_connected", type=ch_type)
                return ch_type
            except asyncio.TimeoutError:
                logger.warning("channel_connect_timeout", type=ch_type,
                               timeout_s=_CHANNEL_CONNECT_TIMEOUT)
                return None
            except Exception:
                logger.exception("channel_connect_failed", type=ch_type)
                return None

        results = await asyncio.gather(
            *(_connect_one(t, ch) for t, ch in self._channels.items())
        )
        return [t for t in results if t]

    async def disconnect_all(self) -> None:
        """Disconnect all channels concurrently, bounded per-channel.

        Mirrors connect_all: a channel that hangs on disconnect must not block
        shutdown of the whole process, so each disconnect is timeout-bounded and
        they run in parallel.
        """
        async def _disconnect_one(ch_type: str, channel: AbstractChannel) -> None:
            try:
                await asyncio.wait_for(channel.disconnect(), timeout=_CHANNEL_CONNECT_TIMEOUT)
                logger.info("channel_disconnected", type=ch_type)
            except asyncio.TimeoutError:
                logger.warning("channel_disconnect_timeout", type=ch_type,
                               timeout_s=_CHANNEL_CONNECT_TIMEOUT)
            except Exception:
                logger.exception("channel_disconnect_error", type=ch_type)

        await asyncio.gather(
            *(_disconnect_one(t, ch) for t, ch in self._channels.items())
        )

    async def send(self, message: OutboundMessage) -> Optional[dict[str, Any]]:
        """Send a message through the appropriate channel."""
        channel = self._channels.get(message.channel_type)
        if channel is None:
            logger.warning("channel_not_found", type=message.channel_type)
            return None
        return await channel.send(message)

    def get(self, channel_type: str) -> Optional[AbstractChannel]:
        return self._channels.get(channel_type)

    def list_channels(self) -> list[dict[str, Any]]:
        return [
            {"type": ch.channel_type, "registered": True}
            for ch in self._channels.values()
        ]

    async def health_check_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for channel in self._channels.values():
            try:
                results.append(await channel.health_check())
            except Exception as e:
                results.append({"channel": channel.channel_type, "status": "error",
                                "error": str(e)})
        return results


_manager: Optional[ChannelManager] = None


def get_channel_manager() -> ChannelManager:
    global _manager
    if _manager is None:
        _manager = ChannelManager()
    return _manager
