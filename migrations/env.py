"""Alembic migration environment.

Targets axiom.platform.db.Base.metadata so autogenerate sees all domain models
once they register against Base. No models exist in Phase 0 — this wires the
migration tooling so the first real schema (Phase 1) has somewhere to land.

Runs in async mode against the AXIOM_DATABASE_URL from settings.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from axiom.platform.config import get_settings
from axiom.platform.db import Base

# Import every module that defines ORM models so they register on
# Base.metadata before autogenerate compares against the database. Without this
# import, autogenerate sees an empty metadata and would propose dropping every
# table. As new domains add tables, register their models module here.
import axiom.domains.credentials.models  # noqa: E402,F401  (import-for-side-effect)
import axiom.domains.execution.models  # noqa: E402,F401  (import-for-side-effect)
import axiom.domains.workflow_authoring.models  # noqa: E402,F401  (import-for-side-effect)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime database URL so migrations use the same config as the app.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a live connection (`alembic upgrade --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
