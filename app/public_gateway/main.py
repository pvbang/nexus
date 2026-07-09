"""
MekongAI Nexus - Public Gateway (separate port).

Why:
- In production, Nexus runs inside Docker and may expose many internal/admin endpoints.
- For real-world usage, we often only need a tiny "public entry" (e.g. tokenized inbox)
  without exposing the entire backend.

This app is intentionally small:
- GET /health
- GET/POST /inbox/{token}  -> proxies to backend /api/v1/public/inbox/{token}
"""

from __future__ import annotations

import os
from typing import Iterable

import structlog
from fastapi import FastAPI, HTTPException, Request, Response

logger = structlog.get_logger(__name__)


def _get_backend_base_url() -> str:
    # In Docker Compose, set PUBLIC_GATEWAY_BACKEND_URL=http://nexus:8000
    base = (os.environ.get("PUBLIC_GATEWAY_BACKEND_URL") or "").strip()
    if base:
        return base.rstrip("/")
    # Local fallback
    return "http://127.0.0.1:8000"


def _get_backend_prefix() -> str:
    # Backend API prefix (keep configurable for future routing changes).
    prefix = (os.environ.get("PUBLIC_GATEWAY_BACKEND_PREFIX") or "/api/v1").strip()
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def _hop_by_hop_headers() -> set[str]:
    # RFC 7230 hop-by-hop headers that should not be forwarded.
    return {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }


def _filter_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    deny = _hop_by_hop_headers()
    out: dict[str, str] = {}
    for k, v in headers:
        lk = (k or "").lower()
        if lk in deny:
            continue
        out[k] = v
    return out


app = FastAPI(title="MekongAI Nexus Public Gateway", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, object]:
    """Best-effort backend readiness probe."""
    try:
        import httpx

        base = _get_backend_base_url()
        async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
            r = await client.get(f"{base}/health")
        return {"ready": bool(r.status_code == 200)}
    except Exception as e:
        return {"ready": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


async def _proxy(request: Request, *, token: str) -> Response:
    base = _get_backend_base_url()
    prefix = _get_backend_prefix()
    target = f"{base}{prefix}/public/inbox/{token}"

    try:
        import httpx
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Missing dependency httpx: {e}")

    # Guard against accidental very large bodies (public endpoint).
    max_bytes = int(os.environ.get("PUBLIC_GATEWAY_MAX_BODY_BYTES", "300000") or "300000")
    body = await request.body()
    if body and len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="Request body too large")

    req_headers = _filter_headers(request.headers.items())

    proxy_timeout = float(os.environ.get("PUBLIC_GATEWAY_PROXY_TIMEOUT", "300"))
    try:
        async with httpx.AsyncClient(timeout=proxy_timeout, follow_redirects=True) as client:
            resp = await client.request(
                method=request.method,
                url=target,
                params=dict(request.query_params),
                headers=req_headers,
                content=body if body else None,
            )
    except httpx.RequestError as e:
        logger.warning("public_gateway_backend_unreachable", target=target, error=str(e)[:200])
        raise HTTPException(status_code=502, detail="Backend unreachable")
    except Exception as e:
        logger.error("public_gateway_proxy_failed", target=target, error=str(e)[:200])
        raise HTTPException(status_code=502, detail="Proxy failed")

    # Only forward a minimal safe subset of response headers.
    out_headers: dict[str, str] = {}
    ct = resp.headers.get("content-type")
    if ct:
        out_headers["content-type"] = ct
    cache = resp.headers.get("cache-control")
    if cache:
        out_headers["cache-control"] = cache

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=out_headers,
    )


@app.get("/inbox/{token}")
async def inbox_ui(token: str, request: Request) -> Response:
    _ = request
    return await _proxy(request, token=token)


@app.post("/inbox/{token}")
async def inbox_post(token: str, request: Request) -> Response:
    return await _proxy(request, token=token)

