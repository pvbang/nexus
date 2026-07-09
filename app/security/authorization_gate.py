"""Pre-flight authorization gate for LIVE OFFENSIVE operations (audit C2).

Audit of task 4c05b7ff found the orchestrator accepted "escalate privileges +
control the entire system" against a REAL public IP (117.5.141.7) and executed
live commands with NO authorization check — the generated ROE.md was written but
never enforced.

Policy (mirrors the Claude Code harness): authorized pentest / CTF / security
research / defensive work is fine. What requires an EXPLICIT owner opt-in is a
LIVE OFFENSIVE operation against REAL, public-routable infrastructure — the
dual-use case that needs clear authorization context. Lab/CTF/private/internal
targets, passive OSINT, and non-security tasks are never gated.

Classification is FORMAT-based (``ipaddress`` + the same target regex the ROE
generator uses), never semantic keyword matching — so it can't be talked around
and can't false-positive on prose.

All functions here are pure (no I/O); the env-var read happens in the caller and
is passed in as ``authorized: bool`` so this stays unit-testable.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from enum import Enum

from app.security.roe_generator import _DOMAIN_RE, _IP_RE

# Reuse the ROE generator's target extractors (format-based, single source of truth).

# Safe targets are matched by DOMAIN LABEL / SUFFIX, never bare substring — a
# substring test (`"htb" in "htbank.com"`) is trivially bypassable and was the
# original gate's critical hole. Two precise rules instead:
#
# _SAFE_SUFFIXES: the domain must END with one of these (dotted boundary), e.g.
#   "target.lab.example.com" → ".example.com"; "box.htb" → ".htb". This covers
#   RFC-reserved/non-routable namespaces and CTF/training TLDs.
_SAFE_SUFFIXES = (
    ".lab",
    ".local",
    ".internal",
    ".test",
    ".invalid",
    ".localhost",
    ".example",            # the reserved .example TLD
    ".example.com",
    ".example.org",
    ".example.net",
    ".htb",                # HackTheBox
    ".thm",                # TryHackMe
)
# _SAFE_EXACT: the whole host equals one of these.
_SAFE_EXACT = (
    "localhost",
    "example.com",
    "example.org",
    "example.net",
)
# _SAFE_LABELS: a full dot-separated LABEL equals one of these (e.g. the "ctf" in
#   "ctf.acme.com" or "acme.hackthebox.io"), never a substring of a label.
_SAFE_LABELS = (
    "hackthebox",
    "tryhackme",
    "vulnhub",
    "ctf",
    "pentesterlab",
)

_ENV_ALLOW_LIVE_OFFENSIVE = "ALLOW_LIVE_OFFENSIVE"


class TargetClass(Enum):
    """How the task's target infrastructure is classified."""

    NONE = "none"  # no infrastructure target (e.g. product design, writing)
    SAFE = "safe"  # private/loopback/reserved IP, lab/CTF/example domain
    REAL_PUBLIC_INFRA = "real_public_infra"  # public-routable IP / real domain


@dataclass
class AuthorizationDecision:
    """Outcome of the pre-flight gate."""

    blocked: bool
    target: str
    target_class: TargetClass
    reason: str


# Candidate IPv6 literals: a run of hex groups/colons (optionally bracketed). Loose
# on purpose — every candidate is validated with ``ipaddress`` below, so a false
# catch like a "10:30" time is rejected there rather than here. Requires "::" or at
# least two colons so a single "key:value" never qualifies.
_IPV6_CANDIDATE_RE = re.compile(r"\[?([0-9A-Fa-f:]*::[0-9A-Fa-f:.]*|(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f:.]+)\]?")


