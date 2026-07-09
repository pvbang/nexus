"""Extract structured ledger fields from agent output + tool results.

Extracted from ``pipeline`` (audit C1: shrink the 2026-line file; also removes the
cross-module reach where ``turn_executor`` imported a private ``_extract_key_data``
out of ``pipeline``). Pure functions over text/tool-call records — no ``self``, no
I/O — so they're directly unit-testable. They feed the ContextLedger, so their
truncation/noise-filtering behaviour directly shapes downstream context quality
(audit C2).

Each ``tool_calls`` entry is a ``ToolCall`` with ``.tool_name``, ``.arguments``,
``.output``, ``.success``.
"""

from __future__ import annotations

import re

# smart_truncate now lives in the shared text_budget toolkit (single source of
# truth). Re-exported here so the existing importers of
# ``ledger_extract.smart_truncate`` keep working unchanged.
from app.orchestration.text_budget import smart_truncate  # noqa: F401 (re-exported)


# Verbose/debug lines that are NOT findings (curl traces, TLS handshake, etc.).
_NOISE_PATTERNS = (
    "TLS", "handshake", "cipher", "CAfile", "CApath",
    "Trying ", "Connected to", "ALPN:", "shutting down",
    "TCP_NODELAY", "schannel", "expire in",
    "upload completely sent", "Mark bundle as",
)

# Tool outputs above this size are truncated in LLM context — record a search hint.
_LLM_CAP = 20000


def extract_did_what(output: str) -> str:
    """Extract a meaningful 'did_what' summary from agent output.

    Strategy (priority order):
    1. Named DONE-work section (## ACCOMPLISHED / Conclusion / Kết quả …).
    1b. Explicit summary markers via regex (## Summary, FINAL ANSWER).
    2. The LAST paragraph that reads like a conclusion (not tool output).
    3. Fallback: first meaningful sentence from the tail.

    Avoids raw tool output, HTTP status lines, JSON fragments.
    """
    if not output:
        return ""
    text = output.strip()
    if len(text) <= 500:
        return text

    # Strategy 1: named-section preference — the engine prompts agents for
    # ## ACCOMPLISHED / KEY FINDINGS / STILL MISSING (agent_worker.py). Prefer the
    # DONE-work section (accomplished/conclusion, incl. VI headings) so did_what
    # records what was COMPLETED, never the STILL MISSING todo — which the tail
    # heuristic used to capture (audit tri-model 2026-07-01).
    from app.orchestration.text_budget import parse_output_sections
    _secs = parse_output_sections(text)
    for _key in ("accomplished", "conclusion"):
        _body = _secs.get(_key, "").strip()
        if len(_body) > 20 and not _body.startswith(("{", "[")):
            return smart_truncate(_body, 500)

    # Strategy 1b: Explicit summary section headers (legacy regex, kept).
    summary_patterns = [
        r"##\s*(?:Summary|Conclusion|Final|Result)[^\n]*\n([\s\S]{20,500}?)(?:\n##|\Z)",
        r"(?:FINAL ANSWER|CONCLUSION|SUMMARY)[:\s]*\n?([\s\S]{20,500}?)(?:\n##|\n\n\n|\Z)",
    ]
    for pattern in summary_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if len(candidate) > 30 and not candidate.startswith(("{", "[")):
                return smart_truncate(candidate, 500)

    # Strategy 2: Last paragraph that looks like prose (not tool output).
    paragraphs = re.split(r"\n\s*\n", text)
    for para in reversed(paragraphs[-5:]):
        para = para.strip()
        if len(para) < 30:
            continue
        if para.startswith(("{", "[", "HTTP/", "Status", "<!DOCTYPE")):
            continue
        if re.match(r"^\d{3}\s", para):  # HTTP status codes
            continue
        if len(para) <= 500:
            return para
        last_period = para[:500].rfind(". ")
        if last_period > 100:
            return para[:last_period + 1]
        return smart_truncate(para, 500)

    # Strategy 3: Fallback — first sentence from last 600 chars.
    # Drop the leading partial word: text[-600:] almost always starts mid-word, and
    # if the tail has no sentence terminator the whole fragment was returned starting
    # mid-word ("n Antigravity thành..."). Cut to the first space so the summary
    # begins on a real word.
    tail = text[-600:]
    # text[-600:] almost always begins mid-word. Drop everything up to the first
    # space so the summary starts on a whole word — regardless of how long that
    # leading fragment is (a long no-space token like minified JSON/base64 would
    # otherwise slip a mid-word start through; the old `< 40` guard missed it).
    _first_space = tail.find(" ")
    if _first_space > 0:
        tail = tail[_first_space + 1:]
    sentences = re.split(r"(?<=[.!?])\s+", tail)
    for sent in reversed(sentences):
        sent = sent.strip()
        if len(sent) > 20 and not sent.startswith(("{", "[", "HTTP")):
            return smart_truncate(sent, 500)

    return smart_truncate(tail.strip(), 300)


