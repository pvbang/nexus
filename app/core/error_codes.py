"""
MekongAI Nexus - Error Codes & Classification System.

Inspired by Shannon's ErrorCode enum + classifyErrorForTemporal pattern.
Two-tier classification:
1. Code-based (preferred) — for internal errors with known ErrorCode
2. String-based (fallback) — for external errors from LLM APIs, network, etc.

Each error is classified as retryable or permanent to drive retry decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    """
    Specific error codes for reliable classification.

    Grouped by category for clarity. Used by classify_error() for
    code-based classification (preferred path).
    """

    # --- LLM Provider Errors (typically retryable) ---
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_OVERLOADED = "LLM_OVERLOADED"
    LLM_CONTEXT_OVERFLOW = "LLM_CONTEXT_OVERFLOW"
    LLM_CONTENT_FILTERED = "LLM_CONTENT_FILTERED"
    LLM_SPENDING_CAP = "LLM_SPENDING_CAP"

    # --- Authentication/Permission Errors (permanent) ---
    AUTH_INVALID_KEY = "AUTH_INVALID_KEY"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_PERMISSION_DENIED = "AUTH_PERMISSION_DENIED"

    # --- Configuration Errors (permanent) ---
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISSING = "CONFIG_MISSING"
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"

    # --- Execution Errors (may retry) ---
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    SWARM_EXECUTION_FAILED = "SWARM_EXECUTION_FAILED"
    TASK_TIMEOUT = "TASK_TIMEOUT"
    TASK_CANCELLED = "TASK_CANCELLED"

    # --- Resource Errors (may retry) ---
    MEMORY_LIMIT = "MEMORY_LIMIT"
    DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
    DB_QUERY_FAILED = "DB_QUERY_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"

    # --- Validation Errors (permanent) ---
    INPUT_VALIDATION_FAILED = "INPUT_VALIDATION_FAILED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"

    # --- Communication Errors (retryable) ---
    CHANNEL_SEND_FAILED = "CHANNEL_SEND_FAILED"
    WEBHOOK_DELIVERY_FAILED = "WEBHOOK_DELIVERY_FAILED"
    MCP_CONNECTION_FAILED = "MCP_CONNECTION_FAILED"


class ErrorCategory(str, Enum):
    """Coarse error categories for grouping."""

    LLM = "llm"
    AUTH = "auth"
    CONFIG = "config"
    EXECUTION = "execution"
    RESOURCE = "resource"
    VALIDATION = "validation"
    COMMUNICATION = "communication"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorClassification:
    """Result of error classification — drives retry behavior."""

    error_type: str
    retryable: bool
    category: ErrorCategory
    code: Optional[ErrorCode] = None
    context: dict[str, Any] = field(default_factory=dict)


# --- Code-Based Classification (Priority 1) ---

_CODE_CLASSIFICATION: dict[ErrorCode, tuple[str, bool, ErrorCategory]] = {
    # LLM errors (retryable — wait for rate limit reset, retry on timeout)
    ErrorCode.LLM_RATE_LIMITED: ("RateLimitError", True, ErrorCategory.LLM),
    ErrorCode.LLM_TIMEOUT: ("TimeoutError", True, ErrorCategory.LLM),
    ErrorCode.LLM_OVERLOADED: ("OverloadedError", True, ErrorCategory.LLM),
    ErrorCode.LLM_CONTEXT_OVERFLOW: ("ContextOverflowError", False, ErrorCategory.LLM),
    ErrorCode.LLM_CONTENT_FILTERED: ("ContentFilteredError", False, ErrorCategory.LLM),
    ErrorCode.LLM_SPENDING_CAP: ("SpendingCapError", True, ErrorCategory.LLM),
    # Auth errors (permanent — bad key won't fix itself)
    ErrorCode.AUTH_INVALID_KEY: ("AuthenticationError", False, ErrorCategory.AUTH),
    ErrorCode.AUTH_EXPIRED: ("AuthenticationError", False, ErrorCategory.AUTH),
    ErrorCode.AUTH_PERMISSION_DENIED: ("PermissionError", False, ErrorCategory.AUTH),
    # Config errors (permanent — need manual fix)
    ErrorCode.CONFIG_INVALID: ("ConfigurationError", False, ErrorCategory.CONFIG),
    ErrorCode.CONFIG_MISSING: ("ConfigurationError", False, ErrorCategory.CONFIG),
    ErrorCode.AGENT_NOT_FOUND: ("ConfigurationError", False, ErrorCategory.CONFIG),
    ErrorCode.TOOL_NOT_FOUND: ("ConfigurationError", False, ErrorCategory.CONFIG),
    ErrorCode.SKILL_NOT_FOUND: ("ConfigurationError", False, ErrorCategory.CONFIG),
    # Execution errors (retryable by default)
    ErrorCode.TOOL_EXECUTION_FAILED: ("ToolExecutionError", True, ErrorCategory.EXECUTION),
    ErrorCode.TOOL_TIMEOUT: ("ToolTimeoutError", True, ErrorCategory.EXECUTION),
    ErrorCode.AGENT_EXECUTION_FAILED: ("AgentExecutionError", True, ErrorCategory.EXECUTION),
    ErrorCode.SWARM_EXECUTION_FAILED: ("SwarmExecutionError", True, ErrorCategory.EXECUTION),
    ErrorCode.TASK_TIMEOUT: ("TaskTimeoutError", True, ErrorCategory.EXECUTION),
    ErrorCode.TASK_CANCELLED: ("TaskCancelledError", False, ErrorCategory.EXECUTION),
    # Resource errors (retryable)
    ErrorCode.MEMORY_LIMIT: ("MemoryLimitError", True, ErrorCategory.RESOURCE),
    ErrorCode.DB_CONNECTION_FAILED: ("DatabaseError", True, ErrorCategory.RESOURCE),
    ErrorCode.DB_QUERY_FAILED: ("DatabaseError", True, ErrorCategory.RESOURCE),
    ErrorCode.NETWORK_ERROR: ("NetworkError", True, ErrorCategory.RESOURCE),
    ErrorCode.FILE_NOT_FOUND: ("FileNotFoundError", False, ErrorCategory.RESOURCE),
    # Validation errors (permanent)
    ErrorCode.INPUT_VALIDATION_FAILED: ("ValidationError", False, ErrorCategory.VALIDATION),
    ErrorCode.OUTPUT_VALIDATION_FAILED: ("ValidationError", True, ErrorCategory.VALIDATION),
    ErrorCode.SCHEMA_MISMATCH: ("ValidationError", False, ErrorCategory.VALIDATION),
    # Communication errors (retryable)
    ErrorCode.CHANNEL_SEND_FAILED: ("CommunicationError", True, ErrorCategory.COMMUNICATION),
    ErrorCode.WEBHOOK_DELIVERY_FAILED: ("CommunicationError", True, ErrorCategory.COMMUNICATION),
    ErrorCode.MCP_CONNECTION_FAILED: ("CommunicationError", True, ErrorCategory.COMMUNICATION),
}


# --- String-Based Classification Patterns (Priority 2 — fallback) ---

_RETRYABLE_PATTERNS: list[str] = [
    # Network/connection
    "network",
    "connection",
    "timeout",
    "econnreset",
    "econnrefused",
    # Rate limiting
    "rate limit",
    "rate_limit",
    "429",
    "too many requests",
    # Server errors
    "server error",
    "5xx",
    "internal server error",
    "service unavailable",
    "bad gateway",
    "502",
    "503",
    # LLM API transient errors
    "overloaded",
    "temporarily unavailable",
    "model unavailable",
    "api error",
]

_NON_RETRYABLE_PATTERNS: list[str] = [
    # Auth
    "authentication",
    "invalid api key",
    "invalid_api_key",
    "unauthorized",
    "401",
    # Permanent
    "permission denied",
    "forbidden",
    "403",
    "invalid request",
    "invalid_request",
    "malformed",
    "not found",
    # Validation
    "validation error",
    "schema",
    # Limits (permanent for this request)
    "context_length_exceeded",
    "context length",
    "content_filter",
]


class NexusError(Exception):
    """
    Base exception for MekongAI Nexus with structured error code.

    Enhanced from the original NexusError to include ErrorCode for
    reliable classification.
    """

    def __init__(
        self,
        message: str,
        code: Optional[ErrorCode] = None,
        detail: Any = None,
        retryable: Optional[bool] = None,
        context: Optional[dict[str, Any]] = None,
    ):
        self.message = message
        self.code = code
        self.detail = detail
        self._retryable = retryable
        self.context = context or {}
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        """Determine if this error is retryable."""
        if self._retryable is not None:
            return self._retryable
        if self.code:
            classification = _CODE_CLASSIFICATION.get(self.code)
            if classification:
                return classification[1]
        return False


# --- Convenience subclasses (backward-compatible with existing codebase) ---


class AgentError(NexusError):
    """Error related to agent operations."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.AGENT_EXECUTION_FAILED, **kwargs)


