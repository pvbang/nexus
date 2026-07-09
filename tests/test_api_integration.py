"""Integration tests for the API layer (audit C3 — the API had ~0 tests).

These drive the real FastAPI app / real endpoint code WITHOUT a live DB or LLM:
  - TestClient(app) is used WITHOUT a `with` block, so the lifespan (DB/Redis boot)
    does NOT run — only pure HTTP routes are exercised.
  - The terminal auth guard and request models are pure and tested directly.

This is the first integration coverage of the API surface; heavier endpoints
(chat/tasks/sessions) need DB fixtures and are out of scope for this pass.
"""

from __future__ import annotations

import os

import pytest

# Ensure a dev, no-auth environment before importing the app.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("TERMINAL_API_KEY", "")


# ───────────────────────── /health (real route via TestClient) ─────────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    # No `with` → lifespan (DB/Redis) is NOT started; only HTTP routing runs.
    return TestClient(app)


def test_health_returns_200_and_status(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert "version" in body and "app" in body


def test_unknown_route_not_500(client):
    # An unmatched API route must not blow up with a server error.
    r = client.get("/api/v1/__definitely_not_a_route__")
    assert r.status_code in (404, 405)


def test_metrics_endpoint_returns_prometheus_text(client):
    # Audit C4: /metrics must serve scrapeable Prometheus text exposition format.
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # Exposition format always carries HELP/TYPE headers; our circuit metrics
    # are always present (even with no circuits, the family header is emitted).
    assert "# TYPE nexus_circuit_breaker_state gauge" in r.text
    assert r.text.endswith("\n")


# ───────────────────────── terminal auth guard (pure dependency) ─────────────────────────


@pytest.mark.asyncio
async def test_terminal_auth_allows_when_no_key_configured(monkeypatch):
    import app.api.v1.endpoints.terminal as term
    import app.config as cfg
    monkeypatch.setenv("TERMINAL_API_KEY", "")
    cfg.get_settings.cache_clear()
    # No key → dev mode → returns without raising.
    assert await term.require_terminal_auth(x_terminal_key="anything") is None
    cfg.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_terminal_auth_rejects_wrong_key(monkeypatch):
    import app.api.v1.endpoints.terminal as term
    import app.config as cfg
    from fastapi import HTTPException
    monkeypatch.setenv("TERMINAL_API_KEY", "secret-123")
    cfg.get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        await term.require_terminal_auth(x_terminal_key="wrong")
    assert exc.value.status_code == 403
    cfg.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_terminal_auth_accepts_correct_key(monkeypatch):
    import app.api.v1.endpoints.terminal as term
    import app.config as cfg
    monkeypatch.setenv("TERMINAL_API_KEY", "secret-123")
    cfg.get_settings.cache_clear()
    assert await term.require_terminal_auth(x_terminal_key="secret-123") is None
    cfg.get_settings.cache_clear()


# ───────────────────────── request model validation (Pydantic) ─────────────────────────


def test_set_profile_request_rejects_empty():
    from pydantic import ValidationError
    from app.api.v1.endpoints.chat import SetProfileRequest
    with pytest.raises(ValidationError):
        SetProfileRequest(profile_name="")  # min_length=1


def test_set_profile_request_rejects_too_long():
    from pydantic import ValidationError
    from app.api.v1.endpoints.chat import SetProfileRequest
    with pytest.raises(ValidationError):
        SetProfileRequest(profile_name="x" * 65)  # max_length=64


def test_set_profile_request_accepts_valid():
    from app.api.v1.endpoints.chat import SetProfileRequest
    req = SetProfileRequest(profile_name="default")
    assert req.profile_name == "default"


# ───────────────────────── preview reverse-proxy (router-level) ─────────────────────────


def test_preview_blocks_non_dev_port(client):
    # SSRF guard: a non-dev port (e.g. Postgres 5432) must be refused, not proxied.
    r = client.get("/preview/5432/")
    assert r.status_code == 403
    assert "not allowed" in r.text.lower()


def test_preview_dev_port_no_server_returns_502(client):
    # Allowed port but nothing listening → 502 with a clear message (not a crash).
    r = client.get("/preview/19000/")
    assert r.status_code == 502
    assert "no app is responding" in r.text.lower()
