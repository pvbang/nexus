"""
Nexus - SSRF Guard (OpenClaw-inspired).

Server-Side Request Forgery protection for HTTP-based tools.

Features:
- Block private/reserved IP ranges (RFC1918, link-local, loopback)
- Block cloud metadata endpoints (AWS, GCP, Azure)
- DNS rebinding protection (resolve then validate)
- Configurable allowlist/denylist
- IPv4 and IPv6 support

Inspired by:
- OpenClaw src/web-fetch/ — URL validation before fetch
- OpenClaw src/security/audit-network.py — SSRF surface audit
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────
# Blocked Networks & Hosts
# ─────────────────────────────────────────────────────────────────

BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 private & reserved
    ipaddress.IPv4Network("0.0.0.0/8"),        # "This" network
    ipaddress.IPv4Network("10.0.0.0/8"),       # Private Class A
    ipaddress.IPv4Network("100.64.0.0/10"),    # Carrier-grade NAT
    ipaddress.IPv4Network("127.0.0.0/8"),      # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),   # Link-local / Cloud metadata
    ipaddress.IPv4Network("172.16.0.0/12"),    # Private Class B
    ipaddress.IPv4Network("192.0.0.0/24"),     # IETF Protocol Assignments
    ipaddress.IPv4Network("192.0.2.0/24"),     # TEST-NET-1
    ipaddress.IPv4Network("192.88.99.0/24"),   # 6to4 Relay
    ipaddress.IPv4Network("192.168.0.0/16"),   # Private Class C
    ipaddress.IPv4Network("198.18.0.0/15"),    # Benchmark testing
    ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.IPv4Network("203.0.113.0/24"),   # TEST-NET-3
    ipaddress.IPv4Network("224.0.0.0/4"),      # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),      # Reserved
    ipaddress.IPv4Network("255.255.255.255/32"),  # Broadcast
    # IPv6 private & reserved
    ipaddress.IPv6Network("::1/128"),          # Loopback
    ipaddress.IPv6Network("::/128"),           # Unspecified
    ipaddress.IPv6Network("::ffff:0:0/96"),    # IPv4-mapped (check underlying)
    ipaddress.IPv6Network("64:ff9b::/96"),     # NAT64
    ipaddress.IPv6Network("100::/64"),         # Discard-only
    ipaddress.IPv6Network("2001:db8::/32"),    # Documentation
    ipaddress.IPv6Network("fc00::/7"),         # Unique Local (ULA)
    ipaddress.IPv6Network("fe80::/10"),        # Link-local
    ipaddress.IPv6Network("ff00::/8"),         # Multicast
]

BLOCKED_HOSTS: set[str] = {
    # AWS metadata
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    # Azure metadata
    "169.254.169.254",
    # DigitalOcean metadata
    "169.254.169.254",
    # Kubernetes
    "kubernetes.default.svc",
    # Common local hostnames
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
    "gateway.docker.internal",
}

# Blocked URL schemes
BLOCKED_SCHEMES: set[str] = {
    "file", "ftp", "gopher", "ldap", "dict",
    "ssh", "telnet", "tftp", "jar",
}

# Default allowed schemes
ALLOWED_SCHEMES: set[str] = {"http", "https"}


@dataclass
class SSRFResult:
    """Result of SSRF validation."""

    safe: bool
    url: str = ""
    resolved_ip: str = ""
    reason: str = ""
    blocked_by: str = ""  # "network", "host", "scheme", "dns", "denylist"

    @property
    def blocked(self) -> bool:
        return not self.safe


@dataclass
class SSRFConfig:
    """Configuration for SSRF Guard."""

    # Additional hosts/IPs to block
    denylist_hosts: set[str] = field(default_factory=set)
    # Hosts that bypass SSRF checks (use sparingly)
    allowlist_hosts: set[str] = field(default_factory=set)
    # Allow localhost for development
    allow_localhost: bool = False
    # Allow private networks (DANGEROUS)
    allow_private: bool = False
    # Maximum redirects to follow
    max_redirects: int = 5
    # DNS resolution timeout
    dns_timeout: float = 5.0


class SSRFGuard:
    """
    SSRF protection for HTTP-based tools.

    Validates URLs before making outbound requests to prevent
    access to internal services, cloud metadata, and private networks.

    Usage:
        guard = get_ssrf_guard()
        result = await guard.validate_url("http://example.com/api")
        if result.blocked:
            raise SSRFError(result.reason)
        # Proceed with request using result.resolved_ip

    DNS Rebinding Protection:
        Uses validate_with_dns() to resolve hostname first,
        then validates the resolved IP against blocked networks.
        This prevents DNS rebinding attacks where a hostname
        initially resolves to a safe IP but later resolves to
        a private IP.
    """

    def __init__(self, config: Optional[SSRFConfig] = None) -> None:
        self._config = config or SSRFConfig()
        self._request_count = 0
        self._blocked_count = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "total_requests": self._request_count,
            "blocked_requests": self._blocked_count,
        }

    def validate_url_sync(self, url: str) -> SSRFResult:
        """Synchronous URL validation (no DNS resolution).

        Checks scheme, hostname against blocklists, and parsed IP addresses.
        For full protection including DNS rebinding, use validate_with_dns().
        """
        self._request_count += 1

        # Parse URL
        try:
            parsed = urlparse(url)
        except Exception as e:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, reason=f"Invalid URL: {e}",
                blocked_by="parse",
            )

        # Check scheme
        scheme = (parsed.scheme or "").lower()
        if scheme in BLOCKED_SCHEMES:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url,
                reason=f"Blocked scheme: {scheme}",
                blocked_by="scheme",
            )
        if scheme not in ALLOWED_SCHEMES:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url,
                reason=f"Unsupported scheme: {scheme}",
                blocked_by="scheme",
            )

        # Extract hostname
        hostname = (parsed.hostname or "").lower().strip(".")
        if not hostname:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, reason="Empty hostname",
                blocked_by="host",
            )

        # Check allowlist (bypass all other checks)
        if hostname in self._config.allowlist_hosts:
            return SSRFResult(safe=True, url=url)

        # Check denylist
        if hostname in self._config.denylist_hosts:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url,
                reason=f"Host in denylist: {hostname}",
                blocked_by="denylist",
            )

        # Check blocked hosts
        if hostname in BLOCKED_HOSTS:
            if hostname == "localhost" and self._config.allow_localhost:
                return SSRFResult(safe=True, url=url)
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url,
                reason=f"Blocked host: {hostname}",
                blocked_by="host",
            )

        # Check if hostname is an IP address directly
        try:
            ip = ipaddress.ip_address(hostname)
            ip_result = self._check_ip(ip, url)
            if ip_result is not None:
                return ip_result
        except ValueError:
            pass  # Not an IP literal, will need DNS resolution

        return SSRFResult(safe=True, url=url)

    async def validate_url(self, url: str) -> SSRFResult:
        """Async URL validation (includes basic checks, no DNS resolution).

        For full DNS rebinding protection, use validate_with_dns().
        """
        return self.validate_url_sync(url)

    async def validate_with_dns(self, url: str) -> SSRFResult:
        """Full SSRF validation with DNS resolution.

        Resolves hostname to IP, then validates the resolved IP.
        This prevents DNS rebinding attacks.

        Args:
            url: URL to validate.

        Returns:
            SSRFResult with resolved_ip if safe.
        """
        # First do basic validation
        basic_result = self.validate_url_sync(url)
        if basic_result.blocked:
            return basic_result

        # Extract hostname for DNS resolution
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().strip(".")

        # Skip DNS check if hostname is already an IP
        try:
            ip = ipaddress.ip_address(hostname)
            return SSRFResult(safe=True, url=url, resolved_ip=str(ip))
        except ValueError:
            pass

        # Resolve DNS
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            addrs = await loop.run_in_executor(
                None, lambda: socket.getaddrinfo(
                    hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                    socket.AF_UNSPEC, socket.SOCK_STREAM,
                ),
            )
        except (socket.gaierror, OSError) as e:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url,
                reason=f"DNS resolution failed: {e}",
                blocked_by="dns",
            )

        if not addrs:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url,
                reason="DNS resolution returned no addresses",
                blocked_by="dns",
            )

        # Check ALL resolved IPs (prevent DNS round-robin bypass)
        for addr_info in addrs:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
                ip_result = self._check_ip(ip, url)
                if ip_result is not None:
                    ip_result.resolved_ip = ip_str
                    return ip_result
            except ValueError:
                continue

        # All resolved IPs are safe
        first_ip = addrs[0][4][0]
        return SSRFResult(safe=True, url=url, resolved_ip=first_ip)

    def _check_ip(
        self,
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
        url: str,
    ) -> Optional[SSRFResult]:
        """Check if an IP address is in blocked networks.

        Returns SSRFResult if blocked, None if safe.
        """
        # Handle IPv4-mapped IPv6 addresses
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped

        # Check loopback
        if ip.is_loopback:
            if self._config.allow_localhost:
                return None
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, resolved_ip=str(ip),
                reason=f"Loopback address: {ip}",
                blocked_by="network",
            )

        # Check private
        if ip.is_private:
            if self._config.allow_private:
                return None
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, resolved_ip=str(ip),
                reason=f"Private network address: {ip}",
                blocked_by="network",
            )

        # Check reserved
        if ip.is_reserved:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, resolved_ip=str(ip),
                reason=f"Reserved address: {ip}",
                blocked_by="network",
            )

        # Check link-local
        if ip.is_link_local:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, resolved_ip=str(ip),
                reason=f"Link-local address: {ip}",
                blocked_by="network",
            )

        # Check multicast
        if ip.is_multicast:
            self._blocked_count += 1
            return SSRFResult(
                safe=False, url=url, resolved_ip=str(ip),
                reason=f"Multicast address: {ip}",
                blocked_by="network",
            )

        # Check against explicit blocked networks
        for network in BLOCKED_NETWORKS:
            if ip in network:
                self._blocked_count += 1
                return SSRFResult(
                    safe=False, url=url, resolved_ip=str(ip),
                    reason=f"Address {ip} in blocked network {network}",
                    blocked_by="network",
                )

        return None  # Safe


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_ssrf_guard: Optional[SSRFGuard] = None


def get_ssrf_guard() -> SSRFGuard:
    """Get the singleton SSRFGuard instance."""
    global _ssrf_guard
    if _ssrf_guard is None:
        _ssrf_guard = SSRFGuard()
    return _ssrf_guard


def configure_ssrf_guard(config: SSRFConfig) -> SSRFGuard:
    """Configure and return SSRFGuard with custom settings."""
    global _ssrf_guard
    _ssrf_guard = SSRFGuard(config=config)
    return _ssrf_guard