class ToolNotFoundError(NexusError):
    """Required tool not found in registry."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.TOOL_NOT_FOUND, **kwargs)


class ToolExecutionError(NexusError):
    """Tool execution failed."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.TOOL_EXECUTION_FAILED, **kwargs)


class LLMError(NexusError):
    """LLM provider error."""

    def __init__(self, message: str, code: Optional[ErrorCode] = None, **kwargs: Any):
        super().__init__(message, code=code or ErrorCode.LLM_TIMEOUT, **kwargs)


class SwarmError(NexusError):
    """Error in swarm execution."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.SWARM_EXECUTION_FAILED, **kwargs)


class TaskDecompositionError(NexusError):
    """Failed to decompose a task."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.AGENT_EXECUTION_FAILED, **kwargs)


class CommunicationError(NexusError):
    """Inter-agent communication error."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.CHANNEL_SEND_FAILED, **kwargs)


class MCPError(NexusError):
    """MCP protocol error."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.MCP_CONNECTION_FAILED, **kwargs)


class SandboxError(NexusError):
    """Sandbox execution error."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.TOOL_EXECUTION_FAILED, **kwargs)


class NexusMemoryError(NexusError):
    """Memory/GraphRAG operation error."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, code=ErrorCode.DB_QUERY_FAILED, **kwargs)


