"""In-memory Store — the reference implementation of the engine's Store contract.

This is not a throwaway test mock. It is the canonical, behaviour-defining
implementation of the ``Store`` protocol: the engine's unit tests run against it
in milliseconds, and the Postgres store (``store_pg.py``) must reproduce exactly
this behaviour under concurrency and crashes. When the two disagree, this file
defines what "correct" means.

Concurrency model: asyncio is single-threaded and these methods contain no
``await`` points between read and write, so each method is effectively atomic
with respect to other coroutines — which is precisely the guarantee the Postgres
store buys with ``FOR UPDATE SKIP LOCKED`` and a transaction. That symmetry is
what lets the same engine tests validate both stores.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from axiom.domains.execution.protocols import Store
from axiom.domains.execution.state import (
    Execution,
    ExecutionEvent,
    NodeState,
    NodeStatus,
)
from axiom.sdk import JsonObject


class InMemoryStore(Store):
    """A dict-backed Store. Single-process, no durability — for tests and the
    in-process CLI run path."""

    def __init__(self) -> None:
        self._executions: dict[UUID, Execution] = {}
        # execution_id -> {node_id -> NodeState}
        self._nodes: dict[UUID, dict[str, NodeState]] = {}
        # execution_id -> append-only event list
        self._events: dict[UUID, list[ExecutionEvent]] = {}

    # ── Reads ────────────────────────────────────────────────────────────────────

    async def get_execution(self, execution_id: UUID) -> Execution | None:
        return self._executions.get(execution_id)

    async def get_node_states(self, execution_id: UUID) -> list[NodeState]:
        return list(self._nodes.get(execution_id, {}).values())

    def events(self, execution_id: UUID) -> list[ExecutionEvent]:
        """Test/inspection helper — the event log for a run (not in the protocol)."""
        return list(self._events.get(execution_id, []))

    # ── Run creation ───────────────────────────────────────────────────────────────

    async def create_execution(
        self,
        execution: Execution,
        node_states: list[NodeState],
        events: list[ExecutionEvent],
    ) -> None:
        self._executions[execution.id] = execution
        self._nodes[execution.id] = {ns.node_id: ns for ns in node_states}
        self._events[execution.id] = list(events)

    # ── The atomic claim ─────────────────────────────────────────────────────────────

    async def claim_next_ready(
        self, *, now: datetime, lease_ttl_seconds: float
    ) -> NodeState | None:
        """Claim the oldest claimable READY node across all running executions.

        Claimable = READY and (not_before is None or not_before <= now). The
        transition to RUNNING + lease + attempt increment + NodeStarted event is
        done here with no intervening await, mirroring the Postgres transaction.
        The lease is set to ``now + lease_ttl_seconds`` (engine policy), so a
        crash between claim and settle is recoverable by the reaper.
        """
        from datetime import timedelta

        for execution_id, nodes in self._nodes.items():
            for node in nodes.values():
                if node.status is not NodeStatus.READY:
                    continue
                if node.not_before is not None and node.not_before > now:
                    continue
                # Claim it.
                node.status = NodeStatus.RUNNING
                node.attempt += 1
                node.not_before = None
                node.lease_until = now + timedelta(seconds=lease_ttl_seconds)
                node.started_at = node.started_at or now
                self._append(
                    execution_id,
                    _started_event(self._executions[execution_id], node),
                )
                return node
        return None

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
        nodes = self._nodes[node.execution_id]
        # Persist the settled node (it is the same object the engine mutated, but
        # assign explicitly so a future copy-based store stays correct).
        nodes[node.node_id] = node
        self._append(node.execution_id, event)

        # Guarded promotion: only promote a successor that is STILL pending, and
        # set its resolved input. This guard is what makes concurrent sibling
        # settles idempotent (DOMAIN_MODEL.md §4).
        for succ_id, resolved_input in promote_to_ready.items():
            succ = nodes.get(succ_id)
            if succ is not None and succ.status is NodeStatus.PENDING:
                succ.status = NodeStatus.READY
                succ.input = resolved_input

        if execution_update is not None:
            self._executions[execution_update.id] = execution_update
        for ev in terminal_events:
            self._append(node.execution_id, ev)

    # ── Crash recovery ─────────────────────────────────────────────────────────────────

    async def recover_expired_leases(self, *, now: datetime) -> list[NodeState]:
        recovered: list[NodeState] = []
        for execution_id, nodes in self._nodes.items():
            for node in nodes.values():
                if (
                    node.status is NodeStatus.RUNNING
                    and node.lease_until is not None
                    and node.lease_until <= now
                ):
                    node.status = NodeStatus.READY
                    node.lease_until = None
                    # not_before stays None — recovered work is immediately
                    # re-claimable; the attempt counter is NOT bumped here (the
                    # next claim bumps it), so the recovered run resumes at the
                    # right attempt key.
                    recovered.append(node)
                    self._append(
                        execution_id,
                        _recovered_event(self._executions[execution_id], node),
                    )
        return recovered

    async def renew_lease(self, *, node: NodeState, lease_until: datetime) -> bool:
        nodes = self._nodes.get(node.execution_id)
        if nodes is None:
            return False
        current = nodes.get(node.node_id)
        if current is None or current.status is not NodeStatus.RUNNING:
            return False
        current.lease_until = lease_until
        return True

    # ── internals ──────────────────────────────────────────────────────────────────────

    def _append(self, execution_id: UUID, event: ExecutionEvent) -> None:
        log = self._events.setdefault(execution_id, [])
        # Assign a monotonic seq if the caller left it at 0 (engine sets seq only
        # for the opening events; cascade events get their order here).
        if event.seq == 0:
            next_seq = (log[-1].seq + 1) if log else 1
            event = ExecutionEvent(
                execution_id=event.execution_id,
                org_id=event.org_id,
                seq=next_seq,
                type=event.type,
                payload=event.payload,
                node_id=event.node_id,
                attempt=event.attempt,
                created_at=event.created_at,
            )
        log.append(event)


def _started_event(execution: Execution, node: NodeState) -> ExecutionEvent:
    from axiom.domains.execution.state import EventType

    return ExecutionEvent(
        execution_id=execution.id,
        org_id=execution.org_id,
        seq=0,
        type=EventType.NODE_STARTED,
        node_id=node.node_id,
        attempt=node.attempt,
    )


def _recovered_event(execution: Execution, node: NodeState) -> ExecutionEvent:
    from axiom.domains.execution.state import EventType

    return ExecutionEvent(
        execution_id=execution.id,
        org_id=execution.org_id,
        seq=0,
        type=EventType.NODE_RECOVERED,
        node_id=node.node_id,
        attempt=node.attempt,
    )
