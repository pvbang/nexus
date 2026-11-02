"""
Nexus - Database URL resolution for Alembic.

Pure helper (no DB, no I/O) so Alembic's sync migration URL is resolved from the
environment first and never depends on a credential committed to alembic.ini
(audit L1). Resolution order:

  1. DATABASE_URL — the canonical app URL (async driver normalized to sync).
  2. POSTGRES_* env vars — compose a URL (matches docker-compose / .env.example).
  3. ini fallback — a non-secret placeholder for bare local dev.
"""

from __future__ import annotations

from collections.abc import Mapping


def resolve_db_url(env: Mapping[str, str], ini_url: str = "") -> str:
    """Resolve the sync SQLAlchemy URL for Alembic from `env`, else `ini_url`.

    `env` is typically os.environ. Async drivers (`+asyncpg`) are normalized to
    the sync driver Alembic uses. Composing from POSTGRES_* requires at least
    user + password + db; a partial set falls through to `ini_url`.
    """
    database_url = env.get("DATABASE_URL", "")
    if database_url:
        return database_url.replace("+asyncpg", "")

    user = env.get("POSTGRES_USER")
    password = env.get("POSTGRES_PASSWORD")
    db = env.get("POSTGRES_DB")
    if user and password and db:
        host = env.get("POSTGRES_HOST", "localhost")
        port = env.get("POSTGRES_PORT", "5432")
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    return ini_url
