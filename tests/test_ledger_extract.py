"""Tests for ledger-field extraction (audit C1/C3).

Extracted from pipeline (reached cross-module by turn_executor) with no tests.
These feed the ContextLedger, so their truncation/noise behaviour shapes context
quality (audit C2). Pins: did_what strategies, key_data bullets/URLs/CVEs + 25-cap
+ noise filter, truncated-topics LLM-cap gating.
"""

from __future__ import annotations

from app.orchestration.ledger_extract import (
    extract_did_what,
    extract_key_data,
    extract_truncated_topics,
)
from app.orchestration.models import ToolCall


# ───────────────────────── extract_did_what ─────────────────────────


def test_did_what_short_output_returned_whole():
    assert extract_did_what("Done scanning ports.") == "Done scanning ports."


def test_did_what_empty():
    assert extract_did_what("") == ""


def test_did_what_prefers_summary_header():
    text = "noise " * 100 + "\n## Summary\nFound 3 open ports and a SQL injection.\n"
    out = extract_did_what(text)
    assert "open ports" in out


def test_did_what_skips_json_tool_output_picks_prose():
    text = (
        "intro " * 100 + "\n\n"
        '{"raw": "tool output json"}\n\n'
        "The scan completed and the service is confirmed vulnerable."
    )
    out = extract_did_what(text)
    assert "vulnerable" in out
    assert not out.startswith("{")


# ───────────────────────── extract_key_data ─────────────────────────


def test_key_data_empty():
    assert extract_key_data("", []) == []


def test_key_data_extracts_bullets():
    # Note: extract_key_data keeps only bullets whose cleaned text is > 15 chars,
    # so both bullets here are comfortably above that threshold.
    out = extract_key_data("- Found admin panel at /admin\n- Open port 8080 running jenkins\n", [])
    assert any("admin panel" in d for d in out)
    assert any("8080" in d for d in out)


def test_key_data_filters_noise_bullets():
    out = extract_key_data("- TLS handshake completed with cipher X\n- Real finding: API key leaked\n", [])
    assert any("API key leaked" in d for d in out)
    assert not any("handshake" in d for d in out)


def test_key_data_extracts_urls_and_cves():
    text = "See https://example.com/vuln-report-page and CVE-2024-12345 for details."
    out = extract_key_data(text, [])
    assert any("example.com" in d for d in out)
    assert "CVE-2024-12345" in out


def test_key_data_capped_at_25():
    text = "\n".join(f"- finding number {i} is substantive content here" for i in range(40))
    out = extract_key_data(text, [])
    assert len(out) <= 25


def test_key_data_samples_tool_output():
    tc = ToolCall(tool_name="scan", output="- discovered service nginx 1.18 on host", success=True)
    out = extract_key_data("", [tc])
    assert any("nginx" in d for d in out)


def test_key_data_ignores_failed_tool_output():
    tc = ToolCall(tool_name="scan", output="- secret leaked here", success=False)
    out = extract_key_data("", [tc])
    assert not any("secret leaked" in d for d in out)


# ───────────────────────── extract_truncated_topics ─────────────────────────


def test_truncated_topics_only_for_large_output():
    small = ToolCall(tool_name="t1", output="x" * 100)
    big = ToolCall(tool_name="t2", output="y" * 25000)
    topics = extract_truncated_topics("", [small, big])
    assert any(t.startswith("t2") for t in topics)
    assert not any(t.startswith("t1") for t in topics)


def test_truncated_topics_uses_url_hint():
    big = ToolCall(tool_name="fetch", output="https://target.example.org/path " + "z" * 25000)
    topics = extract_truncated_topics("", [big])
    assert topics and "target.example.org" in topics[0]


def test_truncated_topics_capped_at_5():
    bigs = [ToolCall(tool_name=f"t{i}", output="z" * 25000) for i in range(10)]
    assert len(extract_truncated_topics("", bigs)) <= 5
