"""Anti-detection escalation strategy.

Automatically escalates stealth level when a page blocks the browser:
Level 1: Patchright headless + fingerprint
Level 2: Patchright headful + proxy
Level 3: Camoufox (Firefox) + proxy
Level 4: Byparr cookie extraction → inject into session
"""

import structlog

from app.browser.stealth.config import StealthConfig, StealthEngine
from app.browser.stealth.engine import StealthBrowserManager

logger = structlog.get_logger(__name__)

_BLOCK_INDICATORS = [
    "access denied",
    "403 forbidden",
    "just a moment",
    "attention required",
    "blocked",
    "captcha",
    "verify you are human",
    "please wait",
    "checking your browser",
    "enable javascript",
    "are you a robot",
]


async def detect_block(page) -> bool:
    """Detect if current page is showing a block/challenge."""
    try:
        title = (await page.title()).lower()
        content = (await page.content())[:3000].lower()

        for indicator in _BLOCK_INDICATORS:
            if indicator in title or indicator in content:
                return True

        # Check HTTP status via response
        status = await page.evaluate("() => document.querySelector('meta[http-equiv=\"status\"]')?.content")
        if status and status.startswith("4"):
            return True

    except Exception:
        pass
    return False


async def navigate_with_escalation(
    url: str,
    config: StealthConfig | None = None,
) -> tuple["StealthBrowserManager", any]:
    """Navigate to URL with automatic escalation on block detection.

    Returns (manager, page) tuple. Caller is responsible for closing manager.
    """
    config = config or StealthConfig()
    levels = _build_escalation_levels(config)

    for level_num, level_config in enumerate(levels, 1):
        manager = StealthBrowserManager(level_config)
        try:
            await manager.launch()
            page = await manager.new_page(url)

            if not await detect_block(page):
                logger.info("escalation_success", level=level_num, url=url)
                return manager, page

            logger.info("escalation_blocked", level=level_num, url=url)
            await manager.close()

        except Exception as e:
            logger.warning("escalation_level_failed", level=level_num, error=str(e)[:200])
            await manager.close()

    # All levels exhausted - return last attempt anyway
    manager = StealthBrowserManager(levels[-1])
    await manager.launch()
    page = await manager.new_page(url)
    return manager, page


def _build_escalation_levels(base_config: StealthConfig) -> list[StealthConfig]:
    """Build escalation levels from base config."""
    return [
        # Level 1: Patchright headless + fingerprint (fastest)
        StealthConfig(
            engine=StealthEngine.PATCHRIGHT,
            headless=True,
            use_proxy_pool=False,
            fingerprint_randomize=True,
            human_behavior=True,
            cloudflare_bypass=True,
            byparr_url=base_config.byparr_url,
        ),
        # Level 2: Patchright headful + proxy (more realistic)
        StealthConfig(
            engine=StealthEngine.PATCHRIGHT,
            headless=False,
            use_proxy_pool=True,
            fingerprint_randomize=True,
            human_behavior=True,
            cloudflare_bypass=True,
            byparr_url=base_config.byparr_url,
        ),
        # Level 3: Camoufox (Firefox) + proxy (different browser entirely)
        StealthConfig(
            engine=StealthEngine.CAMOUFOX,
            headless=base_config.headless,
            use_proxy_pool=True,
            fingerprint_randomize=True,
            human_behavior=True,
            cloudflare_bypass=True,
            byparr_url=base_config.byparr_url,
        ),
        # Level 4: Byparr + Patchright (cookie pre-extraction)
        StealthConfig(
            engine=StealthEngine.PATCHRIGHT,
            headless=False,
            use_proxy_pool=True,
            fingerprint_randomize=True,
            human_behavior=True,
            cloudflare_bypass=True,
            byparr_url=base_config.byparr_url or "http://localhost:8191",
        ),
    ]
