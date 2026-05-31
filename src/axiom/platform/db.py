"""Database engine and session factory (async SQLAlchemy 2.0).

Postgres is the source of truth (ADR-0001). This module owns the async engine
and the session factory; it holds NO domain models — those live with their
domains and register against `Base`.

Audit note R3 (SECURITY.md §4.2): when Row-Level Security is enabled in Phase 1,
the per-request org_id GUC must be set strictly per-transaction to avoid bleeding
tenancy context across pooled async connections. The session dependency is the
intended single place to do that; it is left as a Phase-1 TODO here rather than
faked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from axiom.platform.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models across domains.

    Domain models import and subclass this. Alembic's autogenerate targets
    `Base.metadata`. No models are defined in Phase 0 — this is the registry
    they will attach to.
    """


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            echo=settings.db_echo,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session scoped to a unit of work.

    Intended as the FastAPI dependency and the worker's per-node session source.
    In Phase 1 this is where the RLS org_id GUC is set per-transaction (audit R3).
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose of the engine's connection pool. Called on graceful shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
