"""
Nexus - OpenClaw Pydantic Models.

Request/response models for OpenClaw Gateway API communication.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────
# Inbound (OpenClaw → Nexus)
# ─────────────────────────────────────────────────────────────────


class InboundEvent(str, Enum):
    """Events that OpenClaw sends to Nexus."""
    MESSAGE = "message"
    REACTION = "reaction"
    COMMAND = "command"
    ATTACHMENT = "attachment"
    TYPING = "typing"
    APPROVAL_RESPONSE = "approval_response"
    CHANNEL_STATUS = "channel_status"


class InboundAttachment(BaseModel):
    """An attachment in an inbound message."""
    type: str = "file"           # file, image, audio, video
    url: str = ""
    filename: str = ""
    mime_type: str = ""
    size_bytes: int = 0


class InboundMessage(BaseModel):
    """Message received from OpenClaw via webhook.

    Sent by OpenClaw hooks when a user message arrives on any channel.
    """
    event: InboundEvent = InboundEvent.MESSAGE
    channel: str = ""            # telegram, discord, slack, matrix, etc.
    session_id: str = ""         # OpenClaw session identifier
    sender: str = ""             # Sender identifier (channel-specific)
    sender_name: str = ""        # Human-readable sender name
    content: str = ""            # Message text content
    attachments: list[InboundAttachment] = Field(default_factory=list)
    reply_to: Optional[str] = None  # Message being replied to
    thread_id: Optional[str] = None  # Thread/conversation thread
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0

    @property
    def has_attachments(self) -> bool:
        return len(self.attachments) > 0

    @property
    def is_command(self) -> bool:
        return self.event == InboundEvent.COMMAND or self.content.startswith("/")


# ─────────────────────────────────────────────────────────────────
# Outbound (Nexus → OpenClaw)
# ─────────────────────────────────────────────────────────────────


class OutboundResponse(BaseModel):
    """Response to send back via OpenClaw channel."""
    session_id: str = ""
    content: str = ""
    channel: str = ""            # Target channel (if different from source)
    target: str = ""             # Target user/chat ID
    reply_to: Optional[str] = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatCompletionMessage(BaseModel):
    """OpenAI-compatible chat message."""
    role: str = "assistant"
    content: str = ""
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request for OpenClaw Gateway."""
    model: str = "default"
    messages: list[ChatCompletionMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    user: Optional[str] = None
    # OpenClaw extensions
    session_key: Optional[str] = None


class ChatCompletionChoice(BaseModel):
    """A choice in chat completion response."""
    index: int = 0
    message: ChatCompletionMessage = Field(default_factory=ChatCompletionMessage)
    finish_reason: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""
    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: list[ChatCompletionChoice] = Field(default_factory=list)
    usage: Optional[dict[str, int]] = None

    @property
    def content(self) -> str:
        if self.choices:
            return self.choices[0].message.content
        return ""


# ─────────────────────────────────────────────────────────────────
# Tool Invocation
# ─────────────────────────────────────────────────────────────────


class ToolInvokeRequest(BaseModel):
    """Request to invoke a tool on OpenClaw."""
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
    timeout: int = 60
    sandbox: bool = False        # Force sandbox execution


class ToolInvokeResult(BaseModel):
    """Result from OpenClaw tool invocation."""
    success: bool = False
    output: str = ""
    error: str = ""
    exit_code: int = 0
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────
# Health & Status
# ─────────────────────────────────────────────────────────────────


class GatewayStatus(BaseModel):
    """OpenClaw Gateway status."""
    healthy: bool = False
    version: str = ""
    uptime_s: float = 0.0
    channels_active: list[str] = Field(default_factory=list)
    sessions_count: int = 0
    last_check_at: float = 0.0
