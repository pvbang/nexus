"""HTTP reverse-proxy router for agent-hosted dev-server apps.

Mounts ``/preview/{port}/{path}`` on the main app and proxies to the loopback
dev-server port, so users reach agent-built apps through the project domain
(e.g. https://nexus.example.com/preview/19000/). Pure logic lives in
``preview_proxy``; this file is the thin async I/O layer.
"""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from app.api.preview_proxy import (
    PREVIEW_PORT_MAX,
    PREVIEW_PORT_MIN,
    build_upstream_url,
    inject_base_href,
    is_proxyable_port,
    rewrite_location_header,
)

logger = structlog.get_logger(__name__)

preview_router = APIRouter(tags=["Preview"])

# Hop-by-hop headers (RFC 7230) + ones httpx/we set. ALSO drop cookie/authorization:
# the previewed app is agent-built and not fully trusted — it must NOT receive the
# Nexus user's session cookie or auth token (would leak the session to that app).
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "keep-alive",
                         "transfer-encoding", "upgrade", "te", "trailer",
                         "cookie", "authorization"}
_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding",
                          "connection", "keep-alive"}

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@preview_router.api_route("/preview/{port}/{path:path}", methods=_METHODS)
@preview_router.api_route("/preview/{port}", methods=_METHODS)
async def preview_proxy(port: int, request: Request, path: str = "") -> Response:
    """Proxy a request to the agent's dev server on ``port`` (loopback only)."""
    if not is_proxyable_port(port):
        return Response(
            content=(
                f"Preview port {port} is not allowed. Only agent dev-server ports "
                f"{PREVIEW_PORT_MIN}-{PREVIEW_PORT_MAX} can be previewed."
            ),
            status_code=403,
            media_type="text/plain",
        )

    upstream = build_upstream_url(port, path, request.url.query)
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_REQUEST_HEADERS
    }
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            upstream_resp = await client.request(
                request.method, upstream, headers=fwd_headers, content=body,
            )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return Response(
            content=(
                f"No app is responding on preview port {port}. The dev server may have "
                "stopped, or the task that started it is no longer running."
            ),
            status_code=502,
            media_type="text/plain",
        )
    except httpx.HTTPError as e:
        logger.warning("preview_proxy_error", port=port, error=str(e)[:200])
        return Response(content=f"Preview proxy error: {str(e)[:200]}", status_code=502,
                        media_type="text/plain")

    # Build downstream headers (drop hop-by-hop; rewrite redirect Location).
    out_headers = {
        k: v for k, v in upstream_resp.headers.items()
        if k.lower() not in _DROP_RESPONSE_HEADERS
    }
    if "location" in upstream_resp.headers:
        out_headers["location"] = rewrite_location_header(
            upstream_resp.headers["location"], port
        )

    content = upstream_resp.content
    content_type = upstream_resp.headers.get("content-type", "")
    # Inject <base href> into HTML so SPA relative asset paths resolve under prefix.
    if "text/html" in content_type.lower():
        content = inject_base_href(content, port)

    return Response(
        content=content,
        status_code=upstream_resp.status_code,
        headers=out_headers,
        media_type=content_type or None,
    )
