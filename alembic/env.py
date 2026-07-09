"""Alembic migrations environment."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.models.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    """Get database URL from env (DATABASE_URL / POSTGRES_*) or alembic.ini.

    Resolution order (see app.core.db_url): DATABASE_URL → composed POSTGRES_*
    → alembic.ini placeholder. Keeps real credentials out of the committed
    alembic.ini (audit L1). Async URLs are normalized to sync for Alembic.
    """
    from app.core.db_url import resolve_db_url
    return resolve_db_url(
        os.environ,
        ini_url=config.get_main_option("sqlalchemy.url", ""),
    )


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _get_database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
