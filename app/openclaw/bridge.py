"""
Nexus - OpenClaw Bridge Client.

Main HTTP/WS client for communicating with OpenClaw Gateway.
Handles: chat completions, tool invocation, message delivery, health monitoring.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Optional

import httpx
import structlog

from app.openclaw.config import OpenClawConfig, get_openclaw_config
from app.openclaw.models import (
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    GatewayStatus,
    OutboundResponse,
    ToolInvokeRequest,
    ToolInvokeResult,
)

logger = structlog.get_logger(__name__)


class OpenClawBridge:
    """
    HTTP client for OpenClaw Gateway communication.

    Provides:
    - Chat completions (send LLM responses via OpenClaw channels)
    - Tool invocation (delegate tool execution to OpenClaw)
    - Message delivery (proactive outbound messages)
    - Health monitoring (periodic gateway checks)
    - Automatic retries with exponential backoff

    Usage:
        bridge = get_openclaw_bridge()
        # Send a response via OpenClaw channel
        await bridge.send_chat(session_id="abc", messages=[...])
        # Invoke a tool on OpenClaw
        result = await bridge.invoke_tool("bash", {"command": "ls"})
        # Check health
        status = await bridge.check_health()
    """

    def __init__(self, config: Optional[OpenClawConfig] = None) -> None:
        self._config = config or get_openclaw_config()
        self._client: Optional[httpx.AsyncClient] = None
        self._status = GatewayStatus()
        self._health_task: Optional[asyncio.Task] = None
        self._stats = {
            "requests_sent": 0,
            "requests_failed": 0,
            "tools_invoked": 0,
            "messages_delivered": 0,
        }

    @property
    def is_healthy(self) -> bool:
        return self._status.healthy

    @property
    def status(self) -> GatewayStatus:
        return self._status

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize HTTP client and start health monitoring."""
        self._client = httpx.AsyncClient(
            base_url=self._config.gateway_url,
            timeout=httpx.Timeout(self._config.timeout),
            headers=self._build_headers(),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        # Initial health check
        await self.check_health()
        # Start periodic health monitoring
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("openclaw_bridge_started", url=self._config.gateway_url)

    async def stop(self) -> None:
        """Shutdown bridge client."""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("openclaw_bridge_stopped")

    def _build_headers(self) -> dict[str, str]:
        """Build default headers for OpenClaw requests."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "Nexus/1.0",
        }
        if self._config.gateway_token:
            headers["Authorization"] = f"Bearer {self._config.gateway_token}"
        return headers

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            await self.start()
        assert self._client is not None
        return self._client

    # ── Chat Completions ─────────────────────────────────────────

    async def send_chat(
        self,
        session_id: str,
        messages: list[dict[str, str]],
        model: str = "default",
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatCompletionResponse:
        """Send a chat completion request to OpenClaw Gateway.

        This is the primary way Nexus delivers responses to users:
        Nexus generates the response → sends to OpenClaw → OpenClaw delivers via channel.

        Args:
            session_id: OpenClaw session to respond to.
            messages: Chat messages (OpenAI format).
            model: Model override (or "default" for gateway config).
            stream: Whether to stream response.
            temperature: Temperature override.
            max_tokens: Max tokens override.

        Returns:
            ChatCompletionResponse from OpenClaw.
        """
        client = await self._ensure_client()

        request = ChatCompletionRequest(
            model=model,
            messages=[ChatCompletionMessage(**m) for m in messages],
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            session_key=session_id,
        )

        response = await self._request_with_retry(
            "POST", "/v1/chat/completions",
            json=request.model_dump(exclude_none=True),
        )

        if response.status_code == 200:
            data = response.json()
            self._stats["messages_delivered"] += 1
            return ChatCompletionResponse(**data)
        else:
            logger.warning("openclaw_chat_failed",
                           status=response.status_code,
                           body=response.text[:200])
            return ChatCompletionResponse()

    async def send_chat_stream(
        self,
        session_id: str,
        messages: list[dict[str, str]],
        model: str = "default",
    ) -> AsyncIterator[str]:
        """Stream chat completion response from OpenClaw.

        Yields SSE delta text chunks.
        """
        client = await self._ensure_client()

        request = ChatCompletionRequest(
            model=model,
            messages=[ChatCompletionMessage(**m) for m in messages],
            stream=True,
            session_key=session_id,
        )

        async with client.stream(
            "POST", "/v1/chat/completions",
            json=request.model_dump(exclude_none=True),
            timeout=self._config.stream_timeout,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    yield data

    # ── Tool Invocation ──────────────────────────────────────────

    async def invoke_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        session_id: str = "",
        timeout: int = 60,
        sandbox: bool = False,
    ) -> ToolInvokeResult:
        """Invoke a tool on OpenClaw Gateway.

        Delegates tool execution to OpenClaw, which has 70+ built-in tools
        and sandbox capabilities.

        Args:
            tool_name: Name of the tool to invoke.
            args: Tool arguments.
            session_id: Session context for the tool.
            timeout: Execution timeout.
            sandbox: Force sandbox execution.

        Returns:
            ToolInvokeResult with output/error.
        """
        client = await self._ensure_client()

        request = ToolInvokeRequest(
            tool_name=tool_name,
            args=args,
            session_id=session_id,
            timeout=timeout,
            sandbox=sandbox,
        )

        try:
            response = await self._request_with_retry(
                "POST", "/tools/invoke",
                json=request.model_dump(),
                timeout=timeout + 10,  # Extra buffer for network
            )

            self._stats["tools_invoked"] += 1

            if response.status_code == 200:
                data = response.json()
                return ToolInvokeResult(
                    success=data.get("success", True),
                    output=data.get("output", ""),
                    error=data.get("error", ""),
                    exit_code=data.get("exit_code", 0),
                    execution_time_ms=data.get("execution_time_ms", 0),
                    metadata=data.get("metadata", {}),
                )
            else:
                return ToolInvokeResult(
                    success=False,
                    error=f"OpenClaw returned {response.status_code}: {response.text[:200]}",
                    exit_code=response.status_code,
                )

        except httpx.TimeoutException:
            return ToolInvokeResult(
                success=False,
                error=f"Tool invocation timed out after {timeout}s",
                exit_code=-1,
            )
        except Exception as exc:
            return ToolInvokeResult(
                success=False,
                error=f"Bridge error: {exc}",
                exit_code=-1,
            )

    # ── Message Delivery ─────────────────────────────────────────

    async def send_message(
        self,
        channel: str,
        target: str,
        content: str,
        session_id: str = "",
        reply_to: Optional[str] = None,
    ) -> bool:
        """Send a proactive message via OpenClaw channel.

        Used for: notifications, scheduled reports, alerts.

        Args:
            channel: Target channel (telegram, discord, slack, etc.)
            target: Target user/chat ID.
            content: Message content.
            session_id: Optional session context.
            reply_to: Optional message to reply to.

        Returns:
            True if delivered successfully.
        """
        messages = [
            {"role": "assistant", "content": content}
        ]

        # Use chat completions with session targeting
        response = await self.send_chat(
            session_id=session_id or f"{channel}:{target}",
            messages=messages,
        )

        success = bool(response.content or response.choices)
        if success:
            self._stats["messages_delivered"] += 1
        return success

    # ── Health Monitoring ────────────────────────────────────────

    async def check_health(self) -> GatewayStatus:
        """Check OpenClaw Gateway health."""
        try:
            client = await self._ensure_client()
            response = await client.get("/healthz", timeout=5.0)

            if response.status_code == 200:
                data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                self._status = GatewayStatus(
                    healthy=True,
                    version=data.get("version", ""),
                    uptime_s=data.get("uptime_s", 0),
                    channels_active=data.get("channels", []),
                    sessions_count=data.get("sessions", 0),
                    last_check_at=time.time(),
                )
            else:
                self._status.healthy = False
                self._status.last_check_at = time.time()

        except Exception as exc:
            self._status.healthy = False
            self._status.last_check_at = time.time()
            # Rate-limit: only log once per 60s to avoid log spam from parallel callers
            _now = time.time()
            if not hasattr(self, '_last_health_log') or _now - self._last_health_log > 60:
                self._last_health_log = _now
                logger.debug("openclaw_health_check_failed", error=str(exc))

        return self._status

    async def _health_loop(self) -> None:
        """Periodic health check loop with exponential backoff on failures."""
        consecutive_failures = 0
        max_interval = 300.0  # Cap at 5 minutes between checks
        while True:
            try:
                # Exponential backoff: 30s → 60s → 120s → 300s (cap)
                if consecutive_failures > 0:
                    interval = min(
                        self._config.health_interval * (2 ** consecutive_failures),
                        max_interval,
                    )
                else:
                    interval = self._config.health_interval
                await asyncio.sleep(interval)
                status = await self.check_health()
                if status.healthy:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_failures += 1

    # ── HTTP Helpers ─────────────────────────────────────────────

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        """Make HTTP request with retry and exponential backoff."""
        client = await self._ensure_client()
        self._stats["requests_sent"] += 1

        # Propagate the current trace across the service boundary (audit C4), so
        # OpenClaw can log under the same trace_id. Empty when no trace is bound.
        from app.core.trace_context import trace_headers
        _trace_headers = trace_headers() or None

        last_exc: Optional[Exception] = None
        for attempt in range(self._config.max_retries):
            try:
                response = await client.request(
                    method, path, json=json,
                    timeout=timeout or self._config.timeout,
                    headers=_trace_headers,
                )
                # Don't retry on 4xx client errors
                if response.status_code < 500:
                    return response
                # Retry on 5xx
                last_exc = httpx.HTTPStatusError(
                    f"Server error: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc

            # Exponential backoff
            if attempt < self._config.max_retries - 1:
                delay = self._config.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        self._stats["requests_failed"] += 1
        if last_exc:
            raise last_exc
        raise httpx.ConnectError("Max retries exceeded")

    # ── Introspection ────────────────────────────────────────────

    def get_info(self) -> dict[str, Any]:
        """Get bridge status information."""
        return {
            "gateway_url": self._config.gateway_url,
            "healthy": self._status.healthy,
            "version": self._status.version,
            "channels": self._status.channels_active,
            "stats": self._stats,
            "last_health_check": self._status.last_check_at,
        }


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_bridge: Optional[OpenClawBridge] = None


def get_openclaw_bridge() -> OpenClawBridge:
    """Get the singleton OpenClawBridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = OpenClawBridge()
    return _bridge
