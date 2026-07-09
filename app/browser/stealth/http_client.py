"""Stealth HTTP client using curl_cffi for TLS fingerprint impersonation.

For requests that don't need a full browser (API calls, data fetching).
Mimics real browser TLS/JA3/HTTP2 fingerprints at the transport layer.
"""

import random

import structlog

logger = structlog.get_logger(__name__)

_BROWSER_IMPERSONATIONS = [
    "chrome131", "chrome130", "chrome124", "chrome120",
    "chrome119", "chrome116", "chrome110",
    "safari18_0", "safari17_5", "safari17_2_1",
    "edge131", "edge127",
]


async def stealth_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: str | None = None,
    json_data: dict | None = None,
    proxy: str | None = None,
    impersonate: str | None = None,
    timeout: float = 30.0,
) -> dict:
    """Make an HTTP request with browser-identical TLS fingerprint.

    Returns dict with: status, text, headers, url
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        logger.warning("curl_cffi_not_installed", hint="pip install curl_cffi")
        # Fallback to httpx without TLS spoofing
        import httpx
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            resp = await client.request(method, url, headers=headers, content=data)
            return {
                "status": resp.status_code,
                "text": resp.text,
                "headers": dict(resp.headers),
                "url": str(resp.url),
            }

    browser = impersonate or random.choice(_BROWSER_IMPERSONATIONS)

    async with AsyncSession(impersonate=browser, timeout=timeout, proxy=proxy) as session:
        resp = await session.request(
            method,
            url,
            headers=headers,
            data=data,
            json=json_data,
        )
        return {
            "status": resp.status_code,
            "text": resp.text,
            "headers": dict(resp.headers),
            "url": str(resp.url),
        }


async def stealth_fetch_page(url: str, proxy: str | None = None) -> str | None:
    """Fetch a page's HTML using stealth HTTP (no browser needed).

    Useful for pages that don't need JS rendering.
    """
    result = await stealth_request(url, proxy=proxy)
    if result["status"] == 200:
        return result["text"]
    return None
