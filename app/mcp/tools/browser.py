"""
Nexus - Browser Automation Tool (Playwright).

Navigate, screenshot, extract text, click elements, type into inputs.
Inspired by: hermes-agent Browserbase/CDP integration

Playwright is an optional dependency — all imports are guarded so the
module loads cleanly even when ``playwright`` is not installed.

Usage::

    # Tools are auto-registered into the global registry.
    # Agents can call: browser_navigate, browser_screenshot,
    #   browser_click, browser_type, browser_extract
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Optional

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result
from app.mcp.tools.filesystem import get_task_workspace, WORKSPACE_DIR

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------
# Optional dependency check
# ------------------------------------------------------------------

_playwright_available: bool = False

try:
    from playwright.async_api import (  # type: ignore[import-untyped]
        Browser,
        BrowserContext,
        Page,
        async_playwright,
    )

    _playwright_available = True
except ImportError:
    Browser = None  # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]
    Page = None  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]


def _check_playwright() -> bool:
    """Toolset availability check — called by the registry."""
    return _playwright_available


# ------------------------------------------------------------------
# Singleton browser manager
# ------------------------------------------------------------------


class _BrowserManager:
    """Lazy-initialised singleton that keeps a single browser instance
    alive across tool invocations so we don't pay startup cost each time.
    """

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()

    async def get_page(self) -> Any:
        """Return the active Playwright :class:`Page`, creating one if needed."""
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page

            if async_playwright is None:
                raise RuntimeError("playwright is not installed")

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Nexus/1.0",
            )
            self._page = await self._context.new_page()
            logger.info("browser_manager_started")
            return self._page

    async def close(self) -> None:
        """Teardown browser resources."""
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None
            self._context = None
            self._page = None
            logger.info("browser_manager_closed")


_manager = _BrowserManager()

# ------------------------------------------------------------------
# Screenshot storage
# ------------------------------------------------------------------


def _get_screenshot_dir() -> Path:
    """Get screenshot directory within current task workspace."""
    ws = get_task_workspace()
    d = ws / ".screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_browser_navigate(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Open a URL and return the visible page text."""
    url = params.get("url", "")
    if not url:
        return {"success": False, "output": "No URL provided", "data": None, "artifacts": []}

    try:
        page = await _manager.get_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        status = response.status if response else 0

        # Extract visible text
        text_content = await page.evaluate("() => document.body?.innerText || ''")
        title = await page.title()

        # Truncate large pages
        text_content = text_content[:15_000] if text_content else ""

        return {
            "success": True,
            "output": f"Navigated to: {url}\nTitle: {title}\nStatus: {status}\n\n{text_content}",
            "data": {"url": url, "title": title, "status": status, "text_length": len(text_content)},
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("browser_navigate_error", url=url[:200], error=str(exc))
        return {"success": False, "output": f"Navigation failed: {exc}", "data": None, "artifacts": []}


async def handle_browser_screenshot(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Capture a screenshot of the current page."""
    filename = params.get("filename", "screenshot.png")
    full_page = params.get("full_page", False)

    try:
        page = await _manager.get_page()
        screenshot_dir = _get_screenshot_dir()
        filepath = screenshot_dir / filename
        await page.screenshot(path=str(filepath), full_page=full_page)

        relative_path = filepath.relative_to(WORKSPACE_DIR)
        download_url = f"/api/v1/artifacts/download/{relative_path}"
        return {
            "success": True,
            "output": f"Screenshot saved: {filepath}",
            "data": {"path": str(relative_path), "download_url": download_url, "full_page": full_page},
            "artifacts": [{"type": "image", "path": str(relative_path)}],
        }
    except Exception as exc:
        logger.error("browser_screenshot_error", error=str(exc))
        return {"success": False, "output": f"Screenshot failed: {exc}", "data": None, "artifacts": []}


async def handle_browser_click(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Click an element identified by a CSS selector."""
    selector = params.get("selector", "")
    if not selector:
        return {"success": False, "output": "No selector provided", "data": None, "artifacts": []}

    try:
        page = await _manager.get_page()
        await page.click(selector, timeout=10_000)

        return {
            "success": True,
            "output": f"Clicked element: {selector}",
            "data": {"selector": selector},
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("browser_click_error", selector=selector, error=str(exc))
        return {"success": False, "output": f"Click failed: {exc}", "data": None, "artifacts": []}


async def handle_browser_type(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Type text into an element identified by a CSS selector."""
    selector = params.get("selector", "")
    text = params.get("text", "")
    if not selector or not text:
        return {"success": False, "output": "selector and text are required", "data": None, "artifacts": []}

    try:
        page = await _manager.get_page()
        clear = params.get("clear", True)
        if clear:
            await page.fill(selector, text, timeout=10_000)
        else:
            await page.type(selector, text, timeout=10_000)

        return {
            "success": True,
            "output": f"Typed into {selector}: {text[:100]}",
            "data": {"selector": selector, "text_length": len(text)},
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("browser_type_error", selector=selector, error=str(exc))
        return {"success": False, "output": f"Type failed: {exc}", "data": None, "artifacts": []}


async def handle_browser_extract(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Extract text content from an element by CSS selector."""
    selector = params.get("selector", "body")

    try:
        page = await _manager.get_page()
        element = await page.query_selector(selector)
        if element is None:
            return {
                "success": False,
                "output": f"Element not found: {selector}",
                "data": None,
                "artifacts": [],
            }

        text = await element.inner_text()
        text = text[:15_000] if text else ""

        return {
            "success": True,
            "output": text,
            "data": {"selector": selector, "text_length": len(text)},
            "artifacts": [],
        }
    except Exception as exc:
        logger.error("browser_extract_error", selector=selector, error=str(exc))
        return {"success": False, "output": f"Extract failed: {exc}", "data": None, "artifacts": []}


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "browser"
_CHECK = _check_playwright

registry.register(
    name="browser_navigate",
    toolset=_TOOLSET,
    schema={
        "description": "Navigate to a URL and return the page text content",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    handler=handle_browser_navigate,
    is_async=True,
    check_fn=_CHECK,
    emoji="🌐",
)

registry.register(
    name="browser_screenshot",
    toolset=_TOOLSET,
    schema={
        "description": "Capture a screenshot of the current browser page",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output filename (default: screenshot.png)"},
                "full_page": {"type": "boolean", "description": "Capture full page (default: false)"},
            },
            "required": [],
        },
    },
    handler=handle_browser_screenshot,
    is_async=True,
    check_fn=_CHECK,
    emoji="📸",
)

registry.register(
    name="browser_click",
    toolset=_TOOLSET,
    schema={
        "description": "Click an element on the page by CSS selector",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of element to click"},
            },
            "required": ["selector"],
        },
    },
    handler=handle_browser_click,
    is_async=True,
    check_fn=_CHECK,
    emoji="👆",
)

registry.register(
    name="browser_type",
    toolset=_TOOLSET,
    schema={
        "description": "Type text into an input element by CSS selector",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input element"},
                "text": {"type": "string", "description": "Text to type"},
                "clear": {"type": "boolean", "description": "Clear existing text first (default: true)"},
            },
            "required": ["selector", "text"],
        },
    },
    handler=handle_browser_type,
    is_async=True,
    check_fn=_CHECK,
    emoji="⌨️",
)

registry.register(
    name="browser_extract",
    toolset=_TOOLSET,
    schema={
        "description": "Extract text content from a page element by CSS selector",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector (default: body)"},
            },
            "required": [],
        },
    },
    handler=handle_browser_extract,
    is_async=True,
    check_fn=_CHECK,
    emoji="📄",
)
