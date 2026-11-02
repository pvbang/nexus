"""
Nexus - Pydantic Schemas (API Request/Response).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================
# Task Schemas
# ============================================================


class TaskCreate(BaseModel):
    """Schema tạo task mới."""

    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    priority: int = Field(default=5, ge=1, le=10)
    user_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class TaskResponse(BaseModel):
    """Schema trả về thông tin task."""

    id: UUID
    title: str
    description: Optional[str]
    status: str
    priority: int
    user_id: Optional[str]
    parent_task_id: Optional[UUID]
    swarm_id: Optional[UUID]
    result: Optional[dict[str, Any]]
    error: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskStatusUpdate(BaseModel):
    """Cập nhật trạng thái task."""

    status: str
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


# ============================================================
# Agent Schemas
# ============================================================


class AgentCreate(BaseModel):
    """Schema tạo agent mới (có thể do Core Agent tự tạo)."""

    name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    personality: Optional[str] = None
    system_prompt: Optional[str] = None
    skills: Optional[dict[str, Any]] = None
    tools: Optional[list[str]] = None
    llm_model: str = "gpt-4o-mini"
    config: Optional[dict[str, Any]] = None
    # Rich identity fields
    avatar: Optional[str] = None
    expertise: Optional[list[str]] = None
    thinking_style: Optional[str] = None
    background: Optional[str] = None


class AgentResponse(BaseModel):
    """Schema trả về thông tin agent."""

    id: UUID
    name: str
    role: str
    description: Optional[str] = None
    status: str
    personality: Optional[str] = None
    skills: Optional[dict[str, Any]] = None
    tools: Optional[list[str]] = None
    llm_model: str
    system_prompt: Optional[str] = None
    parent_agent_id: Optional[UUID] = None
    swarm_id: Optional[UUID] = None
    # Rich identity fields
    avatar: Optional[str] = None
    expertise: Optional[list[str]] = None
    thinking_style: Optional[str] = None
    background: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentUpdate(BaseModel):
    """Schema cập nhật agent."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    personality: Optional[str] = None
    system_prompt: Optional[str] = None
    skills: Optional[dict[str, Any]] = None
    tools: Optional[list[str]] = None
    llm_model: Optional[str] = None
    # Rich identity fields
    avatar: Optional[str] = None
    expertise: Optional[list[str]] = None
    thinking_style: Optional[str] = None
    background: Optional[str] = None




# ============================================================
# Message Schemas
# ============================================================


class MessageCreate(BaseModel):
    """Schema tạo tin nhắn."""

    content: str = Field(..., min_length=1)
    receiver_id: Optional[UUID] = None
    swarm_id: Optional[UUID] = None
    channel: str = "direct"
    message_type: str = "text"
    metadata: Optional[dict[str, Any]] = None


class MessageResponse(BaseModel):
    """Schema trả về tin nhắn."""

    id: UUID
    sender_id: Optional[UUID]
    receiver_id: Optional[UUID]
    swarm_id: Optional[UUID]
    channel: str
    content: str
    message_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Chat / User Interaction Schemas
# ============================================================


class ChatRequest(BaseModel):
    """Schema cho user chat request."""

    # Long message support: raised to 500k, chunked processing handles large inputs at service layer.
    message: str = Field(..., min_length=1, max_length=500_000)
    # MED-03 FIX: Validate conversation_id format (alphanumeric + dash/underscore, max 100 chars).
    conversation_id: Optional[str] = Field(None, max_length=100, pattern=r"^[a-zA-Z0-9_\-]+$")
    user_id: Optional[str] = "default_user"
    model: Optional[str] = None  # Model override from frontend toolbar
    # H-03 FIX: Idempotency key — backend uses this to prevent duplicate task creation on SSE retry
    request_id: Optional[str] = Field(None, max_length=100)
    attachments: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    # Free model flag: use Kilo Gateway anonymous free models
    use_free: bool = False


class ChatResponse(BaseModel):
    """Schema cho chat response."""

    conversation_id: str
    message: str
    task_id: Optional[UUID] = None
    agents_spawned: list[AgentResponse] = []
    swarm_id: Optional[UUID] = None
    status: str = "completed"
    metadata: Optional[dict[str, Any]] = None


# ============================================================
# Tool Schemas
# ============================================================


class ToolRegister(BaseModel):
    """Schema đăng ký tool mới."""

    name: str
    description: str
    tool_type: str = "mcp"
    schema_: dict[str, Any] = Field(alias="schema")
    config: Optional[dict[str, Any]] = None
    sandbox_required: bool = False


class ToolResponse(BaseModel):
    """Schema trả về thông tin tool."""

    id: UUID
    name: str
    description: str
    tool_type: str
    is_active: bool
    sandbox_required: bool
    created_at: datetime

    model_config = {"from_attributes": True}
