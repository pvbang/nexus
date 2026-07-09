"""Extract user-facing preview/demo URLs from task activity.

Audit gap B: when an agent builds and hosts an app, the public URL lived only in
tool output and reached the user ONLY if the LLM remembered to copy it through
several summary layers — so it often didn't. This pulls the URL out
deterministically so the final report can surface it regardless of the LLM.

Pure function over text — no I/O. Prefers genuinely reachable URLs (public tunnel
hosts, explicit external hosts) and drops internal-only ones (localhost, Docker
internal IPs) that the user cannot open.
"""

from __future__ import annotations

import re

# A reachable URL the user can actually open in a browser.
_URL_RE = re.compile(r"https?://[^\s\)\"'<>\]}]+")

# Hosts that are NOT reachable from the user's machine — never surface these as a
# "live demo" link.
_INTERNAL_HOST_MARKERS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "host.docker.internal",
)

# Internal/private IP ranges (Docker bridge, LAN) — also not user-reachable.
_PRIVATE_IP_RE = re.compile(
    r"https?://(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(?::\d+)?"
)

# Hosts that indicate a genuinely public tunnel/preview — ranked first.
_PUBLIC_TUNNEL_MARKERS = (
    "trycloudflare.com",
    "bore.pub",
    ".ngrok.",
    ".ngrok-free.app",
    ".loca.lt",
)

# A URL like https://domain:19000 (dev-server port appended to the public host) is
# a DEAD link behind a reverse proxy — the dev-server port is not published on 443.
# The reachable form is the domain's /preview/<port>/ path. Drop the :port variant
# so the user is never handed the dead link.
_DEAD_PORT_RE = re.compile(r"^https?://[^/:]+:(\d{4,5})(?:/|$)")
_DEV_PORT_MIN, _DEV_PORT_MAX = 19000, 19049


def _is_internal(url: str) -> bool:
    low = url.lower()
    if any(m in low for m in _INTERNAL_HOST_MARKERS):
        return True
    if _PRIVATE_IP_RE.match(url):
        return True
    # host:devport (not the /preview/ path) → unreachable behind the proxy.
    m = _DEAD_PORT_RE.match(url)
    if m and _DEV_PORT_MIN <= int(m.group(1)) <= _DEV_PORT_MAX:
        return True
    return False


def _is_public_tunnel(url: str) -> bool:
    low = url.lower()
    return any(m in low for m in _PUBLIC_TUNNEL_MARKERS)


def _is_public_tunnel(url: str) -> bool:
    low = url.lower()
    return any(m in low for m in _PUBLIC_TUNNEL_MARKERS)


def extract_preview_urls(text: str) -> list[str]:
    """Return reachable preview URLs found in ``text``, best-first, de-duplicated.

    Ordering: public-tunnel URLs first (most likely user-reachable), then other
    non-internal URLs in first-seen order. Internal-only URLs are excluded.
    """
    if not text:
        return []

    seen: set[str] = set()
    public: list[str] = []
    other: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(".,;:!?)")  # trailing punctuation from prose
        if url in seen or _is_internal(url):
            seen.add(url)
            continue
        seen.add(url)
        (public if _is_public_tunnel(url) else other).append(url)

    return public + other


def build_preview_banner(text: str) -> str:
    """Build a markdown 'Live demo' banner from URLs in ``text`` ("" if none).

    Meant to be prepended to a BUILD hand-over note so the user sees a working
    link at the top — independent of whether the LLM copied it into prose.
    """
    urls = extract_preview_urls(text)
    if not urls:
        return ""
    if len(urls) == 1:
        return f"## 🌐 Live demo\n{urls[0]}\n"
    lines = ["## 🌐 Live demo"]
    lines.extend(f"- {u}" for u in urls[:5])
    return "\n".join(lines) + "\n"
