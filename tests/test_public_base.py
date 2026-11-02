"""Tests for auto-learning the public base URL from request headers.

Lets preview links work without a hand-set EXTERNAL_BASE_URL. Pins: header
derivation (Host / X-Forwarded-Host / -Proto), rejecting internal hosts, and the
config-overrides-learned precedence.
"""

from __future__ import annotations

import pytest

from app.core import public_base
from app.core.public_base import derive_base_url, resolve_public_base


@pytest.fixture(autouse=True)
def _reset_learned():
    public_base._learned_base = ""
    yield
    public_base._learned_base = ""


# ───────────────────────── derive_base_url ─────────────────────────


def test_derive_from_host_header_defaults_https():
    assert derive_base_url({"Host": "nexus.example.com"}) == "https://nexus.example.com"


def test_derive_honours_forwarded_proto():
    assert derive_base_url(
        {"Host": "x.com", "X-Forwarded-Proto": "http"}
    ) == "http://x.com"


def test_forwarded_host_takes_precedence():
    # Behind a proxy: X-Forwarded-Host is the public one, Host is proxy↔app.
    h = {"Host": "internal:8000", "X-Forwarded-Host": "nexus.example.com",
         "X-Forwarded-Proto": "https"}
    assert derive_base_url(h) == "https://nexus.example.com"


def test_forwarded_host_comma_list_takes_first():
    h = {"X-Forwarded-Host": "public.example.com, internal.local"}
    assert derive_base_url(h) == "https://public.example.com"


def test_case_insensitive_headers():
    assert derive_base_url({"host": "x.com", "x-forwarded-proto": "https"}) == "https://x.com"


def test_bare_ip_defaults_http():
    assert derive_base_url({"Host": "203.0.113.9:8000"}) == "http://203.0.113.9:8000"


def test_internal_hosts_rejected():
    for host in ("localhost:3000", "127.0.0.1", "0.0.0.0:8000", "host.docker.internal"):
        assert derive_base_url({"Host": host}) == "", host


def test_no_host_returns_empty():
    assert derive_base_url({}) == ""
    assert derive_base_url({"Host": ""}) == ""


# ───────────────────────── resolve precedence ─────────────────────────


def test_external_base_url_wins_over_learned():
    public_base.remember_base_url("https://learned.example.com")
    assert resolve_public_base("https://config.example.com", "") == "https://config.example.com"


def test_public_host_wins_over_learned():
    public_base.remember_base_url("https://learned.example.com")
    assert resolve_public_base("", "vps.example.com") == "https://vps.example.com"


def test_falls_back_to_learned_when_no_config():
    public_base.remember_base_url("https://nexus.example.com")
    assert resolve_public_base("", "") == "https://nexus.example.com"


def test_empty_when_nothing_available():
    assert resolve_public_base("", "") == ""


def test_remember_ignores_empty():
    public_base.remember_base_url("https://x.com")
    public_base.remember_base_url("")  # must not wipe a good value
    assert resolve_public_base("", "") == "https://x.com"