def extract_still_missing(output: str) -> str:
    """Extract the agent's STILL-MISSING / next-step handoff (W-1/W-2).

    did_what deliberately keeps only ACCOMPLISHED. The STILL MISSING section (what's
    left, the handoff to the next turn) had no home — blockers only got a crash
    string — so "còn nợ / cần làm gì tiếp" was lost at write time. This captures it
    (EN + VI headings, incl. "next step/next focus/remaining/còn thiếu") for the
    ledger blockers field so the next turn knows what remains. Word-bounded, ≤600.
    """
    if not output:
        return ""
    from app.orchestration.text_budget import parse_output_sections
    body = parse_output_sections(output).get("still_missing", "").strip()
    if len(body) < 3:
        return ""
    # A "nothing left" note is not an outstanding item — do not surface it.
    if re.fullmatch(r"(?i)\W*(none|n/?a|nothing( left)?|-|✓|đã xong|không có)\W*", body):
        return ""
    return smart_truncate(body, 600)


def extract_key_data(output: str, tool_calls: list) -> list[str]:
    """Extract key data points (bullets, URLs, CVEs) from output + tool results.

    Returns up to 25 compact, de-duplicated data points, filtering verbose
    debug/noise lines.
    """
    if not output and not tool_calls:
        return []

    key_data: list[str] = []
    text = output or ""

    # Sample up to 800 chars of each successful tool output.
    for tc in tool_calls[:10]:
        if tc.success and tc.output:
            text += "\n" + tc.output[:800]

    # Bullet/dash lines with substantive content (likely findings). W-2 (context-
    # arch audit): collect ALL bullets first, THEN prioritize handoff/action facts to
    # the front, THEN cap — instead of returning at the first 15 in document order
    # (which kept the ✅-achievements the agent writes first and dropped the
    # ❌/error-location facts it writes last, the very handoff the next turn needs).
    _bullets: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("-", "•", "*", "✓", "✅", "❌", "✗")) and len(line) > 20:
            clean = line.lstrip("-•*✓✅❌✗ ").strip()
            if any(noise in clean for noise in _NOISE_PATTERNS):
                continue
            if len(clean) > 15 and clean not in _bullets:
                # C-6: word-boundary truncation, not a hard [:200] mid-word cut.
                _bullets.append(smart_truncate(clean, 240))
    if _bullets:
        from app.orchestration.context_ledger import prioritize_handoff_key_data
        for kd in prioritize_handoff_key_data(_bullets, limit=15):
            if kd not in key_data:
                key_data.append(kd)

    # URLs (important references).
    urls = re.findall(r"https?://[^\s\)\"'<>]{10,100}", text[:5000])
    for url in urls[:3]:
        if url not in key_data:
            key_data.append(url)

    # CVE IDs.
    cves = re.findall(r"CVE-\d{4}-\d{4,}", text[:5000])
    for cve in cves[:3]:
        if cve not in key_data:
            key_data.append(cve)

    return key_data[:25]


def extract_truncated_topics(output: str, tool_calls: list) -> list[str]:
    """Search-hint topics for tool outputs that were large (truncated in context).

    Returns short keywords/phrases future agents can pass to ``activity_search()``
    to recover full data cut from context. Only records topics for tools whose
    output exceeded the LLM cap.
    """
    topics: list[str] = []

    for tc in tool_calls:
        if not tc.output or len(tc.output) <= _LLM_CAP:
            continue
        hint = tc.tool_name
        url_match = re.search(r"https?://([^/\s]{5,40})", tc.output[:2000])
        if url_match:
            hint = f"{tc.tool_name}:{url_match.group(1)}"
        elif tc.arguments:
            for v in tc.arguments.values():
                if isinstance(v, str) and 5 < len(v) < 80:
                    hint = f"{tc.tool_name}:{v[:40]}"
                    break
        topics.append(hint)

    return topics[:5]
