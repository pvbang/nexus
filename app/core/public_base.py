"""Auto-learn the public base URL (scheme://host) from incoming requests.

Why this exists: a server inside Docker can't know the public domain it's served
under (e.g. https://nexus.example.com) — that mapping lives in the outer
nginx/Cloudflare/DNS layer, invisible to the process. So operators had to set
EXTERNAL_BASE_URL by hand just to build a correct preview link.

Instead, we learn it from the very requests users send: their browser/proxy puts
the public host in the ``Host`` header (or ``X-Forwarded-Host`` behind a proxy),
and the scheme in ``X-Forwarded-Proto``. We remember the first good value and use
it to build preview URLs — no manual config needed in the common case.

Precedence (see ``resolve_public_base``): explicit config (EXTERNAL_BASE_URL /
NEXUS_PUBLIC_HOST) always wins; the learned value is the fallback.

``derive_base_url`` is pure (headers in → base URL out). The learned value is held
in a tiny module-level store updated by middleware.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# Hosts we must never "learn" as the public base — they're not what a user typed.
_NON_PUBLIC_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


def derive_base_url(headers: dict) -> str:
    """Derive ``scheme://host`` from request headers, or "" if not derivable.

    Honours a reverse proxy: ``X-Forwarded-Host`` / ``X-Forwarded-Proto`` take
    precedence over ``Host`` (the latter is the proxy↔app hop, not the public one).
    Header lookup is case-insensitive. Internal/loopback hosts are rejected so we
    never learn a non-public address.
    """
    # Case-insensitive view.
    h = {k.lower(): v for k, v in headers.items()}

    host = (h.get("x-forwarded-host") or h.get("host") or "").strip()
    if not host:
        return ""
    # X-Forwarded-Host may be a comma list ("public, internal") — take the first.
    host = host.split(",")[0].strip()
    if not host:
        return ""

    host_only = host.split(":")[0].lower()
    if host_only in _NON_PUBLIC_HOSTS:
        return ""

    proto = (h.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        # No explicit proto: assume https for a real domain, http for a bare IP.
        proto = "http" if _looks_like_ip(host_only) else "https"

    return f"{proto}://{host}"


def _looks_like_ip(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


# ── learned-value store (updated by middleware on each request) ──────────────

_learned_base: str = ""


def remember_base_url(base: str) -> None:
    """Record the public base URL learned from a request (no-op if empty)."""
    global _learned_base
    if base:
        _learned_base = base


def get_learned_base_url() -> str:
    """The most recently learned public base URL ("" if none yet)."""
    return _learned_base


def resolve_public_base(external_base_url: str = "", public_host: str = "") -> str:
    """Resolve the public base URL to use, "" if none available.

    Precedence: EXTERNAL_BASE_URL → NEXUS_PUBLIC_HOST → learned-from-request.

    Security note: the learned value comes from request headers (spoofable). It is
    ONLY the fallback — any explicit config wins — so a production deploy that sets
    EXTERNAL_BASE_URL/NEXUS_PUBLIC_HOST is immune to Host-header spoofing. When the
    learned value is used (no config), we log it so operators notice.
    """
    base = (external_base_url or "").strip().rstrip("/")
    if base:
        return base
    ph = (public_host or "").strip()
    if ph:
        return f"https://{ph}"
    if _learned_base:
        # Used the spoofable learned value — flag it once so prod gets configured.
        logger.warning(
            "public_base_from_request_header",
            learned=_learned_base,
            hint="Set EXTERNAL_BASE_URL or NEXUS_PUBLIC_HOST in production to avoid "
                 "relying on a spoofable Host header for preview links.",
        )
    return _learned_base
