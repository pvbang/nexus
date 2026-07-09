"""Tests for resolve_external_url (audit gap C — no fabricated internal IPs).

Pins the priority order and the key behaviour change: in Docker without a public
host, we return the honest detected IP + an actionable hint instead of inventing an
unreachable host.docker.internal / gateway address.
"""

from __future__ import annotations

from app.project_builder.url_resolver import _is_public_ip, resolve_external_url


# ───────────────────────── priority ─────────────────────────


def test_external_base_url_wins():
    url, hint = resolve_external_url(
        port=19000, external_base_url="https://demo.example.com",
        public_host="ignored", detected_ip="1.2.3.4", in_docker=True,
    )
    assert url == "https://demo.example.com:19000"
    assert hint == ""  # explicit config → no hint


def test_public_host_used_when_no_base_url():
    url, hint = resolve_external_url(
        port=8080, external_base_url="", public_host="myvps.io",
        detected_ip="172.17.0.2", in_docker=True,
    )
    assert url == "http://myvps.io:8080"
    assert hint == ""


def test_detected_ip_used_when_no_config():
    url, hint = resolve_external_url(
        port=3000, external_base_url="", public_host="",
        detected_ip="8.8.4.4", in_docker=False,
    )
    assert url == "http://8.8.4.4:3000"
    assert hint == ""  # public IP, not docker → no hint


# ───────────────────────── gap C: no fabricated internal IP ─────────────────────────


def test_docker_internal_ip_gets_hint_not_fabricated_host():
    # In Docker, no config, detected IP is private → honest URL + actionable hint.
    url, hint = resolve_external_url(
        port=19000, external_base_url="", public_host="",
        detected_ip="172.17.0.2", in_docker=True,
    )
    assert url == "http://172.17.0.2:19000"  # honest, not host.docker.internal
    assert "NEXUS_PUBLIC_HOST" in hint
    assert "tunnel" in hint.lower()


def test_docker_with_public_ip_no_hint():
    # In Docker but detected IP is genuinely public → reachable, no hint needed.
    url, hint = resolve_external_url(
        port=19000, external_base_url="", public_host="",
        detected_ip="8.8.4.4", in_docker=True,
    )
    assert url == "http://8.8.4.4:19000"
    assert hint == ""


def test_base_url_trailing_slash_stripped():
    url, _ = resolve_external_url(
        port=19000, external_base_url="https://demo.example.com/",
        public_host="", detected_ip="1.2.3.4", in_docker=False,
    )
    assert url == "https://demo.example.com:19000"


# ───────────────────────── _is_public_ip ─────────────────────────


def test_is_public_ip_classification():
    assert _is_public_ip("8.8.8.8") is True
    assert _is_public_ip("8.8.4.4") is True
    assert _is_public_ip("172.17.0.1") is False   # docker bridge
    assert _is_public_ip("192.168.1.5") is False  # LAN
    assert _is_public_ip("10.0.0.4") is False     # private
    assert _is_public_ip("127.0.0.1") is False    # loopback
    assert _is_public_ip("localhost") is False    # hostname → can't classify → False
    assert _is_public_ip("host.docker.internal") is False
