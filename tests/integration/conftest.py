"""Fixtures for tests that need a live Postgres (integration + chaos lanes).

These build an async engine + session factory from the configured
``AXIOM_DATABASE_URL`` (the Makefile points the integration/chaos lanes at the
isolated test datastore on port 55432), ensure the schema exists via
``Base.metadata``, and truncate the engine's tables so each test starts clean.
No Alembic here — schema creation from metadata is faster and the migration's
own correctness is verified separately (it round-trips in CI).

Scope note: the engine fixture is **function-scoped with NullPool**. Under
pytest-asyncio's auto mode each test runs in its own event loop, and a pooled
asyncpg connection bound to a previous loop fails with "another operation is in
progress". Function scope + NullPool means every test gets a fresh connection on
its own loop — slightly slower, unambiguously correct for the handful of
DB-backed tests here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# Importing the models registers them on Base.metadata so create_all builds them.
import axiom.domains.credentials.models
import axiom.domains.execution.models
import axiom.domains.workflow_authoring.models  # noqa: F401
from axiom.platform.config import get_settings
from axiom.platform.db import Base

# All engine + authoring + credential tables, in FK-safe truncation order
# (children first).
_ENGINE_TABLES = (
    "execution_event",
    "node_state",
    "execution",
    "workflow_version",
    "workflow",
    "credential",
)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """A function-scoped async engine against the test database (NullPool).

    Skips the lane cleanly if the database is unreachable, so running the
    integration/chaos lanes without ``make test-stack-up`` fails loud-but-clear
    rather than hanging.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - environment guard
        await engine.dispose()
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    pg_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A per-test session factory; truncates engine tables before the test."""
    async with pg_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_ENGINE_TABLES)} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)
