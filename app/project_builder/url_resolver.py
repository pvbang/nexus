"""Resolve the external URL users outside the host can open (audit gap C).

Extracted from ``DevServerManager._resolve_external_url`` as a pure function so the
decision is testable without sockets/Docker. The I/O (detect IP, read settings,
check ``/.dockerenv``) stays in the caller and is passed in.

Why this changed: the old code, when running in Docker without a configured public
host, *fabricated* an address — ``host.docker.internal`` or the default gateway
(e.g. ``172.17.0.1``). Both are NOT reachable from the user's machine, so the user
got a dead link presented as if it worked. We no longer invent an internal address:
we use the detected IP (honest) and return an actionable hint telling the operator
to set ``NEXUS_PUBLIC_HOST`` (or rely on the cloudflared tunnel) for a real URL.
"""

from __future__ import annotations

import ipaddress


def _is_public_ip(host: str) -> bool:
    """True if ``host`` is a routable public IP. Hostnames → False (can't tell)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def resolve_external_url(
    port: int,
    external_base_url: str | None,
    public_host: str | None,
    detected_ip: str,
    in_docker: bool,
) -> tuple[str, str]:
    """Decide the external URL. Returns ``(url, hint)``.

    Priority:
        1. ``external_base_url`` (explicit, wins) — port appended.
        2. ``public_host`` (explicit Docker-friendly host).
        3. ``detected_ip`` — used as-is (honest), no internal-IP fabrication.

    ``hint`` is non-empty only when the result is likely NOT user-reachable
    (in Docker, no explicit host, detected IP not public) so the caller can warn
    and the operator knows the real fix.
    """
    if external_base_url:
        return f"{external_base_url.rstrip('/')}:{port}", ""

    if public_host:
        return f"http://{public_host}:{port}", ""

    url = f"http://{detected_ip}:{port}"

    hint = ""
    if in_docker and not _is_public_ip(detected_ip):
        hint = (
            "Dev server URL may be internal-only (running in Docker without a public "
            "host). Set NEXUS_PUBLIC_HOST=<your-server-ip-or-domain> (or EXTERNAL_BASE_URL) "
            "in .env, or rely on the cloudflared tunnel, to give users a reachable link."
        )
    return url, hint
