"""Stealth browser engine - creates undetected browser instances.

Supports multiple engines with automatic fallback:
1. Patchright (patched Chromium via Playwright API) - primary
2. Camoufox (modified Firefox) - fallback when Chromium is blocked
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import structlog

from app.browser.stealth.cloudflare import (
    bypass_via_byparr,
    inject_cf_cookies,
    wait_for_cloudflare_challenge,
)
from app.browser.stealth.config import ProxyConfig, StealthConfig, StealthEngine
from app.browser.stealth.fingerprint import (
    BrowserFingerprint,
    fingerprint_to_js_overrides,
    generate_fingerprint,
)
from app.browser.stealth.human_behavior import HUMAN_BEHAVIOR_INIT_JS, human_delay
from app.browser.stealth.proxy_pool import get_proxy_pool

logger = structlog.get_logger(__name__)


def _is_docker() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "1"


def _detect_platform() -> str:
    if _is_docker():
        return "docker"
    return sys.platform


async def _launch_patchright(
    config: StealthConfig,
    fingerprint: BrowserFingerprint,
    proxy_url: str | None,
) -> Any:
    """Launch browser via Patchright (undetected Playwright fork)."""
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "patchright not installed. Install with: pip install patchright && python -m patchright install chromium"
        )

    pw = await async_playwright().start()

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        "--disable-web-security",
        f"--window-size={fingerprint.viewport_width},{fingerprint.viewport_height}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-popup-blocking",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
    ]

    if _detect_platform() == "docker":
        launch_args.extend([
            "--no-sandbox",
            "--disable-gpu-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ])

    proxy_settings = None
    if proxy_url:
        proxy_settings = {"server": proxy_url}

    browser = await pw.chromium.launch(
        headless=config.headless,
        args=launch_args,
        proxy=proxy_settings,
    )

    context = await browser.new_context(
        viewport={"width": fingerprint.viewport_width, "height": fingerprint.viewport_height},
        user_agent=fingerprint.user_agent,
        locale=fingerprint.locale,
        timezone_id=fingerprint.timezone,
        color_scheme="light",
        extra_http_headers={
            "Accept-Language": f"{fingerprint.locale},en;q=0.9",
            "sec-ch-ua-platform": f'"{fingerprint.platform}"',
        },
    )

    # Inject fingerprint overrides and human behavior patches
    await context.add_init_script(fingerprint_to_js_overrides(fingerprint))
    if config.human_behavior:
        await context.add_init_script(HUMAN_BEHAVIOR_INIT_JS)

    return pw, browser, context


async def _launch_camoufox(
    config: StealthConfig,
    fingerprint: BrowserFingerprint,
    proxy_url: str | None,
) -> Any:
    """Launch browser via Camoufox (anti-detect Firefox)."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        raise ImportError(
            "camoufox not installed. Install with: pip install camoufox && python -m camoufox fetch"
        )

    launch_kwargs: dict[str, Any] = {
        "headless": config.headless,
        "humanize": config.human_behavior,
    }

    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}

    # Camoufox handles fingerprinting internally at C++ level
    camoufox = AsyncCamoufox(**launch_kwargs)
    browser = await camoufox.start()
    context = browser  # Camoufox context IS the browser

    # Still inject our timezone override since Camoufox may not handle all cases
    page = await context.new_page()
    await page.close()

    return camoufox, browser, context


