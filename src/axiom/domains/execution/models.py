"""SQLAlchemy ORM models for the execution domain (DOMAIN_MODEL.md §4).

These are the *persistence* shape of the engine's state — distinct from the pure
value objects in ``state.py``, which the scheduler operates on. The Postgres
store (``store_pg.py``) maps between the two. Keeping them separate means the
engine logic never touches the ORM, stays trivially unit-testable against the
in-memory store, and the schema can evolve without rippling into scheduling code.

They attach to the shared ``Base`` (``axiom.platform.db``) so Alembic's
autogenerate sees them on ``Base.metadata``. Tenancy: every table carries
``org_id`` (the multi-tenant spine, DOMAIN_MODEL.md §1). The orchestrator's claim
loop is a privileged system operation that spans orgs, so it does not run under a
per-request RLS GUC; user-facing reads (Phase 1 API) will. The ``org_id`` columns
are what RLS will key on when it is switched on (SECURITY.md §4.2 / db.py audit R3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from axiom.platform.db import Base


class ExecutionRow(Base):
    """A run (DOMAIN_MODEL.md §4 — execution)."""

    __tablename__ = "execution"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    total_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    nodes: Mapped[list[NodeStateRow]] = relationship(
        back_populates="execution", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # History queries: a tenant's runs of a workflow, newest first.
        Index("ix_execution_org_workflow_started", "org_id", "workflow_id", "started_at"),
        # Scheduler/reaper hot path: only the runs that are still live.
        Index(
            "ix_execution_active",
            "status",
            postgresql_where=status.in_(("queued", "running")),
        ),
    )


class NodeStateRow(Base):
    """One node per run — the orchestrator's unit of work (DOMAIN_MODEL.md §4)."""

    __tablename__ = "node_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Crash-recovery lease — the reaper resets RUNNING rows whose lease expired.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Backoff gate — a READY-for-retry row is not claimable before this time.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    execution: Mapped[ExecutionRow] = relationship(back_populates="nodes")

    __table_args__ = (
        # One row per (run, graph node) — the idempotency spine.
        UniqueConstraint("execution_id", "node_id", name="uq_node_state_exec_node"),
        # The claim + reap hot path: find claimable / expired rows fast.
        Index("ix_node_state_claim", "status", "not_before"),
        Index("ix_node_state_lease", "status", "lease_until"),
    )


class ExecutionEventRow(Base):
    """The append-only execution event log (DOMAIN_MODEL.md §4).

    Source of truth for run history, the live viewer, and audit. The autoincrement
    ``id`` is the global monotonic total order; ``seq`` is a per-execution,
    best-effort ordinal for human-friendly display (ordering authority is ``id``).
    """

    __tablename__ = "execution_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(128))
    attempt: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Replaying a run's log: filter by execution, order by global id.
        Index("ix_execution_event_exec_id", "execution_id", "id"),
        # Audit / cost projections scan by org over time.
        Index("ix_execution_event_org_created", "org_id", "created_at"),
    )
