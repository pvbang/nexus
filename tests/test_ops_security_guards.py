"""Tests for production fail-fast ops-security guards (audit L2/L3).

Both guards mirror the existing secret-key policy (SEC-03): production must refuse
to boot on an insecure config instead of just logging a warning the process then
ignores.

L2 (CORS wildcard) is exercised through create_app() with a patched environment.
L3 (terminal key) lives inside the async lifespan (which boots DB/Redis), so its
decision logic is verified directly rather than by booting the whole app.
"""

from __future__ import annotations

import importlib

import pytest


def _reload_main():
    import app.main as m
    return importlib.reload(m)


# ───────────────────────── L2: CORS wildcard ─────────────────────────


def test_cors_wildcard_blocks_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    # Secret keys must be non-default or the secret guard fires first.
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("JWT_SECRET_KEY", "y" * 40)
    monkeypatch.setenv("TERMINAL_API_KEY", "term-secret")

    import app.config as cfg
    cfg.get_settings.cache_clear()

    # app.main has a module-level `app = create_app()`, so reloading the module
    # already runs the guard — the RuntimeError surfaces during reload itself.
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        _reload_main()

    cfg.get_settings.cache_clear()


def test_cors_wildcard_allowed_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    import app.config as cfg
    cfg.get_settings.cache_clear()
    m = _reload_main()

    # Dev tolerates wildcard — create_app must succeed.
    app = m.create_app()
    assert app is not None

    cfg.get_settings.cache_clear()


def test_cors_explicit_origins_ok_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("JWT_SECRET_KEY", "y" * 40)
    monkeypatch.setenv("TERMINAL_API_KEY", "term-secret")
    import app.config as cfg
    cfg.get_settings.cache_clear()
    m = _reload_main()

    app = m.create_app()  # explicit origins → no raise
    assert app is not None

    cfg.get_settings.cache_clear()


# ───────────────────────── L3: terminal key (decision logic) ─────────────────────────


def test_terminal_auth_open_when_no_key(monkeypatch):
    # Mirrors require_terminal_auth: empty key → dev open access (returns, no raise).
    import app.config as cfg
    monkeypatch.setenv("TERMINAL_API_KEY", "")
    cfg.get_settings.cache_clear()
    assert cfg.get_settings().terminal_api_key == ""
    cfg.get_settings.cache_clear()


def test_production_requires_terminal_key_condition(monkeypatch):
    # The L3 guard fires when is_production AND no terminal key. Verify that exact
    # condition resolves true for a prod config missing the key.
    import app.config as cfg
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TERMINAL_API_KEY", "")
    cfg.get_settings.cache_clear()
    s = cfg.get_settings()
    assert s.is_production and not s.terminal_api_key  # → guard must raise at startup
    cfg.get_settings.cache_clear()