class AgentSpawnError(AgentError):
    """Failed to spawn a new agent."""
    pass


# --- Main Classification Function ---


def classify_error(error: Exception) -> ErrorClassification:
    """
    Classify an error for retry behavior decisions.

    Classification priority:
    1. If error is NexusError with ErrorCode → classify by code (reliable)
    2. Fall through to string matching for external errors (SDK, network, etc.)

    Returns ErrorClassification with error_type, retryable flag, and category.
    """
    # === CODE-BASED CLASSIFICATION (Preferred for internal errors) ===
    if isinstance(error, NexusError) and error.code is not None:
        classification = _CODE_CLASSIFICATION.get(error.code)
        if classification:
            error_type, retryable, category = classification
            # Allow override from error instance
            if error._retryable is not None:
                retryable = error._retryable
            return ErrorClassification(
                error_type=error_type,
                retryable=retryable,
                category=category,
                code=error.code,
                context=error.context,
            )

    # === STRING-BASED CLASSIFICATION (Fallback for external errors) ===
    message = str(error).lower()

    # 1. Check non-retryable patterns first (more specific)
    for pattern in _NON_RETRYABLE_PATTERNS:
        if pattern in message:
            return ErrorClassification(
                error_type="PermanentError",
                retryable=False,
                category=_infer_category_from_message(message),
            )

    # 2. Check retryable patterns
    for pattern in _RETRYABLE_PATTERNS:
        if pattern in message:
            return ErrorClassification(
                error_type="TransientError",
                retryable=True,
                category=_infer_category_from_message(message),
            )

    # 3. Default: unknown errors are NOT retryable (fail-safe)
    return ErrorClassification(
        error_type="UnknownError",
        retryable=False,
        category=ErrorCategory.UNKNOWN,
    )


def is_retryable_error(error: Exception) -> bool:
    """
    Quick check: is this error retryable?

    Convenience wrapper over classify_error for simple retry decisions.
    """
    return classify_error(error).retryable


def _infer_category_from_message(message: str) -> ErrorCategory:
    """Infer error category from message content."""
    if any(p in message for p in ["rate limit", "429", "overloaded", "model"]):
        return ErrorCategory.LLM
    if any(p in message for p in ["auth", "key", "401", "403", "permission"]):
        return ErrorCategory.AUTH
    if any(p in message for p in ["network", "connection", "timeout", "502", "503"]):
        return ErrorCategory.RESOURCE
    if any(p in message for p in ["validation", "schema", "invalid"]):
        return ErrorCategory.VALIDATION
    return ErrorCategory.UNKNOWN
