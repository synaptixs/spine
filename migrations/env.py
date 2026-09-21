"""Alembic environment.

Resolves the DB URL from the DATABASE_URL env var when present; falls
back to the value in alembic.ini for local development.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from orchestrator.registry.db.models import Base

config = context.config

# `fileConfig` defaults to `disable_existing_loggers=True`, so configuring Alembic's logging
# silently sets `disabled=True` on every logger created before this line. Harmless for the CLI,
# where the migration is the whole process — and not harmless for a test session, where the
# migration runs once in a fixture and every later test finds its own loggers dead. Guarded the
# way Alembic's own template does; the default is `True`, so `alembic upgrade` is unchanged.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

if (url := os.getenv("DATABASE_URL")) is not None:
    config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
