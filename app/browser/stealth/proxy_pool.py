"""Free proxy pool with automatic fetching, validation, and rotation."""

import asyncio
import random
import time
from dataclasses import dataclass, field

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Public free proxy sources
_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]

_VALIDATION_URL = "https://httpbin.org/ip"
_VALIDATION_TIMEOUT = 8.0
_REFRESH_INTERVAL = 300  # 5 minutes


@dataclass
class ProxyEntry:
    url: str
    last_used: float = 0
    fail_count: int = 0
    response_time: float = 999.0
    is_valid: bool = False


@dataclass
class ProxyPool:
    _proxies: list[ProxyEntry] = field(default_factory=list)
    _blacklist: set[str] = field(default_factory=set)
    _last_refresh: float = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _max_pool_size: int = 50
    _max_fails: int = 3

    async def get_proxy(self) -> str | None:
        """Get next available proxy from pool."""
        async with self._lock:
            if not self._proxies or time.time() - self._last_refresh > _REFRESH_INTERVAL:
                await self._refresh()

            valid = [p for p in self._proxies if p.is_valid and p.fail_count < self._max_fails]
            if not valid:
                await self._refresh()
                valid = [p for p in self._proxies if p.is_valid and p.fail_count < self._max_fails]

            if not valid:
                return None

            # Weighted selection: prefer faster proxies used less recently
            valid.sort(key=lambda p: (p.response_time + (time.time() - p.last_used) * -0.01))
            proxy = valid[0]
            proxy.last_used = time.time()
            return proxy.url

    async def report_failure(self, proxy_url: str) -> None:
        """Report a proxy as failed."""
        for p in self._proxies:
            if p.url == proxy_url:
                p.fail_count += 1
                if p.fail_count >= self._max_fails:
                    p.is_valid = False
                    self._blacklist.add(proxy_url)
                break

    async def report_success(self, proxy_url: str) -> None:
        """Report a proxy as working."""
        for p in self._proxies:
            if p.url == proxy_url:
                p.fail_count = max(0, p.fail_count - 1)
                break

    async def _refresh(self) -> None:
        """Fetch fresh proxies from public sources."""
        logger.info("proxy_pool_refreshing")
        raw_proxies: set[str] = set()

        async with httpx.AsyncClient(timeout=10.0) as client:
            for source in _PROXY_SOURCES:
                try:
                    resp = await client.get(source)
                    if resp.status_code == 200:
                        for line in resp.text.strip().splitlines():
                            line = line.strip()
                            if line and ":" in line and line not in self._blacklist:
                                raw_proxies.add(f"http://{line}")
                except Exception:
                    continue

        if not raw_proxies:
            logger.warning("proxy_pool_no_sources")
            return

        # Validate a sample concurrently
        sample = random.sample(list(raw_proxies), min(len(raw_proxies), self._max_pool_size * 2))
        validated = await self._validate_batch(sample)

        self._proxies = validated[: self._max_pool_size]
        self._last_refresh = time.time()
        logger.info("proxy_pool_refreshed", valid_count=len(self._proxies))

    async def _validate_batch(self, proxy_urls: list[str]) -> list[ProxyEntry]:
        """Validate proxies concurrently."""
        semaphore = asyncio.Semaphore(20)

        async def check_one(url: str) -> ProxyEntry | None:
            async with semaphore:
                try:
                    start = time.time()
                    async with httpx.AsyncClient(
                        proxy=url, timeout=_VALIDATION_TIMEOUT
                    ) as client:
                        resp = await client.get(_VALIDATION_URL)
                        if resp.status_code == 200:
                            elapsed = time.time() - start
                            return ProxyEntry(url=url, response_time=elapsed, is_valid=True)
                except Exception:
                    pass
                return None

        results = await asyncio.gather(*[check_one(url) for url in proxy_urls])
        valid = [r for r in results if r is not None]
        valid.sort(key=lambda p: p.response_time)
        return valid


# Singleton pool instance
_pool: ProxyPool | None = None


async def get_proxy_pool() -> ProxyPool:
    """Get or create the global proxy pool."""
    global _pool
    if _pool is None:
        _pool = ProxyPool()
    return _pool
