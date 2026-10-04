"""Alembic environment: URL comes from DATABASE_URL via x_insight.db."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_insight import db as db_module  # noqa: E402
from x_insight.identity.tables import metadata as identity_metadata  # noqa: E402
from x_insight.operations.audit import metadata as audit_metadata  # noqa: E402

config = context.config

# The migration entry point (`make migrate`): runtime DATABASE_URL wins, the
# ini placeholder only covers a bare local checkout.
config.set_main_option("sqlalchemy.url", db_module.get_database_url())

if config.config_file_name is not None and os.path.exists(config.config_file_name):
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

# Combined metadata for autogenerate; upgrades run from version files.
target_metadata = [audit_metadata, identity_metadata]


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
