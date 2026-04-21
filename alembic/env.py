from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _get_database_url() -> str:
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return _normalize_sqlalchemy_url(env_url)
    fallback_url = _normalize_sqlalchemy_url(config.get_main_option("sqlalchemy.url"))
    if "://user:password@" in fallback_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to your PostgreSQL connection string "
            "before running Alembic."
        )
    return fallback_url


def _normalize_sqlalchemy_url(url: str) -> str:
    # Project standard is psycopg (v3). Normalize generic postgres URLs so
    # SQLAlchemy does not try to import psycopg2.
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_get_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
