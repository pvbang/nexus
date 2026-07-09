"""Tests for preview reverse-proxy logic (agent-hosted app via project domain).

Pure logic: SSRF port guard, <base href> injection (SPA asset fix), redirect
Location rewriting. These are the correctness-critical, easy-to-get-wrong bits.
"""

from __future__ import annotations

from app.api.preview_proxy import (
    PREVIEW_PORT_MAX,
    PREVIEW_PORT_MIN,
    build_upstream_url,
    inject_base_href,
    is_proxyable_port,
    preview_prefix,
    rewrite_location_header,
)


# ───────────────────────── SSRF port guard ─────────────────────────


def test_dev_server_ports_allowed():
    assert is_proxyable_port(PREVIEW_PORT_MIN)
    assert is_proxyable_port(PREVIEW_PORT_MAX)
    assert is_proxyable_port(19025)


def test_non_dev_ports_blocked():
    # Postgres/Redis/app/SSH etc. must NOT be proxyable (SSRF guard).
    for p in (22, 80, 443, 5432, 6379, 8000, 8899, 18800, 1, 65535, 18999, 19050):
        assert not is_proxyable_port(p), f"port {p} should be blocked"


# ───────────────────────── upstream URL ─────────────────────────


def test_build_upstream_url():
    assert build_upstream_url(19000, "api/foo") == "http://127.0.0.1:19000/api/foo"
    assert build_upstream_url(19000, "/api/foo") == "http://127.0.0.1:19000/api/foo"
    assert build_upstream_url(19000, "") == "http://127.0.0.1:19000/"


def test_build_upstream_url_with_query():
    assert build_upstream_url(19000, "search", "q=hi&n=2") == \
        "http://127.0.0.1:19000/search?q=hi&n=2"


# ───────────────────────── base href injection (SPA fix) ─────────────────────────


def test_inject_base_href_after_head():
    html = b"<html><head><title>App</title></head><body>hi</body></html>"
    out = inject_base_href(html, 19000)
    assert b'<base href="/preview/19000/">' in out
    # Injected right after <head>, before <title>.
    assert out.index(b"<base") < out.index(b"<title>")


def test_inject_base_href_respects_existing_base():
    html = b'<html><head><base href="/custom/"><title>x</title></head></html>'
    out = inject_base_href(html, 19000)
    assert out.count(b"<base") == 1  # not double-injected
    assert b"/preview/19000/" not in out


def test_inject_base_href_no_head_unchanged():
    html = b"<div>fragment without head</div>"
    assert inject_base_href(html, 19000) == html


def test_inject_base_href_case_insensitive_head():
    html = b"<HTML><HEAD></HEAD></HTML>"
    assert b"<base" in inject_base_href(html, 19000)


def test_inject_base_href_does_not_match_header_tag():
    # <header> must NOT be mistaken for <head>; with no real <head>, return unchanged.
    html = b"<html><body><header>nav</header><h1>hi</h1></body></html>"
    assert inject_base_href(html, 19000) == html


def test_inject_base_href_with_head_attributes():
    html = b'<html><head lang="en"><title>x</title></head></html>'
    out = inject_base_href(html, 19000)
    assert b'<base href="/preview/19000/">' in out


# ───────────────────────── Location header rewrite ─────────────────────────


def test_rewrite_absolute_path_redirect():
    assert rewrite_location_header("/login", 19000) == "/preview/19000/login"


def test_rewrite_loopback_absolute_url():
    assert rewrite_location_header("http://127.0.0.1:19000/dashboard", 19000) == \
        "/preview/19000/dashboard"


def test_rewrite_already_prefixed_unchanged():
    assert rewrite_location_header("/preview/19000/page", 19000) == "/preview/19000/page"


def test_rewrite_external_url_unchanged():
    # A redirect to a genuinely external site must be left alone.
    assert rewrite_location_header("https://google.com", 19000) == "https://google.com"


def test_rewrite_empty_location():
    assert rewrite_location_header("", 19000) == ""


def test_preview_prefix():
    assert preview_prefix(19000) == "/preview/19000/"
