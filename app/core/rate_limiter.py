"""
Nexus - API Rate Limiting Middleware (Pure ASGI).

Sliding-window rate limiting per IP using Redis.
Protects against abuse and ensures fair resource sharing.

IMPORTANT: Uses pure ASGI middleware instead of BaseHTTPMiddleware
to avoid buffering streaming responses (SSE, WebSocket).
BaseHTTPMiddleware reads the entire response body before sending,
which kills Server-Sent Events streaming.

Configurable via settings:
    RATE_LIMIT_RPM=60     # requests per minute per IP
    RATE_LIMIT_BURST=10   # burst allowance
"""

from typing import Optional

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger(__name__)

_SKIP_PATHS = frozenset({"/health", "/api/docs", "/api/redoc", "/openapi.json"})


class RateLimitMiddleware:
    """
    Pure ASGI rate limiter — does NOT buffer response bodies.

    This ensures SSE/EventSource streams flow through immediately,
    token by token, without waiting for the full response.

    Uses Redis sliding-window counter per client IP.
    Skips health check, static files, and WebSocket upgrades.
    """

    def __init__(
        self,
        app: ASGIApp,
        rpm: int = 120,
        burst: int = 20,
        enabled: bool = True,
    ):
        self.app = app
        self.rpm = rpm
        self.burst = burst
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only rate-limit HTTP requests
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Skip health/docs/static paths
        if path in _SKIP_PATHS or path.startswith("/assets/"):
            await self.app(scope, receive, send)
            return

        # Extract client IP
        client_ip = self._get_client_ip(scope)

        # Check rate limit
        allowed, remaining, reset_after = await self._check_rate_limit(client_ip)

        if not allowed:
            logger.warning("rate_limited", ip=client_ip, path=path)
            # Send 429 response directly
            body = (
                b'{"error":"Too Many Requests",'
                b'"message":"Rate limit exceeded. Max '
                + str(self.rpm).encode()
                + b' requests/minute.",'
                b'"retry_after":'
                + str(reset_after).encode()
                + b"}"
            )
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"retry-after", str(reset_after).encode()],
                    [b"x-ratelimit-limit", str(self.rpm).encode()],
                    [b"x-ratelimit-remaining", b"0"],
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
            return

        # Inject rate-limit headers into outgoing response
        original_send = send

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append([b"x-ratelimit-limit", str(self.rpm).encode()])
                headers.append([b"x-ratelimit-remaining", str(remaining).encode()])
                message = {**message, "headers": headers}
            await original_send(message)

        await self.app(scope, receive, send_with_headers)

    async def _check_rate_limit(self, client_ip: str) -> tuple[bool, int, int]:
        """Check if request is within rate limit.

        Returns (allowed, remaining, reset_after_seconds).
        """
        try:
            from app.core.dependencies import get_redis

            redis = await get_redis()
            key = f"nexus:ratelimit:{client_ip}"

            # Increment counter
            current = await redis.incr(key)

            # Set expiry on first request
            if current == 1:
                await redis.expire(key, 60)  # 1 minute window

            # Get TTL for reset_after
            ttl = await redis.ttl(key)
            reset_after = max(ttl, 1)

            # Allow burst + rpm
            max_requests = self.rpm + self.burst
            remaining = max(0, max_requests - current)
            allowed = current <= max_requests

            return allowed, remaining, reset_after

        except Exception as e:
            # If Redis is down, allow the request (fail-open)
            logger.debug("rate_limit_redis_error", error=str(e))
            return True, self.rpm, 60

    def _get_client_ip(self, scope: Scope) -> str:
        """Extract client IP from ASGI scope, respecting X-Forwarded-For."""
        headers = dict(scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"
