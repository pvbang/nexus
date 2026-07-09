"""
MekongAI Nexus - Discord Channel (OpenClaw-inspired).

Discord bot integration using discord.py (optional) or httpx fallback.
"""

import asyncio
from typing import Any, Callable, Awaitable, Optional

import httpx
import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage
from app.config import get_settings

logger = structlog.get_logger(__name__)

# Optional discord.py import — graceful degradation if not installed
try:
    import discord
    from discord import app_commands

    DISCORD_PY_AVAILABLE = True
except ImportError:
    DISCORD_PY_AVAILABLE = False
    logger.info("discord_py_not_installed", hint="pip install discord.py for full Discord support")


# ---------------------------------------------------------------------------
# Discord REST API fallback helpers (when discord.py is not installed)
# ---------------------------------------------------------------------------
DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordChannel(AbstractChannel):
    """
    Discord bot channel.

    Supports two modes:
    - **Full mode** (discord.py installed): gateway events, slash commands,
      threads, embeds, file attachments.
    - **HTTP-only mode** (fallback): send messages via REST API, receive
      messages via polling (limited).

    Config env vars:
        DISCORD_BOT_TOKEN, DISCORD_GUILD_IDS, DISCORD_COMMAND_PREFIX
    """

    channel_type = "discord"

    def __init__(
        self,
        bot_token: str = "",
        guild_ids: Optional[list[int]] = None,
        command_prefix: str = "!",
    ) -> None:
        self._token = bot_token
        self._guild_ids: list[int] = guild_ids or []
        self._command_prefix = command_prefix
        self._handler: Optional[Callable[[InboundMessage], Awaitable[Optional[str]]]] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._running = False

        # discord.py objects (only when library is available)
        self._client: Any = None  # discord.Client
        self._tree: Any = None  # app_commands.CommandTree
        self._bot_task: Optional[asyncio.Task] = None
        self._bot_user_id: Optional[str] = None

    # ------------------------------------------------------------------
    # AbstractChannel interface
    # ------------------------------------------------------------------

    def set_message_handler(self, handler: Any) -> None:
        self._handler = handler

    async def connect(self) -> None:
        settings = get_settings()
        self._token = self._token or settings.discord_bot_token or ""
        if not self._token:
            raise ValueError("DISCORD_BOT_TOKEN not configured")

        # Parse guild IDs from config if not passed directly
        if not self._guild_ids and settings.discord_guild_ids:
            self._guild_ids = [
                int(gid.strip())
                for gid in settings.discord_guild_ids.split(",")
                if gid.strip()
            ]

        self._command_prefix = settings.discord_command_prefix or self._command_prefix
        self._running = True

        if DISCORD_PY_AVAILABLE:
            await self._connect_gateway()
        else:
            await self._connect_http_only()

    async def disconnect(self) -> None:
        self._running = False
        if self._client and DISCORD_PY_AVAILABLE:
            await self._client.close()
        if self._bot_task:
            self._bot_task.cancel()
        if self._http:
            await self._http.aclose()
        logger.info("discord_disconnected")

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Send a message to a Discord channel by channel ID."""
        # Use discord.py client if available, otherwise REST API
        if DISCORD_PY_AVAILABLE and self._client and self._client.is_ready():
            return await self._send_via_gateway(message)
        return await self._send_via_rest(message)

    async def send_typing(self, channel_id: str) -> None:
        """Send typing indicator to a Discord channel."""
        if DISCORD_PY_AVAILABLE and self._client and self._client.is_ready():
            try:
                ch = self._client.get_channel(int(channel_id))
                if ch:
                    await ch.typing()
            except Exception:
                pass
        elif self._http:
            try:
                await self._http.post(
                    f"{DISCORD_API_BASE}/channels/{channel_id}/typing",
                    headers=self._rest_headers,
                )
            except Exception:
                pass

    async def health_check(self) -> dict[str, Any]:
        if DISCORD_PY_AVAILABLE and self._client:
            if self._client.is_ready():
                user = self._client.user
                return {
                    "channel": "discord",
                    "status": "ok",
                    "mode": "gateway",
                    "bot": str(user) if user else "unknown",
                    "guilds": len(self._client.guilds),
                }
            return {"channel": "discord", "status": "connecting", "mode": "gateway"}

        if self._http:
            try:
                resp = await self._http.get(
                    f"{DISCORD_API_BASE}/users/@me",
                    headers=self._rest_headers,
                )
                data = resp.json()
                return {
                    "channel": "discord",
                    "status": "ok",
                    "mode": "http",
                    "bot": data.get("username", "unknown"),
                }
            except Exception as e:
                return {"channel": "discord", "status": "error", "error": str(e)}

        return {"channel": "discord", "status": "disconnected"}

    # ------------------------------------------------------------------
    # Internal: REST API helpers
    # ------------------------------------------------------------------

    @property
    def _rest_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
        }

    async def _send_via_rest(self, message: OutboundMessage) -> dict[str, Any]:
        """Send message via Discord REST API."""
        if not self._http:
            self._http = httpx.AsyncClient(timeout=30.0)

        payload: dict[str, Any] = {}

        # Build embed if metadata requests it
        embed = message.metadata.get("embed")
        if embed:
            payload["embeds"] = [embed] if isinstance(embed, dict) else embed
        else:
            payload["content"] = message.content[:2000]  # Discord 2000 char limit

        # Thread support
        thread_id = message.metadata.get("thread_id")
        target_channel = thread_id or message.channel_id

        # Reply reference
        if message.reply_to:
            payload["message_reference"] = {"message_id": message.reply_to}

        try:
            resp = await self._http.post(
                f"{DISCORD_API_BASE}/channels/{target_channel}/messages",
                headers=self._rest_headers,
                json=payload,
            )
            return resp.json()
        except Exception as e:
            logger.error("discord_send_error", error=str(e))
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Internal: discord.py gateway mode
    # ------------------------------------------------------------------

    async def _connect_gateway(self) -> None:
        """Connect via discord.py gateway (full features)."""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = False

        self._client = discord.Client(intents=intents)
        self._tree = app_commands.CommandTree(self._client)

        self._setup_gateway_handlers()
        self._setup_slash_commands()

        # Start bot in background task
        self._bot_task = asyncio.create_task(self._run_bot())
        logger.info("discord_gateway_starting")

    async def _run_bot(self) -> None:
        """Run the discord.py client in background."""
        try:
            await self._client.start(self._token)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("discord_bot_error")

    def _setup_gateway_handlers(self) -> None:
        """Register discord.py event handlers."""
        client = self._client

        @client.event
        async def on_ready() -> None:
            self._bot_user_id = str(client.user.id) if client.user else None
            logger.info(
                "discord_connected",
                bot=str(client.user),
                guilds=len(client.guilds),
            )
            # Sync slash commands to guilds
            if self._guild_ids:
                for gid in self._guild_ids:
                    guild_obj = discord.Object(id=gid)
                    self._tree.copy_global_to(guild=guild_obj)
                    await self._tree.sync(guild=guild_obj)
            else:
                await self._tree.sync()
            logger.info("discord_slash_commands_synced")

        @client.event
        async def on_message(msg: discord.Message) -> None:
            # Ignore own messages
            if msg.author == client.user:
                return
            # Ignore bots
            if msg.author.bot:
                return

            content = msg.content.strip()

            # Respond to mentions or prefix commands
            is_mention = client.user is not None and client.user.mentioned_in(msg)
            is_prefix_cmd = content.startswith(self._command_prefix)

            if not is_mention and not is_prefix_cmd:
                # Only respond in DMs without prefix/mention
                if not isinstance(msg.channel, discord.DMChannel):
                    return

            # Strip mention from content
            if is_mention and client.user:
                content = content.replace(f"<@{client.user.id}>", "").strip()
                content = content.replace(f"<@!{client.user.id}>", "").strip()

            # Strip prefix
            if is_prefix_cmd:
                content = content[len(self._command_prefix):].strip()

            if not content:
                return

            # Build attachments list
            attachments = []
            for att in msg.attachments:
                attachments.append({
                    "filename": att.filename,
                    "url": att.url,
                    "size": att.size,
                    "content_type": att.content_type,
                })

            inbound = InboundMessage(
                channel_type="discord",
                channel_id=str(msg.channel.id),
                sender_id=str(msg.author.id),
                sender_name=msg.author.display_name,
                content=content,
                attachments=attachments,
                reply_to=str(msg.reference.message_id) if msg.reference else "",
                metadata={
                    "message_id": str(msg.id),
                    "guild_id": str(msg.guild.id) if msg.guild else "",
                    "channel_name": getattr(msg.channel, "name", "DM"),
                    "is_dm": isinstance(msg.channel, discord.DMChannel),
                },
            )

            if self._handler:
                # Show typing while processing
                async with msg.channel.typing():
                    response = await self._handler(inbound)
                if response:
                    await self._send_response(msg.channel, response, msg)

    async def _send_response(
        self,
        channel: Any,
        response: str,
        original_msg: Any = None,
    ) -> None:
        """Send response, creating thread for long conversations."""
        # If response is long, create a thread
        if len(response) > 1500 and original_msg and hasattr(original_msg, "create_thread"):
            try:
                thread = await original_msg.create_thread(
                    name=f"Response to {original_msg.author.display_name}",
                    auto_archive_duration=60,
                )
                # Split into chunks of 2000 chars
                for chunk in _split_message(response):
                    await thread.send(chunk)
                return
            except Exception:
                pass  # Fallback to normal send

        # Build embed for formatted responses
        if len(response) > 300:
            embed = discord.Embed(
                description=response[:4096],
                color=discord.Color.blue(),
            )
            await channel.send(embed=embed, reference=original_msg)
        else:
            for chunk in _split_message(response):
                await channel.send(chunk, reference=original_msg)

    async def _send_via_gateway(self, message: OutboundMessage) -> dict[str, Any]:
        """Send message via discord.py client."""
        try:
            channel = self._client.get_channel(int(message.channel_id))
            if not channel:
                return {"error": f"Channel {message.channel_id} not found"}

            # Thread support
            thread_id = message.metadata.get("thread_id")
            if thread_id:
                thread = self._client.get_channel(int(thread_id))
                if thread:
                    channel = thread

            # Embed support
            embed_data = message.metadata.get("embed")
            if embed_data and isinstance(embed_data, dict):
                embed = discord.Embed.from_dict(embed_data)
                sent = await channel.send(embed=embed)
            else:
                for chunk in _split_message(message.content):
                    sent = await channel.send(chunk)

            return {"ok": True, "message_id": str(sent.id)}
        except Exception as e:
            logger.error("discord_gateway_send_error", error=str(e))
            return {"error": str(e)}

    def _setup_slash_commands(self) -> None:
        """Register slash commands on the command tree."""
        tree = self._tree

        @tree.command(name="ask", description="Ask MekongAI a question")
        @app_commands.describe(question="Your question for the AI")
        async def slash_ask(interaction: discord.Interaction, question: str) -> None:
            await interaction.response.defer(thinking=True)
            inbound = InboundMessage(
                channel_type="discord",
                channel_id=str(interaction.channel_id),
                sender_id=str(interaction.user.id),
                sender_name=interaction.user.display_name,
                content=question,
                metadata={
                    "interaction_id": str(interaction.id),
                    "guild_id": str(interaction.guild_id) if interaction.guild_id else "",
                    "slash_command": "ask",
                },
            )
            response = ""
            if self._handler:
                response = await self._handler(inbound) or "No response."
            # Follow up (deferred)
            if len(response) > 2000:
                embed = discord.Embed(description=response[:4096], color=discord.Color.green())
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(response)

        @tree.command(name="status", description="Check MekongAI Nexus status")
        async def slash_status(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="🤖 MekongAI Nexus Status",
                color=discord.Color.green(),
            )
            embed.add_field(name="Status", value="✅ Online", inline=True)
            embed.add_field(
                name="Guilds",
                value=str(len(self._client.guilds)),
                inline=True,
            )
            embed.add_field(name="Channel", value="Discord", inline=True)
            await interaction.response.send_message(embed=embed)

        @tree.command(name="help", description="Show MekongAI help")
        async def slash_help(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="📖 MekongAI Nexus Help",
                description="Multi-agent AI system accessible via Discord.",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="Commands",
                value=(
                    "**/ask** `<question>` — Ask the AI anything\n"
                    "**/status** — Check system status\n"
                    "**/help** — Show this help message\n"
                ),
                inline=False,
            )
            embed.add_field(
                name="Mentions",
                value="You can also mention the bot: `@MekongAI <question>`",
                inline=False,
            )
            embed.add_field(
                name="Prefix Commands",
                value=f"Use `{self._command_prefix}` prefix: `{self._command_prefix}ask <question>`",
                inline=False,
            )
            await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # Internal: HTTP-only fallback mode (no discord.py)
    # ------------------------------------------------------------------

    async def _connect_http_only(self) -> None:
        """Connect via REST API only (limited functionality)."""
        self._http = httpx.AsyncClient(timeout=30.0)
        # Verify token
        resp = await self._http.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers=self._rest_headers,
        )
        data = resp.json()
        if "id" not in data:
            raise ConnectionError(f"Discord auth failed: {data}")
        self._bot_user_id = data["id"]
        logger.info(
            "discord_http_connected",
            bot=data.get("username", "unknown"),
            mode="http_only",
        )
        logger.warning(
            "discord_limited_mode",
            hint="Install discord.py for full features: pip install discord.py",
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _split_message(text: str, limit: int = 2000) -> list[str]:
    """Split a message into chunks respecting Discord's 2000 char limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at newline
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
