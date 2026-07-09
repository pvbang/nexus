"""
MekongAI Nexus - Custom Exceptions.

Backward-compatible re-exports from the new error_codes module.
All exception classes now carry structured ErrorCode for reliable classification.
"""

# Re-export everything from the new error_codes module
from app.core.error_codes import (
    AgentError,
    AgentSpawnError,
    CommunicationError,
    ErrorCategory,
    ErrorClassification,
    ErrorCode,
    LLMError,
    MCPError,
    NexusError,
    NexusMemoryError,
    SandboxError,
    SwarmError,
    TaskDecompositionError,
    ToolExecutionError,
    ToolNotFoundError,
    classify_error,
    is_retryable_error,
)

__all__ = [
    "NexusError",
    "AgentError",
    "AgentSpawnError",
    "SwarmError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "SandboxError",
    "NexusMemoryError",
    "MCPError",
    "LLMError",
    "TaskDecompositionError",
    "CommunicationError",
    "ErrorCode",
    "ErrorCategory",
    "ErrorClassification",
    "classify_error",
    "is_retryable_error",
]
