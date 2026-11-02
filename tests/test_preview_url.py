"""Tests for preview-URL extraction (audit gap B — link must reach the user).

Pins: reachable URLs are surfaced, internal-only ones (localhost / Docker / private
IP) are dropped, public tunnel URLs rank first, and the banner is built correctly.
"""

from __future__ import annotations

from app.orchestration.preview_url import build_preview_banner, extract_preview_urls


# ───────────────────────── extraction ─────────────────────────


def test_extracts_public_url():
    text = "Dev server started! Public URL (tunnel): https://abc-def.trycloudflare.com"
    assert extract_preview_urls(text) == ["https://abc-def.trycloudflare.com"]


def test_drops_localhost_and_loopback():
    text = "Internal: http://localhost:19000 and http://127.0.0.1:19000 and http://0.0.0.0:8000"
    assert extract_preview_urls(text) == []


def test_drops_docker_internal_and_private_ips():
    text = (
        "http://host.docker.internal:19000 "
        "http://172.18.0.5:19000 "
        "http://10.0.0.4:8000 "
        "http://192.168.1.20:3000"
    )
    assert extract_preview_urls(text) == []


def test_public_tunnel_ranked_before_other():
    text = (
        "External URL: https://myvps.example.com:8080\n"  # non-dev port → kept
        "Public URL (tunnel): https://x.trycloudflare.com\n"
    )
    urls = extract_preview_urls(text)
    assert urls[0] == "https://x.trycloudflare.com"  # tunnel first
    assert "https://myvps.example.com:8080" in urls


def test_dedupes_and_strips_trailing_punctuation():
    text = "See https://x.trycloudflare.com. Again: https://x.trycloudflare.com"
    assert extract_preview_urls(text) == ["https://x.trycloudflare.com"]


def test_no_urls_returns_empty():
    assert extract_preview_urls("just prose, nothing here") == []
    assert extract_preview_urls("") == []


def test_drops_dead_domain_port_url_but_keeps_preview_path():
    # https://domain:19000 is dead behind the reverse proxy; the /preview/ path is
    # the reachable one. Only the path form must survive.
    text = (
        "External URL (for user access): https://nexus.example.com:19000\n"
        "Preview URL (via domain): https://nexus.example.com/preview/19000/\n"
    )
    urls = extract_preview_urls(text)
    assert "https://nexus.example.com/preview/19000/" in urls
    assert "https://nexus.example.com:19000" not in urls


def test_keeps_normal_domain_port_outside_dev_range():
    # A non-dev port (e.g. a real published service on :8443) is NOT dropped.
    urls = extract_preview_urls("App at https://demo.example.com:8443/app")
    assert "https://demo.example.com:8443/app" in urls


# ───────────────────────── banner ─────────────────────────


def test_banner_empty_when_no_reachable_url():
    assert build_preview_banner("http://localhost:8000 only") == ""


def test_banner_single_url():
    banner = build_preview_banner("Public URL (tunnel): https://x.trycloudflare.com")
    assert "🌐 Live demo" in banner
    assert "https://x.trycloudflare.com" in banner


def test_banner_multiple_urls_as_list():
    text = "https://a.trycloudflare.com and https://b.example.com:8080"
    banner = build_preview_banner(text)
    assert banner.count("- ") == 2
    # Tunnel URL listed first.
    assert banner.index("a.trycloudflare.com") < banner.index("b.example.com")
