"""Postgres-backed Store — durability + the atomic claim under concurrency.

This realizes the engine's ``Store`` protocol against Postgres. It must reproduce
*exactly* the behaviour the in-memory reference store defines (``store_memory.py``)
— the engine's unit tests pin that behaviour — while adding the two things memory
cannot give: durability across process death, and a claim that is correct when
many workers race.

The claim is the crux. ``claim_next_ready`` runs

    SELECT ... FROM node_state
    WHERE status = 'ready' AND (not_before IS NULL OR not_before <= now)
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT 1

inside a transaction. ``FOR UPDATE SKIP LOCKED`` is what makes parallelism correct
without a queue server (ADR-0001): each worker locks a different claimable row and
skips rows already locked by a peer, so no two workers ever run the same node.
The transition to RUNNING + lease + attempt-increment + NodeStarted event all
commit together, so a crash either claimed-and-recorded or did neither.

Crash recovery is the lease + reaper: a RUNNING row carries ``lease_until``; if its
worker dies, ``recover_expired_leases`` (run periodically) flips it back to READY.
The run resumes from durable state exactly where it stopped (PHASE_1.md §6).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.domains.execution.models import (
    ExecutionEventRow,
    ExecutionRow,
    NodeStateRow,
)
from axiom.domains.execution.protocols import Store
from axiom.domains.execution.state import (
    EventType,
    Execution,
    ExecutionEvent,
    ExecutionEventRecord,
    ExecutionStatus,
    NodeError,
    NodeState,
    NodeStatus,
)
from axiom.platform.logging import get_logger
from axiom.sdk import JsonObject

logger = get_logger(__name__)


# ── Mapping: ORM row ⇄ pure value object ────────────────────────────────────────


def _execution_to_row(e: Execution) -> ExecutionRow:
    return ExecutionRow(
        id=e.id,
        org_id=e.org_id,
        workflow_id=e.workflow_id,
        workflow_version_id=e.workflow_version_id,
        status=e.status.value,
        trigger=e.trigger,
        input=e.input,
        total_cost_cents=e.total_cost_cents,
        stats=e.stats,
        started_at=e.started_at,
        finished_at=e.finished_at,
    )


def _row_to_execution(r: ExecutionRow) -> Execution:
    return Execution(
        id=r.id,
        org_id=r.org_id,
        workflow_id=r.workflow_id,
        workflow_version_id=r.workflow_version_id,
        status=ExecutionStatus(r.status),
        trigger=r.trigger,
        input=r.input,
        total_cost_cents=r.total_cost_cents,
        stats=r.stats,
        started_at=r.started_at,
        finished_at=r.finished_at,
    )


def _row_to_node_state(r: NodeStateRow) -> NodeState:
    err = None
    if r.error:
        err = NodeError(
            error_class=r.error.get("error_class", "internal"),
            message=r.error.get("message", ""),
            retryable=bool(r.error.get("retryable", False)),
            retry_after_seconds=r.error.get("retry_after_seconds"),
        )
    return NodeState(
        execution_id=r.execution_id,
        node_id=r.node_id,
        status=NodeStatus(r.status),
        attempt=r.attempt,
        input=r.input,
        output=r.output,
        cost_cents=r.cost_cents,
        lease_until=r.lease_until,
        not_before=r.not_before,
        error=err,
        started_at=r.started_at,
        finished_at=r.finished_at,
    )


def _error_to_jsonb(err: NodeError | None) -> JsonObject | None:
    if err is None:
        return None
    return {
        "error_class": err.error_class,
        "message": err.message,
        "retryable": err.retryable,
        "retry_after_seconds": err.retry_after_seconds,
    }


def _apply_node_state(row: NodeStateRow, ns: NodeState) -> None:
    """Copy a value object's mutable fields onto its persisted row."""
    row.status = ns.status.value
    row.attempt = ns.attempt
    row.input = ns.input
    row.output = ns.output
    row.cost_cents = ns.cost_cents
    row.lease_until = ns.lease_until
    row.not_before = ns.not_before
    row.error = _error_to_jsonb(ns.error)
    row.started_at = ns.started_at
    row.finished_at = ns.finished_at


def _event_row(ev: ExecutionEvent) -> ExecutionEventRow:
    return ExecutionEventRow(
        execution_id=ev.execution_id,
        org_id=ev.org_id,
        seq=ev.seq,
        type=ev.type.value,
        node_id=ev.node_id,
        attempt=ev.attempt,
        payload=ev.payload,
        created_at=ev.created_at,
    )