def _ip_is_safe(ip_str: str) -> bool:
    """A private/loopback/reserved/link-local IP is a safe (non-public) target.

    For IPv6 transition addresses that EMBED an IPv4 (v4-mapped ``::ffff:x``, 6to4
    ``2002::/16``, Teredo ``2001::/32``), classify by the embedded IPv4 — otherwise
    a public IPv4 written in tunnel form slips through, because ``ipaddress`` marks
    2002::/16 and 2001::/32 as is_private (audit B review: 117.5.141.7 → 2002:7505:8d07::).
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # Collect every IPv4 this v6 address embeds. Teredo carries BOTH a server
        # and a client IPv4 — treat the address as unsafe if ANY embedded IPv4 is
        # public (the server is what's actually reachable).
        embedded: list = []
        if ip.ipv4_mapped:
            embedded.append(ip.ipv4_mapped)
        if ip.sixtofour:
            embedded.append(ip.sixtofour)
        if ip.teredo:
            embedded.extend(ip.teredo)  # (server_ipv4, client_ipv4)
        if embedded:
            return all(_ip_is_safe(str(v4)) for v4 in embedded)
    return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast


def _iter_ipv6(message: str):
    """Yield (validated_ipv6_str) for each genuine IPv6 literal in the message.

    Each candidate is parsed by ``ipaddress`` so prose colons (times, key:value)
    that merely look IPv6-ish are discarded. Strips one set of [] brackets.
    """
    for m in _IPV6_CANDIDATE_RE.findall(message or ""):
        cand = m.strip().strip("[]")
        try:
            ipaddress.IPv6Address(cand)
        except ValueError:
            continue
        yield cand


def _domain_is_safe(domain: str) -> bool:
    """A domain is safe only by exact host, dotted suffix, or full-label match.

    Substring matching is deliberately avoided — "htb" in "htbank.com" must NOT
    make a real public bank look like a CTF box.
    """
    low = domain.lower().rstrip(".")
    if low in _SAFE_EXACT:
        return True
    if any(low == suf.lstrip(".") or low.endswith(suf) for suf in _SAFE_SUFFIXES):
        return True
    labels = low.split(".")
    return any(lbl in _SAFE_LABELS for lbl in labels)


def classify_target(message: str) -> tuple[TargetClass, str]:
    """Classify the most significant infrastructure target named in ``message``.

    Returns (class, target_string). IP literals are checked first (most explicit),
    then domains. Private/loopback/reserved IPs and lab/CTF/example domains are
    SAFE; any other public-routable IP or real domain is REAL_PUBLIC_INFRA. If no
    IP/domain appears at all, the class is NONE.
    """
    # IPs first — an IP literal is the least ambiguous target signal. IPv6 then IPv4.
    ip_present = False
    for v6 in _iter_ipv6(message or ""):
        ip_present = True
        if not _ip_is_safe(v6):
            return TargetClass.REAL_PUBLIC_INFRA, v6
    for ip_match in _IP_RE.findall(message or ""):
        if not _ip_is_safe(ip_match):
            return TargetClass.REAL_PUBLIC_INFRA, ip_match
    # Any IP present but all safe → safe target.
    ip_present = ip_present or bool(_IP_RE.search(message or ""))

    real_domain = ""
    safe_domain_present = False
    for dom in _DOMAIN_RE.findall(message or ""):
        # A dotted-decimal IP also matches the domain regex; skip those (handled above).
        if _IP_RE.fullmatch(dom):
            continue
        if _domain_is_safe(dom):
            safe_domain_present = True
            continue
        real_domain = real_domain or dom

    if real_domain:
        return TargetClass.REAL_PUBLIC_INFRA, real_domain
    if ip_present or safe_domain_present:
        return TargetClass.SAFE, ""
    return TargetClass.NONE, ""


# Explicit hands-on OFFENSIVE action phrases. This is a SECONDARY backstop, not the
# primary classifier (the LLM task_type is primary). Kept deliberately NARROW —
# only unambiguous active-attack intent — so it adds coverage when the LLM
# mislabels an attack, without false-positiving on OSINT/defensive/benign work.
# Multi-word phrases avoid the bare-keyword trap ("test"/"attack" alone never fire).
_OFFENSIVE_PHRASES = (
    # Execution / RCE
    "remote code execution", "rce on", "run arbitrary code", "arbitrary code execution",
    # Shell acquisition / initial access
    "gain a shell", "get a shell", "obtain a shell", "spawn a shell", "pop a shell",
    "reverse shell", "bind shell", "web shell", "webshell", "drop a webshell",
    "gain root", "get root", "root the box", "root the host", "root the server",
    "hack into", "break into the", "pwn the box", "pwn the host",
    # Persistence
    "install a backdoor", "plant a backdoor", "establish persistence",
    # Credential access
    "dump credentials", "dump the database", "dump hashes", "pass the hash",
    "crack the password", "crack passwords", "brute force the", "credential stuffing",
    "steal credentials", "harvest credentials",
    # Lateral movement
    "lateral movement", "pivot to the", "move laterally",
    # Collection / exfiltration
    "exfiltrate", "data exfiltration",
    # Impact
    "deface the", "deploy ransomware", "wipe the server", "encrypt all files",
    # Injection
    "sql injection", "command injection", "code injection",
    # Take-over / compromise / control — anchored to infra nouns (no business sense)
    "take over the server", "take over the host", "take over the system",
    "take over the machine", "take over the network",
    "full control of the server", "full control of the host",
    "full control of the system", "full control of the machine",
    "compromise the server", "compromise the host", "compromise the system",
    "compromise the network", "compromise the machine", "compromise the target",
    # Vietnamese — same hands-on offensive actions.
    "leo thang đặc quyền", "khai thác lỗ hổng", "chiếm quyền điều khiển",
    "chiếm quyền root", "đoạt quyền root", "chiếm shell", "kiểm soát toàn bộ hệ thống",
    "cài backdoor", "cài mã độc", "đánh cắp dữ liệu", "rò rỉ dữ liệu",
    "dò mật khẩu", "bẻ khóa mật khẩu", "tấn công khai thác",
)

# Morphological variants a fixed literal can't catch cleanly (still \b-anchored
# where it matters via the leading word in each fragment).
_OFFENSIVE_PATTERNS = (
    r"exploit (?:the|a|this|that) vulnerabilit\w*",   # exploit the vulnerability/-ies
    r"exploit (?:the|a) vuln\b",
    r"exploit (?:the|a) cve\b",
    r"privilege escalation",
    r"escalate privilege\w*",                          # escalate privilege/privileges
    r"escalate to (?:root|admin|administrator|system)",
    r"gain (?:root|admin|administrator|system) (?:access|privilege\w*)",
)

# Compiled once: literal phrases (\b-bounded) + morphological patterns.
_OFFENSIVE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _OFFENSIVE_PHRASES) + r")\b"
    + "|" + "|".join(_OFFENSIVE_PATTERNS),
    re.IGNORECASE | re.UNICODE,
)


def detect_offensive_intent(message: str) -> bool:
    """Independent backstop: does the text express explicit hands-on offensive intent?

    NOT the primary classifier (the orchestrator LLM owns task_type). This exists
    so the authorization gate doesn't depend solely on the very LLM-classification
    layer it protects against — a real attack mislabeled "general" must still be
    caught. It covers the major hands-on attack verbs across the ATT&CK arc in
    EN+VI, but is a SEED SET, not an exhaustive classifier: an attacker phrasing
    not listed here can still miss (the FORMAT-based target check + LLM task_type
    remain the other two layers). Matches only unambiguous active-attack phrases
    (word-bounded, never bare ambiguous words); never passive verbs
    (OSINT/monitor/audit/investigate).
    """
    if not message:
        return False
    return _OFFENSIVE_RE.search(message) is not None


def requires_authorization(
    target_class: TargetClass, is_offensive: bool, authorized: bool
) -> bool:
    """Whether the task must be BLOCKED pending explicit owner authorization.

    Blocks ⟺ ALL of:
      - the task is offensive (active attack, not passive OSINT/intel), AND
      - the target is real public-routable infrastructure, AND
      - the owner has NOT explicitly opted in.
    """
    if not is_offensive:
        return False
    if target_class is not TargetClass.REAL_PUBLIC_INFRA:
        return False
    return not authorized


def is_live_offensive_authorized(env: dict | None = None) -> bool:
    """Read the explicit opt-in flag (``ALLOW_LIVE_OFFENSIVE``). Default: False."""
    src = env if env is not None else os.environ
    return str(src.get(_ENV_ALLOW_LIVE_OFFENSIVE, "")).strip().lower() in ("1", "true", "yes", "on")


def is_target_authorized(target: str, env: dict | None = None) -> bool:
    """Whether live offensive work against ``target`` is authorized.

    Authorized iff EITHER:
      - the target matches a declared, documented engagement in the pentest scope
        (per-target proof — the multi-tenant path: each org authorizes its OWN
        assets), OR
      - the deployment-wide ``ALLOW_LIVE_OFFENSIVE`` flag is set (single-tenant /
        all-targets path).

    Preferring the scope means a multi-tenant deployment never needs the blanket
    flag, and an out-of-scope target stays blocked even with many tenants declared.
    """
    if target:
        try:
            from app.security.pentest_scope import target_in_scope
            if target_in_scope(target):
                return True
        except Exception:
            pass  # scope lookup must never crash the gate
    return is_live_offensive_authorized(env)


def build_authorized_scope_note(message: str) -> str:
    """Authorization note for the PLANNER when the message's target is in scope.

    Closes the architectural gap (audit fb1a7a3c): plan_task / intent-analysis
    never consulted the declared pentest scope, so the planner could refuse a
    target that the pre-flight gate and the runtime tool gate would have
    authorized — the three layers disagreed. This mirrors the ``infra_context``
    injection in plan_task: it tells the planner, with the authorizing org +
    document on record, that the named target IS covered by a documented
    engagement and may be planned for in good faith.

    Returns "" when there is no infra target, or the target is NOT in scope — so
    nothing changes for benign or genuinely-unauthorized requests (no
    authorization laundering: an out-of-scope target still gets no note and the
    planner stays free to refuse).
    """
    target_class, target = classify_target(message or "")
    if target_class is not TargetClass.REAL_PUBLIC_INFRA or not target:
        return ""
    try:
        from app.security.pentest_scope import matching_entry
        entry = matching_entry(target)
    except Exception:
        return ""
    if not entry:
        return ""
    org = entry.get("org", "") or "(unnamed engagement)"
    document = entry.get("document", "") or "(document on file)"
    return (
        f"## Authorized Engagement Scope\n"
        f"The target **{target}** IS covered by a declared, documented pentest "
        f"engagement on file:\n"
        f"- Authorized by: {org}\n"
        f"- Authorizing document: {document}\n"
        f"This target is authorized — plan the requested security work in good "
        f"faith WITHIN this scope. Do NOT refuse it as unauthorized; the per-target "
        f"authorization exists. (Targets NOT listed in scope remain unauthorized.)"
    )


# Tools that perform an ACTIVE offensive network probe against a host. These are
# the tools that actually reached out and touched third-party infrastructure in
# the audit (e5029e0d scan of hanoi.edu.vn, eb5c09bd live recon). They take an
# explicit host/target arg, so the real target is re-classifiable at call time.
# Deliberately NARROW: ubiquitous research tools (http_request, web_search) and
# passive lookups (dns_lookup) are NOT here — gating those would break normal work.
_OFFENSIVE_NETWORK_TOOLS = frozenset({
    "port_scan",
    "tcp_connect",
    "network_probe",
    "ssh_exec",
})

# Tool-arg keys that carry the host/target, in priority order.
_TARGET_ARG_KEYS = ("host", "target", "ip", "url", "hostname")


def is_offensive_network_tool(tool_name: str) -> bool:
    """Whether ``tool_name`` performs an active offensive network probe."""
    return tool_name in _OFFENSIVE_NETWORK_TOOLS


def extract_tool_target(tool_args: dict) -> str:
    """Pull the host/target string out of a tool's arguments (first key present)."""
    if not isinstance(tool_args, dict):
        return ""
    for key in _TARGET_ARG_KEYS:
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def evaluate_target_authorization(
    target: str, authorized: bool
) -> AuthorizationDecision:
    """Runtime re-gate for a CONCRETE target an offensive tool is about to hit.

    Unlike the pre-flight gate (which classifies the user's MESSAGE), this
    classifies the actual host the tool will contact — closing the
    fabricated-target hole (CC-11). Blocks ⟺ the target is real public-routable
    infrastructure AND the operator has not opted in. Safe/lab/loopback targets
    and the opt-in case pass through.
    """
    target_class, matched = classify_target(target or "")
    # Treat the offensive call itself as the offensive signal (is_offensive=True):
    # we only call this for tools in _OFFENSIVE_NETWORK_TOOLS.
    blocked = requires_authorization(target_class, is_offensive=True, authorized=authorized)
    reason = ""
    if blocked:
        reason = (
            f"Active offensive probe against real public-routable infrastructure "
            f"({matched or target}) requires explicit owner authorization. Blocked by "
            f"default to prevent unauthorized attacks on third-party systems. If you own "
            f"this target or hold written authorization, set {_ENV_ALLOW_LIVE_OFFENSIVE}=1."
        )
    return AuthorizationDecision(
        blocked=blocked,
        target=matched or target,
        target_class=target_class,
        reason=reason,
    )


def evaluate_authorization(
    message: str, is_offensive: bool, authorized: bool
) -> AuthorizationDecision:
    """End-to-end pre-flight decision for a task."""
    target_class, target = classify_target(message)
    blocked = requires_authorization(target_class, is_offensive, authorized)
    if blocked:
        reason = (
            f"Live offensive operation against real public-routable infrastructure "
            f"({target}) requires explicit owner authorization. This is blocked by "
            f"default to prevent unauthorized attacks on third-party systems. To "
            f"proceed on a target you own or are contracted to test, set "
            f"{_ENV_ALLOW_LIVE_OFFENSIVE}=1 and confirm you hold written authorization."
        )
    else:
        reason = ""
    return AuthorizationDecision(
        blocked=blocked,
        target=target,
        target_class=target_class,
        reason=reason,
    )
