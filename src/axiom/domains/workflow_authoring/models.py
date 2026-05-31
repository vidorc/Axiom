"""SQLAlchemy ORM models for the workflow authoring domain (DOMAIN_MODEL.md §3).

Authoring is the design-time half of the system: a ``workflow`` is the mutable
container a user edits; a ``workflow_version`` is an *immutable, append-only*
snapshot of its graph. Execution pins a ``workflow_version_id`` when a run starts,
so editing a workflow can never alter a run already in flight (DOMAIN_MODEL.md §3
— "versioning lives here so a running execution pins the exact definition it
started with").

These attach to the shared ``Base`` so Alembic autogenerate sees them. Tenancy:
``org_id`` on the workflow (the version inherits scope through its workflow).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from axiom.platform.db import Base


class WorkflowRow(Base):
    """The mutable workflow container (DOMAIN_MODEL.md §3 — workflow)."""

    __tablename__ = "workflow"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    folder: Mapped[str | None] = mapped_column(String(256))
    # The version a manual run uses by default. Nullable until the first version
    # is created (a chicken-and-egg the AuthoringStore resolves in one unit of work).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_workflow_org", "org_id"),)


class WorkflowVersionRow(Base):
    """An immutable workflow definition snapshot (DOMAIN_MODEL.md §3).

    ``graph`` is the nodes+edges+config jsonb the engine consumes. ``node_pins``
    maps each graph node to the exact node version it is pinned to, so publishing
    a new node version never changes a workflow pinned to an older one.
    """

    __tablename__ = "workflow_version"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    node_pins: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # version is monotonic per workflow — the immutability + ordering spine.
        UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),
    )