class StealthBrowserManager:
    """Manages stealth browser lifecycle with automatic engine selection and fallback."""

    def __init__(self, config: StealthConfig | None = None):
        self.config = config or StealthConfig()
        self._pw = None
        self._browser = None
        self._context = None
        self._engine_used: StealthEngine | None = None
        self._fingerprint: BrowserFingerprint | None = None

    @property
    def engine_used(self) -> StealthEngine | None:
        return self._engine_used

    @property
    def fingerprint(self) -> BrowserFingerprint | None:
        return self._fingerprint

    async def launch(self) -> Any:
        """Launch stealth browser, returns Playwright-compatible context."""
        engine = self.config.engine
        proxy_url = await self._resolve_proxy()

        fp_engine = "firefox" if engine == StealthEngine.CAMOUFOX else "chrome"
        self._fingerprint = generate_fingerprint(fp_engine)

        if engine == StealthEngine.AUTO:
            # Try Patchright first, fallback to Camoufox
            try:
                self._pw, self._browser, self._context = await _launch_patchright(
                    self.config, self._fingerprint, proxy_url
                )
                self._engine_used = StealthEngine.PATCHRIGHT
                logger.info("stealth_browser_launched", engine="patchright")
                return self._context
            except ImportError:
                logger.info("patchright_not_available_trying_camoufox")
            except Exception as e:
                logger.warning("patchright_launch_failed", error=str(e)[:200])

            # Fallback to Camoufox
            try:
                self._fingerprint = generate_fingerprint("firefox")
                self._pw, self._browser, self._context = await _launch_camoufox(
                    self.config, self._fingerprint, proxy_url
                )
                self._engine_used = StealthEngine.CAMOUFOX
                logger.info("stealth_browser_launched", engine="camoufox")
                return self._context
            except ImportError:
                raise ImportError(
                    "Neither patchright nor camoufox installed. "
                    "Install at least one: pip install patchright && python -m patchright install chromium"
                )

        elif engine == StealthEngine.PATCHRIGHT:
            self._pw, self._browser, self._context = await _launch_patchright(
                self.config, self._fingerprint, proxy_url
            )
            self._engine_used = StealthEngine.PATCHRIGHT
            logger.info("stealth_browser_launched", engine="patchright")
            return self._context

        elif engine == StealthEngine.CAMOUFOX:
            self._fingerprint = generate_fingerprint("firefox")
            self._pw, self._browser, self._context = await _launch_camoufox(
                self.config, self._fingerprint, proxy_url
            )
            self._engine_used = StealthEngine.CAMOUFOX
            logger.info("stealth_browser_launched", engine="camoufox")
            return self._context

    async def new_page(self, url: str | None = None) -> Any:
        """Create a new page with stealth protections applied."""
        if not self._context:
            await self.launch()

        page = await self._context.new_page()

        if url:
            await page.goto(url, wait_until="domcontentloaded")

            # Handle Cloudflare challenge if detected
            if self.config.cloudflare_bypass:
                await self._handle_cloudflare(page, url)

        if self.config.human_behavior:
            await human_delay(300, 800)

        return page

    async def _handle_cloudflare(self, page, url: str) -> None:
        """Detect and bypass Cloudflare challenge."""
        title = await page.title()
        if "Just a moment" in title or "Attention Required" in title:
            logger.info("cloudflare_challenge_detected", url=url)

            # Strategy 1: Wait for auto-solve (Patchright/Camoufox can often solve it)
            solved = await wait_for_cloudflare_challenge(page, timeout_ms=10000)
            if solved:
                logger.info("cloudflare_challenge_solved_auto")
                return

            # Strategy 2: Use Byparr if configured
            if self.config.byparr_url:
                result = await bypass_via_byparr(url, self.config.byparr_url)
                if result and result.get("cookies"):
                    await inject_cf_cookies(self._context, result["cookies"])
                    await page.reload()
                    logger.info("cloudflare_challenge_solved_byparr")
                    return

            logger.warning("cloudflare_challenge_unsolved", url=url)

    async def _resolve_proxy(self) -> str | None:
        """Get proxy URL from config or pool."""
        if self.config.proxy and self.config.proxy.server:
            return self.config.proxy.server

        if self.config.use_proxy_pool:
            pool = await get_proxy_pool()
            return await pool.get_proxy()

        return None

    async def close(self) -> None:
        """Close browser and cleanup resources."""
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.debug("stealth_browser_close_error", error=str(e)[:100])
        finally:
            self._browser = None
            self._context = None
            self._pw = None


async def create_stealth_browser(config: StealthConfig | None = None) -> "StealthBrowserManager":
    """Create and launch a stealth browser instance.

    Usage:
        manager = await create_stealth_browser()
        page = await manager.new_page("https://example.com")
        # ... do work ...
        await manager.close()
    """
    manager = StealthBrowserManager(config)
    await manager.launch()
    return manager


@asynccontextmanager
async def stealth_browser(config: StealthConfig | None = None):
    """Context manager for stealth browser.

    Usage:
        async with stealth_browser() as manager:
            page = await manager.new_page("https://example.com")
    """
    manager = await create_stealth_browser(config)
    try:
        yield manager
    finally:
        await manager.close()
