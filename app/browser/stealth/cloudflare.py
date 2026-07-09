"""Cloudflare bypass strategies."""

import asyncio

import httpx
import structlog

logger = structlog.get_logger(__name__)


async def bypass_via_byparr(url: str, byparr_url: str) -> dict | None:
    """Use Byparr service to get Cloudflare clearance cookies.

    Byparr runs as a Docker container and solves CF challenges via a real browser.
    Returns cookies + user-agent that can be injected into the stealth browser session.
    """
    endpoint = f"{byparr_url.rstrip('/')}/v1"
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }

    try:
        async with httpx.AsyncClient(timeout=70.0) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    solution = data.get("solution", {})
                    return {
                        "cookies": solution.get("cookies", []),
                        "user_agent": solution.get("userAgent", ""),
                        "url": solution.get("url", url),
                    }
                logger.warning("byparr_challenge_failed", status=data.get("status"))
            else:
                logger.warning("byparr_http_error", status=resp.status_code)
    except httpx.ConnectError:
        logger.warning("byparr_not_available", url=byparr_url)
    except Exception as e:
        logger.warning("byparr_error", error=str(e)[:200])

    return None


async def wait_for_cloudflare_challenge(page, timeout_ms: int = 15000) -> bool:
    """Wait for and attempt to pass Cloudflare challenge in browser.

    Works with Patchright/Camoufox that can auto-solve Turnstile.
    """
    try:
        # Wait for CF challenge to appear and resolve
        for _ in range(timeout_ms // 1000):
            # Check if we're on a CF challenge page
            is_cf = await page.evaluate("""() => {
                return document.title.includes('Just a moment') ||
                       document.title.includes('Attention Required') ||
                       document.querySelector('#challenge-running') !== null ||
                       document.querySelector('.cf-browser-verification') !== null ||
                       document.querySelector('#turnstile-wrapper') !== null;
            }""")

            if not is_cf:
                return True

            # Try clicking the Turnstile checkbox if visible
            turnstile = await page.query_selector(
                'iframe[src*="challenges.cloudflare.com"]'
            )
            if turnstile:
                box = await turnstile.bounding_box()
                if box:
                    # Click center of turnstile iframe
                    await page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )

            await asyncio.sleep(1)

        return False
    except Exception as e:
        logger.debug("cf_challenge_check_error", error=str(e)[:100])
        return False


async def inject_cf_cookies(context, cookies: list[dict]) -> None:
    """Inject Cloudflare clearance cookies into browser context."""
    if not cookies:
        return

    formatted = []
    for cookie in cookies:
        entry = {
            "name": cookie.get("name", ""),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
        }
        if "expires" in cookie:
            entry["expires"] = cookie["expires"]
        formatted.append(entry)

    if formatted:
        await context.add_cookies(formatted)
        logger.debug("cf_cookies_injected", count=len(formatted))
