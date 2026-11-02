"""
Nexus - API Testing Tools.

Provides tools for testing API endpoints, running sequential test flows,
and starting mock HTTP servers for integration testing.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

_VAR_PATTERN = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")

# ---------------------------------------------------------------------------
# Mock server state
# ---------------------------------------------------------------------------

_mock_servers: dict[int, asyncio.AbstractServer] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_dot_path(obj: Any, path: str) -> Any:
    """Resolve a dot-notation path against a dict/nested structure."""
    parts = path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _substitute_vars(text: str, variables: dict[str, Any]) -> str:
    """Replace {{var}} placeholders in text using variables dict."""
    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        val = _resolve_dot_path(variables, key)
        return str(val) if val is not None else m.group(0)

    return _VAR_PATTERN.sub(_replacer, text)


# ---------------------------------------------------------------------------
# Handler: api_test_endpoint
# ---------------------------------------------------------------------------


async def _handle_api_test_endpoint(arguments: dict) -> str:
    """Test a single API endpoint with optional assertions."""
    try:
        import aiohttp
    except ImportError:
        return tool_error("aiohttp is required: pip install aiohttp")

    url = arguments.get("url", "").strip()
    if not url:
        return tool_error("url is required")

    method = arguments.get("method", "GET").upper()
    headers = arguments.get("headers") or {}
    body = arguments.get("body")
    timeout_s = arguments.get("timeout", 90)
    assertions = arguments.get("assertions") or {}

    start = time.time()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            kwargs: dict[str, Any] = {"headers": headers}
            if body:
                kwargs["data"] = body
                if "content-type" not in {k.lower() for k in headers}:
                    kwargs["headers"]["Content-Type"] = "application/json"

            async with session.request(method, url, **kwargs) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                resp_body = await resp.text()
                elapsed_ms = round((time.time() - start) * 1000, 1)
    except asyncio.TimeoutError:
        elapsed_ms = round((time.time() - start) * 1000, 1)
        return tool_result(
            success=False,
            output=f"{method} {url} → TIMEOUT ({elapsed_ms}ms)",
        )
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000, 1)
        return tool_result(
            success=False,
            output=f"{method} {url} → ERROR: {type(e).__name__}: {e} ({elapsed_ms}ms)",
        )

    # Check assertions
    assertion_results: list[str] = []
    all_passed = True

    if "status" in assertions:
        expected_status = assertions["status"]
        if status == expected_status:
            assertion_results.append(f"status={status} ✓")
        else:
            assertion_results.append(f"status={status} ✗ (expected {expected_status})")
            all_passed = False

    if "body_contains" in assertions:
        needle = assertions["body_contains"]
        if needle in resp_body:
            assertion_results.append(f"body_contains ✓")
        else:
            assertion_results.append(f"body_contains ✗ ('{needle}' not found)")
            all_passed = False

    if "headers_contain" in assertions:
        for hk, hv in assertions["headers_contain"].items():
            actual = resp_headers.get(hk, "")
            if hv.lower() in actual.lower():
                assertion_results.append(f"header[{hk}] ✓")
            else:
                assertion_results.append(f"header[{hk}] ✗ (got '{actual}')")
                all_passed = False

    assertions_str = ""
    if assertion_results:
        assertions_str = f" | assertions: {', '.join(assertion_results)}"

    output = f"{method} {url} → {status} ({elapsed_ms}ms){assertions_str}"

    return tool_result(
        success=all_passed,
        output=f"🧪 {output}",
        data={"status": status, "elapsed_ms": elapsed_ms, "passed": all_passed},
    )


# ---------------------------------------------------------------------------
# Handler: api_test_sequence
# ---------------------------------------------------------------------------


async def _handle_api_test_sequence(arguments: dict) -> str:
    """Run an ordered sequence of API calls with variable extraction."""
    try:
        import aiohttp
    except ImportError:
        return tool_error("aiohttp is required: pip install aiohttp")

    steps = arguments.get("steps", [])
    if not steps:
        return tool_error("steps array is required")

    base_url = arguments.get("base_url", "").rstrip("/")
    stop_on_fail = arguments.get("stop_on_fail", True)

    variables: dict[str, Any] = {}
    results: list[str] = []
    all_passed = True

    async with aiohttp.ClientSession() as session:
        for step in steps:
            name = step.get("name", "unnamed")
            raw_url = step.get("url", "")
            method = step.get("method", "GET").upper()
            headers = step.get("headers") or {}
            body = step.get("body")
            extract = step.get("extract") or {}

            # Build URL
            url = _substitute_vars(raw_url, variables)
            if base_url and not url.startswith("http"):
                url = f"{base_url}/{url.lstrip('/')}"

            # Substitute variables in body
            if body:
                body = _substitute_vars(body, variables)

            # Substitute variables in headers
            headers = {k: _substitute_vars(v, variables) for k, v in headers.items()}

            start = time.time()
            try:
                kwargs: dict[str, Any] = {"headers": headers}
                if body:
                    kwargs["data"] = body
                    if "content-type" not in {k.lower() for k in headers}:
                        kwargs["headers"]["Content-Type"] = "application/json"

                async with session.request(method, url, **kwargs) as resp:
                    status = resp.status
                    resp_body = await resp.text()
                    elapsed_ms = round((time.time() - start) * 1000, 1)

                # Try parse JSON for extraction
                resp_json: Any = None
                if extract:
                    try:
                        resp_json = json.loads(resp_body)
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Extract values
                if resp_json and extract:
                    for var_name, json_path in extract.items():
                        val = _resolve_dot_path(resp_json, json_path)
                        if val is not None:
                            variables[var_name] = val

                passed = 200 <= status < 400
                marker = "✓" if passed else "✗"
                if not passed:
                    all_passed = False
                results.append(f"[{marker}] {name} → {status} ({elapsed_ms}ms)")

                if not passed and stop_on_fail:
                    results.append(f"[—] Stopped: {name} failed")
                    break

            except Exception as e:
                elapsed_ms = round((time.time() - start) * 1000, 1)
                results.append(f"[✗] {name} → ERROR: {type(e).__name__} ({elapsed_ms}ms)")
                all_passed = False
                if stop_on_fail:
                    results.append(f"[—] Stopped: {name} errored")
                    break

    output = f"🧪 Sequence ({len(results)} steps):\n" + "\n".join(results)
    return tool_result(success=all_passed, output=output)


# ---------------------------------------------------------------------------
# Handler: api_mock_server
# ---------------------------------------------------------------------------


async def _handle_api_mock_server(arguments: dict) -> str:
    """Start, stop, or check status of a mock HTTP server."""
    try:
        from aiohttp import web
    except ImportError:
        return tool_error("aiohttp is required: pip install aiohttp")

    action = arguments.get("action", "").strip().lower()
    port = arguments.get("port", 8765)

    if action == "start":
        if port in _mock_servers:
            return tool_result(
                success=False,
                output=f"🎭 Mock server already running on port {port}",
            )

        routes_def = arguments.get("routes") or []
        app = web.Application()

        for route in routes_def:
            path = route.get("path", "/")
            r_method = route.get("method", "GET").upper()
            r_status = route.get("status", 200)
            r_body = route.get("body", "")
            r_headers = route.get("headers") or {}

            # Create handler closure
            def _make_handler(
                resp_status: int, resp_body: str, resp_headers: dict
            ):
                async def _handler(request: web.Request) -> web.Response:
                    return web.Response(
                        status=resp_status,
                        text=resp_body,
                        headers=resp_headers,
                    )
                return _handler

            handler = _make_handler(r_status, r_body, r_headers)

            method_map = {
                "GET": app.router.add_get,
                "POST": app.router.add_post,
                "PUT": app.router.add_put,
                "DELETE": app.router.add_delete,
                "PATCH": app.router.add_patch,
            }
            adder = method_map.get(r_method, app.router.add_get)
            adder(path, handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        try:
            await site.start()
        except OSError as e:
            await runner.cleanup()
            return tool_result(
                success=False,
                output=f"🎭 Failed to start mock server on port {port}: {e}",
            )

        _mock_servers[port] = runner  # type: ignore[assignment]
        route_count = len(routes_def)
        return tool_result(
            success=True,
            output=f"🎭 Mock server started on port {port} with {route_count} routes",
            data={"port": port, "routes": route_count},
        )

    elif action == "stop":
        runner = _mock_servers.pop(port, None)
        if not runner:
            return tool_result(
                success=False,
                output=f"🎭 No mock server running on port {port}",
            )
        await runner.cleanup()
        return tool_result(
            success=True,
            output=f"🎭 Mock server on port {port} stopped",
        )

    elif action == "status":
        if port in _mock_servers:
            return tool_result(
                success=True,
                output=f"🎭 Mock server on port {port}: running",
            )
        else:
            return tool_result(
                success=True,
                output=f"🎭 Mock server on port {port}: not running",
            )

    else:
        return tool_error(f"Unknown action '{action}'. Use: start, stop, status")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="api_test_endpoint",
    toolset="api_testing",
    schema={
        "description": (
            "Test a single API endpoint with optional assertions on status code, "
            "response body, and headers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Endpoint URL to test"},
                "method": {
                    "type": "string",
                    "description": "HTTP method (default: GET)",
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "Request headers",
                },
                "body": {
                    "type": "string",
                    "description": "Request body (JSON string)",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30,
                },
                "assertions": {
                    "type": "object",
                    "description": "Assertions to check against response",
                    "properties": {
                        "status": {"type": "number", "description": "Expected status code"},
                        "body_contains": {"type": "string", "description": "Body should contain this string"},
                        "headers_contain": {"type": "object", "description": "Expected header key-value pairs"},
                    },
                },
            },
            "required": ["url"],
        },
    },
    handler=_handle_api_test_endpoint,
    is_async=True,
    emoji="🧪",
    category="testing",
)

registry.register(
    name="api_test_sequence",
    toolset="api_testing",
    schema={
        "description": (
            "Run an ordered sequence of API calls (e.g. login → create → verify). "
            "Supports variable extraction between steps using {{var}} templates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered array of API call steps",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Step name"},
                            "url": {"type": "string", "description": "Endpoint URL"},
                            "method": {"type": "string", "description": "HTTP method (default: GET)"},
                            "headers": {"type": "object", "description": "Request headers"},
                            "body": {"type": "string", "description": "Request body, supports {{var}} templates"},
                            "extract": {
                                "type": "object",
                                "description": "Key-value pairs: variable_name → dot.path to extract from response JSON",
                            },
                        },
                        "required": ["name", "url"],
                    },
                },
                "base_url": {
                    "type": "string",
                    "description": "Base URL prefix for all step URLs",
                },
                "stop_on_fail": {
                    "type": "boolean",
                    "description": "Stop sequence on first failure (default: true)",
                    "default": True,
                },
            },
            "required": ["steps"],
        },
    },
    handler=_handle_api_test_sequence,
    is_async=True,
    emoji="🔗",
    category="testing",
)

registry.register(
    name="api_mock_server",
    toolset="api_testing",
    schema={
        "description": (
            "Start, stop, or check status of a mock HTTP server for testing. "
            "Define routes with static responses."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "status"],
                    "description": "Action to perform",
                },
                "port": {
                    "type": "number",
                    "description": "Port number (default: 8765)",
                    "default": 8765,
                },
                "routes": {
                    "type": "array",
                    "description": "Route definitions (for 'start' action)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "URL path"},
                            "method": {"type": "string", "description": "HTTP method (default: GET)"},
                            "status": {"type": "number", "description": "Response status code (default: 200)"},
                            "body": {"type": "string", "description": "Response body"},
                            "headers": {"type": "object", "description": "Response headers"},
                        },
                        "required": ["path"],
                    },
                },
            },
            "required": ["action"],
        },
    },
    handler=_handle_api_mock_server,
    is_async=True,
    emoji="🎭",
    category="testing",
)
