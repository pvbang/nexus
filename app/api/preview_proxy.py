"""Reverse-proxy so users open agent-hosted apps via the project's own domain.

When an agent builds + runs an app on a dev-server port (19000-19049), the user
needs a link they can actually click. Instead of requiring a public IP / tunnel /
extra DNS, we proxy through the main app's existing domain:

    https://nexus.example.com/preview/19000/         → 127.0.0.1:19000/
    https://nexus.example.com/preview/19000/api/foo  → 127.0.0.1:19000/api/foo

This module holds the PURE logic (port allow-listing, base-href injection, header
rewriting) so it's unit-testable without sockets. The router (preview_router.py)
does the actual async streaming proxy.

Key correctness points:
- SSRF guard: only the dev-server port range is proxyable — never an arbitrary
  port (which could reach Postgres/Redis/internal services on the host).
- SPA asset paths: HTML built for "/" requests assets at "/assets/...". Under the
  "/preview/{port}/" prefix the browser would drop the prefix and 404. We inject
  <base href="/preview/{port}/"> so relative asset URLs resolve correctly.
"""

from __future__ import annotations

import re

# Dev-server preview ports (must match DevServerManager FRONTEND/BACKEND ranges).
PREVIEW_PORT_MIN = 19000
PREVIEW_PORT_MAX = 19049


def is_proxyable_port(port: int) -> bool:
    """Only allow the dev-server preview range — blocks SSRF to internal services."""
    return PREVIEW_PORT_MIN <= port <= PREVIEW_PORT_MAX


def preview_prefix(port: int) -> str:
    """The URL prefix this preview is served under (trailing slash)."""
    return f"/preview/{port}/"


def build_upstream_url(port: int, path: str, query: str = "") -> str:
    """Build the internal upstream URL for a proxied request.

    ``path`` is the part after ``/preview/{port}/`` (no leading slash needed).
    """
    path = path.lstrip("/")
    url = f"http://127.0.0.1:{port}/{path}"
    if query:
        url += f"?{query}"
    return url


# Matches an opening <head> / <head ...> tag (case-insensitive) to inject <base>
# right after. The `(\s[^>]*)?` guard prevents matching <header>.
_HEAD_RE = re.compile(rb"<head(\s[^>]*)?>", re.IGNORECASE)
# Matches an existing <base ...> tag so we don't double-inject.
_BASE_RE = re.compile(rb"<base\b", re.IGNORECASE)


def inject_base_href(html: bytes, port: int) -> bytes:
    """Inject <base href="/preview/{port}/"> so SPA relative assets resolve.

    No-op if the document already has a <base> tag (respect the app's own base).
    If there's no <head>, the HTML is returned unchanged (nothing safe to anchor to).
    """
    if _BASE_RE.search(html):
        return html
    tag = f'<base href="/preview/{port}/">'.encode()
    m = _HEAD_RE.search(html)
    if not m:
        return html
    insert_at = m.end()
    return html[:insert_at] + tag + html[insert_at:]


def rewrite_location_header(location: str, port: int) -> str:
    """Rewrite an upstream redirect Location so it stays under the preview prefix.

    - Absolute-path redirect ("/login")      → "/preview/{port}/login"
    - Loopback absolute URL to our own port   → strip host, keep path under prefix
    - External absolute URL (other host)      → left unchanged
    - Already-prefixed path                   → unchanged
    """
    if not location:
        return location

    prefix = preview_prefix(port).rstrip("/")  # "/preview/{port}"

    # Loopback absolute URL pointing back at the proxied app → reduce to its path.
    for host in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        if location.startswith(host):
            location = location[len(host):] or "/"
            break

    # Absolute-path redirect that isn't already prefixed → add the prefix.
    if location.startswith("/") and not location.startswith(prefix + "/") and location != prefix:
        return prefix + location

    return location
