"""Tests for L1: alembic DB URL resolution prefers env over hardcoded ini.

Audit §4 L1 — alembic.ini shipped a real-looking password in sqlalchemy.url as a
"local dev fallback". The resolver now prefers, in order:
  1. DATABASE_URL (async → sync normalized)
  2. a URL composed from POSTGRES_* env vars (matches docker-compose)
  3. the alembic.ini fallback (now a non-secret placeholder)
so deployments never depend on a committed credential.

Pure function: no DB, no IO.
"""

from __future__ import annotations

from app.core.db_url import resolve_db_url as _resolve_db_url


def test_database_url_wins_and_is_normalized():
    env = {"DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/db"}
    assert _resolve_db_url(env, ini_url="ignored") == "postgresql://u:p@h:5432/db"


def test_composes_from_postgres_env_when_no_database_url():
    env = {
        "POSTGRES_USER": "mekongai",
        "POSTGRES_PASSWORD": "s3cret",
        "POSTGRES_DB": "mekongai_nexus",
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5433",
    }
    assert _resolve_db_url(env, ini_url="x") == (
        "postgresql://mekongai:s3cret@postgres:5433/mekongai_nexus"
    )


def test_postgres_env_defaults_host_and_port():
    env = {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p", "POSTGRES_DB": "d"}
    assert _resolve_db_url(env, ini_url="x") == "postgresql://u:p@localhost:5432/d"


def test_falls_back_to_ini_when_no_env():
    assert _resolve_db_url({}, ini_url="postgresql://ini@localhost/db") == (
        "postgresql://ini@localhost/db"
    )


def test_partial_postgres_env_does_not_compose():
    # Missing password → can't safely compose → fall through to ini.
    env = {"POSTGRES_USER": "u", "POSTGRES_DB": "d"}
    assert _resolve_db_url(env, ini_url="postgresql://ini@localhost/db") == (
        "postgresql://ini@localhost/db"
    )
