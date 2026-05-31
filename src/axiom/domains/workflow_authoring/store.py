"""AuthoringStore — persist workflow definitions, resolve runs to their graphs.

Two responsibilities:

  * **Author** a workflow + an immutable version (``create_workflow_version``).
    The version's ``graph`` jsonb is validated through ``WorkflowGraph.from_dict``
    before it is stored, so an invalid graph fails at save time, never mid-run
    (DOMAIN_MODEL.md §3 — "a workflow that won't run should fail to save").

  * **Resolve** a stored version to a parsed ``WorkflowGraph`` (``get_graph``).
    This is what backs the engine's ``GraphResolver``: a worker, after claiming a
    node, looks up the run's pinned ``workflow_version`` and gets its graph here.

Parsed graphs are cached by ``version_id``. Versions are immutable, so the cache
is trivially correct (a version's graph never changes) — and it matters: a worker
resolves the same run's graph once per node it ticks, so without the cache it
would re-parse + re-validate the whole graph on every step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.domains.workflow_authoring.models import WorkflowRow, WorkflowVersionRow
from axiom.platform.logging import get_logger
from axiom.sdk import JsonObject

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowNotFoundError(Exception):
    """A requested workflow or version does not exist (or is not the caller's)."""


# ── Read value objects ────────────────────────────────────────────────────────
# The store returns these pure records, never ORM rows — the ORM stays inside the
# domain (same discipline as execution.state vs execution.models). The delivery
# layer maps these onto its own response schemas.


@dataclass(frozen=True, slots=True)
class WorkflowSummary:
    """A workflow container's metadata (no version payload)."""

    id: UUID
    org_id: UUID
    name: str
    folder: str | None
    current_version_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkflowVersionSummary:
    """Version metadata without the (potentially large) graph — for listings."""

    id: UUID
    workflow_id: UUID
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkflowVersionRecord:
    """A full immutable version, including its graph + node pins."""

    id: UUID
    workflow_id: UUID
    version: int
    graph: JsonObject
    node_pins: JsonObject
    created_at: datetime


def _to_workflow_summary(row: WorkflowRow) -> WorkflowSummary:
    return WorkflowSummary(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        folder=row.folder,
        current_version_id=row.current_version_id,
        created_at=row.created_at,
    )


def _to_version_summary(row: WorkflowVersionRow) -> WorkflowVersionSummary:
    return WorkflowVersionSummary(
        id=row.id,
        workflow_id=row.workflow_id,
        version=row.version,
        created_at=row.created_at,
    )


def _to_version_record(row: WorkflowVersionRow) -> WorkflowVersionRecord:
    return WorkflowVersionRecord(
        id=row.id,
        workflow_id=row.workflow_id,
        version=row.version,
        graph=row.graph,
        node_pins=row.node_pins,
        created_at=row.created_at,
    )


class AuthoringStore:
    """Persistence for workflow definitions and versions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory
        # version_id -> parsed graph. Safe to cache forever: versions are immutable.
        self._graph_cache: dict[UUID, WorkflowGraph] = {}

    async def create_workflow_version(
        self,
        *,
        org_id: UUID,
        name: str,
        graph: JsonObject,
        node_pins: JsonObject | None = None,
        workflow_id: UUID | None = None,
        folder: str | None = None,
    ) -> tuple[UUID, UUID]:
        """Create (or extend) a workflow with a new immutable version.

        Validates the graph before persisting. If ``workflow_id`` is given, a new
        version is appended to that workflow (version = max + 1); otherwise a new
        workflow is created at version 1. Returns ``(workflow_id, version_id)``
        and points the workflow's ``current_version_id`` at the new version. One
        unit of work.
        """
        # Validate up front — raises GraphValidationError if the graph is bad.
        WorkflowGraph.from_dict(graph)

        version_id = uuid4()
        async with self._sessions() as session, session.begin():
            if workflow_id is None:
                workflow_id = uuid4()
                session.add(
                    WorkflowRow(
                        id=workflow_id,
                        org_id=org_id,
                        name=name,
                        folder=folder,
                        created_at=_utcnow(),
                    )
                )
                next_version = 1
            else:
                wf = await session.get(WorkflowRow, workflow_id)
                if wf is None:
                    raise WorkflowNotFoundError(f"workflow {workflow_id} not found")
                current_max = await session.scalar(
                    select(func.max(WorkflowVersionRow.version)).where(
                        WorkflowVersionRow.workflow_id == workflow_id
                    )
                )
                next_version = (current_max or 0) + 1

            session.add(
                WorkflowVersionRow(
                    id=version_id,
                    workflow_id=workflow_id,
                    version=next_version,
                    graph=graph,
                    node_pins=node_pins or {},
                    created_at=_utcnow(),
                )
            )
            await session.flush()  # version row exists before we point at it
            wf = await session.get(WorkflowRow, workflow_id)
            if wf is not None:
                wf.current_version_id = version_id

        logger.info(
            "workflow.version_created",
            workflow_id=str(workflow_id),
            version_id=str(version_id),
            version=next_version,
        )
        return workflow_id, version_id

    async def get_graph(self, version_id: UUID) -> WorkflowGraph:
        """Resolve a workflow_version to its parsed, validated graph (cached)."""
        cached = self._graph_cache.get(version_id)
        if cached is not None:
            return cached
        async with self._sessions() as session:
            row = await session.get(WorkflowVersionRow, version_id)
            if row is None:
                raise WorkflowNotFoundError(f"workflow_version {version_id} not found")
            graph = WorkflowGraph.from_dict(row.graph)
        self._graph_cache[version_id] = graph
        return graph

    # ── Reads (org-scoped) — back the CRUD + execution APIs ───────────────────

    async def get_workflow(self, *, org_id: UUID, workflow_id: UUID) -> WorkflowSummary:
        """Fetch one workflow's metadata, scoped to its owning org.

        Raises ``WorkflowNotFoundError`` if the workflow does not exist OR belongs
        to another org — the two are deliberately indistinguishable to a caller so
        cross-tenant probing can't tell "absent" from "forbidden".
        """
        async with self._sessions() as session:
            row = await session.get(WorkflowRow, workflow_id)
            if row is None or row.org_id != org_id:
                raise WorkflowNotFoundError(f"workflow {workflow_id} not found")
            return _to_workflow_summary(row)

    async def list_workflows(self, *, org_id: UUID) -> list[WorkflowSummary]:
        """All of an org's workflows, newest first."""
        async with self._sessions() as session:
            result = await session.execute(
                select(WorkflowRow)
                .where(WorkflowRow.org_id == org_id)
                .order_by(WorkflowRow.created_at.desc())
            )
            return [_to_workflow_summary(r) for r in result.scalars()]

    async def get_version(self, version_id: UUID) -> WorkflowVersionRecord:
        """Fetch one immutable version in full (graph + pins)."""
        async with self._sessions() as session:
            row = await session.get(WorkflowVersionRow, version_id)
            if row is None:
                raise WorkflowNotFoundError(f"workflow_version {version_id} not found")
            return _to_version_record(row)

    async def list_versions(
        self, *, org_id: UUID, workflow_id: UUID
    ) -> list[WorkflowVersionSummary]:
        """A workflow's versions (metadata only), highest version first.

        Org-scoped through the parent workflow so one org can't enumerate
        another's version history.
        """
        async with self._sessions() as session:
            wf = await session.get(WorkflowRow, workflow_id)
            if wf is None or wf.org_id != org_id:
                raise WorkflowNotFoundError(f"workflow {workflow_id} not found")
            result = await session.execute(
                select(WorkflowVersionRow)
                .where(WorkflowVersionRow.workflow_id == workflow_id)
                .order_by(WorkflowVersionRow.version.desc())
            )
            return [_to_version_summary(r) for r in result.scalars()]

    async def resolve_runnable_version(
        self, *, org_id: UUID, workflow_id: UUID, version: int | None = None
    ) -> UUID:
        """Resolve which ``workflow_version`` a run should pin to.

        This is the design-time → run-time handoff: starting a run must pin an
        *exact, immutable* version so editing the workflow afterwards can't alter
        the run in flight (DOMAIN_MODEL.md §3). Two modes:

          * ``version is None`` → the workflow's ``current_version_id`` (the
            version the editor last published — the default for a manual run).
          * a specific ``version`` number → that exact version.

        Org-scoped through the parent workflow. Raises ``WorkflowNotFoundError``
        if the workflow isn't the caller's, or if it has no runnable version yet.
        """
        async with self._sessions() as session:
            wf = await session.get(WorkflowRow, workflow_id)
            if wf is None or wf.org_id != org_id:
                raise WorkflowNotFoundError(f"workflow {workflow_id} not found")

            if version is None:
                if wf.current_version_id is None:
                    raise WorkflowNotFoundError(
                        f"workflow {workflow_id} has no published version to run"
                    )
                return wf.current_version_id

            version_id = await session.scalar(
                select(WorkflowVersionRow.id).where(
                    WorkflowVersionRow.workflow_id == workflow_id,
                    WorkflowVersionRow.version == version,
                )
            )
            if version_id is None:
                raise WorkflowNotFoundError(f"workflow {workflow_id} has no version {version}")
            return version_id