def _row_to_event_record(r: ExecutionEventRow) -> ExecutionEventRecord:
    return ExecutionEventRecord(
        id=r.id,
        execution_id=r.execution_id,
        org_id=r.org_id,
        seq=r.seq,
        type=EventType(r.type),
        payload=r.payload,
        node_id=r.node_id,
        attempt=r.attempt,
        created_at=r.created_at,
    )


# ── The store ────────────────────────────────────────────────────────────────────


class PostgresStore(Store):
    """Durable Store. One transaction per protocol method (the unit of work)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    # ── Reads ──────────────────────────────────────────────────────────────────────

    async def get_execution(self, execution_id: UUID) -> Execution | None:
        async with self._sessions() as session:
            row = await session.get(ExecutionRow, execution_id)
            return _row_to_execution(row) if row else None

    async def get_node_states(self, execution_id: UUID) -> list[NodeState]:
        async with self._sessions() as session:
            result = await session.execute(
                select(NodeStateRow)
                .where(NodeStateRow.execution_id == execution_id)
                .order_by(NodeStateRow.node_id)
            )
            return [_row_to_node_state(r) for r in result.scalars()]

    async def get_events(
        self, execution_id: UUID, *, after_id: int = 0, limit: int = 1000
    ) -> list[ExecutionEventRecord]:
        """Read a run's event log ordered by the global monotonic ``id``.

        ``after_id`` is the high-water mark: pass 0 to replay from the start, or
        the last id you have to fetch only what's new. This single primitive backs
        both the ``GET .../events`` endpoint (after_id=0) and the websocket tail
        (poll with the last id seen) — the ordering authority is the autoincrement
        ``id``, so the stream is gap-free and resumable across reconnects.
        """
        async with self._sessions() as session:
            result = await session.execute(
                select(ExecutionEventRow)
                .where(
                    ExecutionEventRow.execution_id == execution_id,
                    ExecutionEventRow.id > after_id,
                )
                .order_by(ExecutionEventRow.id)
                .limit(limit)
            )
            return [_row_to_event_record(r) for r in result.scalars()]

    async def list_executions(
        self, *, org_id: UUID, workflow_id: UUID | None = None, limit: int = 100
    ) -> list[Execution]:
        """An org's runs, newest first — optionally filtered to one workflow.

        Org-scoped (the multi-tenant spine); ``workflow_id`` narrows it to a single
        workflow's history. Backs the run-history endpoints.
        """
        async with self._sessions() as session:
            stmt = select(ExecutionRow).where(ExecutionRow.org_id == org_id)
            if workflow_id is not None:
                stmt = stmt.where(ExecutionRow.workflow_id == workflow_id)
            stmt = stmt.order_by(ExecutionRow.started_at.desc().nullslast()).limit(limit)
            result = await session.execute(stmt)
            return [_row_to_execution(r) for r in result.scalars()]

    # ── Run creation ───────────────────────────────────────────────────────────────

    async def create_execution(
        self,
        execution: Execution,
        node_states: list[NodeState],
        events: list[ExecutionEvent],
    ) -> None:
        async with self._sessions() as session, session.begin():
            session.add(_execution_to_row(execution))
            # Flush the parent row first: both node_state and execution_event
            # carry an FK to execution.id, and execution_event has no ORM
            # relationship to trigger automatic insert-ordering — so we make the
            # parent exist explicitly before adding the children, within the same
            # transaction (no commit until the block exits).
            await session.flush()
            for ns in node_states:
                row = NodeStateRow(execution_id=ns.execution_id, node_id=ns.node_id)
                _apply_node_state(row, ns)
                session.add(row)
            for ev in events:
                session.add(_event_row(ev))

    # ── The atomic claim ───────────────────────────────────────────────────────────

    async def claim_next_ready(
        self, *, now: datetime, lease_ttl_seconds: float
    ) -> NodeState | None:
        from datetime import timedelta

        async with self._sessions() as session, session.begin():
            # Lock one claimable row; SKIP LOCKED lets peers grab different rows.
            stmt = (
                select(NodeStateRow)
                .join(ExecutionRow, ExecutionRow.id == NodeStateRow.execution_id)
                .where(
                    NodeStateRow.status == NodeStatus.READY.value,
                    ExecutionRow.status == ExecutionStatus.RUNNING.value,
                    (NodeStateRow.not_before.is_(None)) | (NodeStateRow.not_before <= now),
                )
                .order_by(NodeStateRow.id)
                .limit(1)
                .with_for_update(skip_locked=True, of=NodeStateRow)
            )
            row = (await session.execute(stmt)).scalars().first()
            if row is None:
                return None

            row.status = NodeStatus.RUNNING.value
            row.attempt += 1
            row.not_before = None
            row.lease_until = now + timedelta(seconds=lease_ttl_seconds)
            row.started_at = row.started_at or now

            execution = await session.get(ExecutionRow, row.execution_id)
            assert execution is not None  # FK guarantees this  # noqa: S101
            session.add(
                ExecutionEventRow(
                    execution_id=row.execution_id,
                    org_id=execution.org_id,
                    seq=0,
                    type=EventType.NODE_STARTED.value,
                    node_id=row.node_id,
                    attempt=row.attempt,
                    payload={},
                    created_at=now,
                )
            )
            return _row_to_node_state(row)

    # ── Settle ───────────────────────────────────────────────────────────────────────

    async def settle_node(
        self,
        *,
        node: NodeState,
        event: ExecutionEvent,
        promote_to_ready: dict[str, JsonObject],
        execution_update: Execution | None,
        terminal_events: list[ExecutionEvent],
    ) -> None:
        async with self._sessions() as session, session.begin():
            row = await self._get_node_row(session, node.execution_id, node.node_id)
            if row is not None:
                _apply_node_state(row, node)
            session.add(_event_row(event))

            # Guarded promotion: only flip a successor that is STILL pending, and
            # set its resolved input. Concurrent sibling settles stay idempotent
            # because a second promotion sees status != PENDING and is a no-op.
            for succ_id, resolved_input in promote_to_ready.items():
                succ_row = await self._get_node_row(session, node.execution_id, succ_id)
                if succ_row is not None and succ_row.status == NodeStatus.PENDING.value:
                    succ_row.status = NodeStatus.READY.value
                    succ_row.input = resolved_input

            if execution_update is not None:
                exec_row = await session.get(ExecutionRow, execution_update.id)
                if exec_row is not None:
                    exec_row.status = execution_update.status.value
                    exec_row.total_cost_cents = execution_update.total_cost_cents
                    exec_row.stats = execution_update.stats
                    exec_row.finished_at = execution_update.finished_at

            for ev in terminal_events:
                session.add(_event_row(ev))

    # ── Crash recovery ─────────────────────────────────────────────────────────────────

    async def recover_expired_leases(self, *, now: datetime) -> list[NodeState]:
        async with self._sessions() as session, session.begin():
            stmt = (
                select(NodeStateRow)
                .where(
                    NodeStateRow.status == NodeStatus.RUNNING.value,
                    NodeStateRow.lease_until.is_not(None),
                    NodeStateRow.lease_until <= now,
                )
                .with_for_update(skip_locked=True)
            )
            rows = list((await session.execute(stmt)).scalars())
            recovered: list[NodeState] = []
            for row in rows:
                row.status = NodeStatus.READY.value
                row.lease_until = None
                # attempt is NOT bumped here — the next claim bumps it, so the
                # recovered run resumes at the correct attempt key.
                execution = await session.get(ExecutionRow, row.execution_id)
                org_id = execution.org_id if execution else row.execution_id
                session.add(
                    ExecutionEventRow(
                        execution_id=row.execution_id,
                        org_id=org_id,
                        seq=0,
                        type=EventType.NODE_RECOVERED.value,
                        node_id=row.node_id,
                        attempt=row.attempt,
                        payload={"reason": "lease_expired"},
                        created_at=now,
                    )
                )
                recovered.append(_row_to_node_state(row))
            return recovered

    async def renew_lease(self, *, node: NodeState, lease_until: datetime) -> bool:
        async with self._sessions() as session, session.begin():
            row = await self._get_node_row(session, node.execution_id, node.node_id)
            if row is None or row.status != NodeStatus.RUNNING.value:
                return False
            row.lease_until = lease_until
            return True

    # ── internals ──────────────────────────────────────────────────────────────────────

    @staticmethod
    async def _get_node_row(
        session: AsyncSession, execution_id: UUID, node_id: str
    ) -> NodeStateRow | None:
        result = await session.execute(
            select(NodeStateRow).where(
                NodeStateRow.execution_id == execution_id,
                NodeStateRow.node_id == node_id,
            )
        )
        return result.scalars().first()
