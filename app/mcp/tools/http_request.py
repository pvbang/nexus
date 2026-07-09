"""
MekongAI Nexus - HTTP Request Tool.

Allows agents to make arbitrary HTTP requests (GET, POST, PUT, DELETE, PATCH)
to APIs. This is CRITICAL for calling REST APIs directly (GitHub API, etc.)
instead of scraping HTML web pages.

Inspired by: hermes-agent web_extract + direct API patterns.

Usage::

    # Auto-registered tool:
    #   http_request — make HTTP requests to APIs and return JSON/text
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
import structlog

from app.core.command_guard import check_url_safety
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# Shared HTTP client (lazy)
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


# ------------------------------------------------------------------
# Handler
# ------------------------------------------------------------------


async def handle_http_request(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Make an HTTP request to any API endpoint.

    Supports GET, POST, PUT, DELETE, PATCH methods.
    Automatically parses JSON responses. Handles both API and HTML responses.
    Includes SSRF protection.
    """
    url = params.get("url", "")
    method = params.get("method", "GET").upper()
    headers_param = params.get("headers", {})
    body = params.get("body", None)
    timeout = params.get("timeout", 30)

    if not url:
        return {"success": False, "output": "No URL provided", "data": None, "artifacts": []}

    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        return {"success": False, "output": f"Unsupported method: {method}", "data": None, "artifacts": []}

    # Security: SSRF protection
    url_verdict = check_url_safety(url)
    if not url_verdict.safe:
        logger.warning("ssrf_blocked", url=url[:200], reason=url_verdict.reason)
        return {
            "success": False,
            "output": f"⚠️ URL blocked (SSRF protection): {url_verdict.reason}",
            "data": {"blocked": True, "reason": url_verdict.reason},
            "artifacts": [],
        }

    try:
        http = await _get_http()

        # Build request kwargs
        req_kwargs: dict[str, Any] = {
            "headers": {
                "User-Agent": "MekongAI-Nexus/1.0",
                "Accept": "application/json, text/plain, text/html, */*",
            },
            "timeout": timeout,
        }

        # Merge custom headers
        if isinstance(headers_param, dict):
            req_kwargs["headers"].update(headers_param)

        # Add body for methods that support it
        if body is not None and method in ("POST", "PUT", "PATCH"):
            if isinstance(body, dict):
                req_kwargs["json"] = body
            elif isinstance(body, str):
                req_kwargs["content"] = body
                if "Content-Type" not in req_kwargs["headers"]:
                    req_kwargs["headers"]["Content-Type"] = "application/json"

        # Make request
        resp = await http.request(method, url, **req_kwargs)

        # Parse response
        content_type = resp.headers.get("content-type", "")
        status_code = resp.status_code
        response_text = resp.text[:50_000]  # Limit size

        # Try JSON parse
        response_data = None
        output = ""
        if "json" in content_type or response_text.strip().startswith(("{", "[")):
            try:
                response_data = resp.json()
                # Format JSON nicely but limit size
                output = json.dumps(response_data, indent=2, ensure_ascii=False)
                if len(output) > 15_000:
                    output = output[:15_000] + "\n... (truncated)"
            except (json.JSONDecodeError, ValueError):
                output = response_text[:10_000]
        elif "html" in content_type:
            # Strip HTML for readability
            clean = re.sub(r'<script.*?</script>', '', response_text, flags=re.DOTALL)
            clean = re.sub(r'<style.*?</style>', '', clean, flags=re.DOTALL)
            clean = re.sub(r'<.*?>', ' ', clean)
            clean = re.sub(r'\s+', ' ', clean).strip()
            output = clean[:10_000]
        else:
            output = response_text[:10_000]

        success = 200 <= status_code < 400

        return {
            "success": success,
            "output": f"HTTP {method} {url} → {status_code}\n\n{output}",
            "data": {
                "url": url,
                "method": method,
                "status_code": status_code,
                "content_type": content_type,
                "response_length": len(response_text),
                "json_data": response_data,
            },
            "artifacts": [],
        }
    except httpx.TimeoutException:
        return {
            "success": False,
            "output": f"Request timed out after {timeout}s: {url}",
            "data": None,
            "artifacts": [],
        }
    except Exception as e:
        logger.error("http_request_error", url=url[:200], method=method, error=str(e))
        return {
            "success": False,
            "output": f"HTTP request failed: {e}",
            "data": None,
            "artifacts": [],
        }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "web"

registry.register(
    name="http_request",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Make an HTTP request to any URL/API endpoint. "
            "Supports GET, POST, PUT, DELETE, PATCH methods. "
            "Use this for calling REST APIs (GitHub API, weather APIs, etc.) "
            "instead of scraping HTML pages. Returns JSON when available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to request (e.g., https://api.github.com/repos/trending)",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                    "description": "HTTP method (default: GET)",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers as key-value pairs",
                },
                "body": {
                    "description": "Request body (for POST/PUT/PATCH). Can be JSON object or string.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                },
            },
            "required": ["url"],
        },
    },
    handler=handle_http_request,
    is_async=True,
    emoji="🌐",
)
