"""
Nexus - Inbound Email Classifier.

Decides whether an inbound email is an ACTIONABLE request (should spawn a task)
or an automated NOTIFICATION (GitHub PR comments, mailing-list digests, bulk
mail) that must only be logged — never turned into a task.

Design: vendor-neutral, header-based signals (RFC 3834 / RFC 2369), NOT body
keyword matching. This is sustainable: it catches any auto-generated mail, not
just today's GitHub templates.
"""

from __future__ import annotations

# Sender addresses/domains known to emit only automated notifications.
_NOTIFICATION_SENDERS = (
    "notifications@github.com",
    "no-reply@github.com",
    "noreply@github.com",
)
_NOTIFICATION_DOMAINS = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
)


def _extract_addr(from_field: str) -> str:
    """Pull the bare email address out of a From header value."""
    raw = (from_field or "").strip().lower()
    if "<" in raw and ">" in raw:
        raw = raw[raw.find("<") + 1 : raw.find(">")]
    return raw.strip()


def is_actionable_email(headers: dict[str, str], from_field: str = "") -> bool:
    """Return True if the email should create a task, False if it's a notification.

    Args:
        headers: Mapping of relevant raw header values. Recognized keys (any
            casing accepted): ``Auto-Submitted``, ``Precedence``, ``List-Id``,
            ``X-GitHub-Reason``, ``X-GitHub-Sender``, ``X-Auto-Response-Suppress``.
        from_field: The raw ``From`` header (display name + address).

    Decision order (first match wins toward NOT actionable):
        1. RFC 3834 ``Auto-Submitted`` != ``no``  → automated, not actionable.
        2. ``Precedence`` in {bulk, list, junk}    → bulk mail, not actionable.
        3. Any ``X-GitHub-*`` header present       → GitHub notification.
        4. ``List-Id`` present AND sender domain is a notification domain.
        5. Sender address is a known notification sender.
        Otherwise → actionable.
    """
    h = {k.lower(): (v or "").strip() for k, v in (headers or {}).items()}

    # 1. RFC 3834: auto-generated / auto-replied mail.
    auto_submitted = h.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        return False

    # 2. Bulk / list precedence (RFC 2076 convention).
    precedence = h.get("precedence", "").lower()
    if precedence in ("bulk", "list", "junk"):
        return False

    # 3. GitHub-specific notification headers (header-based, not body keywords).
    if h.get("x-github-reason") or h.get("x-github-sender"):
        return False

    addr = _extract_addr(from_field)
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""

    # 4. Mailing-list message from a notification domain.
    if h.get("list-id") and domain in _NOTIFICATION_DOMAINS:
        return False

    # 5. Known automated sender addresses.
    if addr in _NOTIFICATION_SENDERS:
        return False

    return True
