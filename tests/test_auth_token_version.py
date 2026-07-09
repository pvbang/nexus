"""Tokens must carry a version claim; bumping the version revokes old tokens."""

import importlib


def _reload_auth(monkeypatch, version: int):
    monkeypatch.setenv("JWT_TOKEN_VERSION", str(version))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-not-default")
    import app.config as cfg
    cfg.get_settings.cache_clear()
    import app.core.auth as auth
    importlib.reload(auth)
    return auth


def test_token_carries_version(monkeypatch):
    auth = _reload_auth(monkeypatch, 3)
    token = auth.create_access_token(user_id="u1")
    payload = auth.verify_token(token)
    assert payload is not None
    assert payload["ver"] == 3
    assert payload["sub"] == "u1"


def test_bumping_version_revokes_old_token(monkeypatch):
    auth = _reload_auth(monkeypatch, 1)
    old_token = auth.create_access_token(user_id="u1")
    assert auth.verify_token(old_token) is not None

    auth2 = _reload_auth(monkeypatch, 2)
    # Old token was signed with ver=1; current expected version is 2 → rejected.
    assert auth2.verify_token(old_token) is None
