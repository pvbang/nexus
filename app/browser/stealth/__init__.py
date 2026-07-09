"""
Stealth Browser Layer - Anti-bot bypass for AI agents.

Provides undetected browser automation via multiple engines:
- Patchright (patched Playwright/Chromium) - primary
- Camoufox (custom Firefox) - fallback when Chromium blocked
- curl_cffi (TLS fingerprint spoofing) - HTTP-only requests

Usage:
    from app.browser.stealth import create_stealth_browser, StealthConfig

    browser = await create_stealth_browser()
    # Use with browser-use Agent as normal
"""

from app.browser.stealth.config import StealthConfig, StealthEngine
from app.browser.stealth.engine import create_stealth_browser, StealthBrowserManager

__all__ = [
    "create_stealth_browser",
    "StealthBrowserManager",
    "StealthConfig",
    "StealthEngine",
]
