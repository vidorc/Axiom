"""Integration + chaos tests for the engine against REAL Postgres.

This is where the durability and concurrency guarantees are proven — the things
the in-memory store cannot demonstrate. Two lanes share this file:

  * ``@pytest.mark.integration`` — the engine runs a DAG to completion through
    ``PostgresStore``, and the ``SKIP LOCKED`` claim is shown to be race-free.
  * ``@pytest.mark.chaos`` — PHASE_1.md §6, "the single most important test in
    the codebase": a worker dies mid-node (its result never settles), the reaper
    finds the expired lease, another worker re-claims and completes, and the run
    finishes exactly once with no double-settled node.

Both require the test datastore (``make test-stack-up``); the session fixture
skips cleanly if Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.domains.execution.engine import EngineConfig, WorkflowEngine
from axiom.domains.execution.protocols import InvocationOutcome
from axiom.domains.execution.state import Execution, ExecutionStatus, NodeStatus
from axiom.domains.execution.store_pg import PostgresStore
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.sdk import JsonObject

# ── Test doubles ─────────────────────────────────────────────────────────────────


class _MutableClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class _CountingInvoker:
    """Succeeds every node with output {"ok": node_id}; counts invocations.

    The invocation count per node is the exactly-once evidence: after a crash +
    recovery, a node must end up SUCCEEDED having been *settled* once, even if it
    was attempted more than once.
    """

    def __init__(self) -> None:
        self.invocations: list[tuple[str, int]] = []

    async def invoke(
        self,
        *,
        node_ref: str,
        config: JsonObject,
        credentials: dict[str, str],
        inputs: JsonObject,
        run_id: UUID,
        org_id: UUID,
        node_id: str,
        attempt: int,
    ) -> InvocationOutcome:
        self.invocations.append((node_id, attempt))
        return InvocationOutcome.success(
            node_id=node_id, attempt=attempt, output={"ok": node_id}, cost_cents=1
        )


def _linear_graph(n: int) -> WorkflowGraph:
    return WorkflowGraph.from_dict(
        {
            "nodes": [{"id": f"n{i}", "node_ref": "test/echo"} for i in range(1, n + 1)],
            "edges": [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(1, n)],
        }
    )


async def _start(engine: WorkflowEngine, graph: WorkflowGraph) -> Execution:
    return await engine.start_execution(
        org_id=uuid4(),
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        graph=graph,
    )


# ── Integration: the engine runs against real Postgres ───────────────────────────


@pytest.mark.integration
async def test_postgres_store_runs_linear_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresStore(session_factory)
    invoker = _CountingInvoker()
    engine = WorkflowEngine(store=store, invoker=invoker, clock=_MutableClock())
    graph = _linear_graph(3)

    execution = await _start(engine, graph)
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None
    assert final.status is ExecutionStatus.SUCCEEDED
    assert final.total_cost_cents == 3  # one cent per node
    # Each node invoked exactly once, in order — durable state, real claim.
    assert invoker.invocations == [("n1", 1), ("n2", 1), ("n3", 1)]

    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert all(s.status is NodeStatus.SUCCEEDED for s in states.values())
    # Data flowed through the edge: n2's INPUT is n1's output. (The counting
    # invoker ignores input and always returns {"ok": node_id}, so we assert on
    # the durably-persisted input, which is what the engine resolved + stored.)
    assert states["n2"].input == {"ok": "n1"}


@pytest.mark.integration
async def test_skip_locked_claim_is_race_free(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two workers claiming concurrently must never grab the same node.

    Start a wide fan-out (one root → many parallel children). After the root
    runs, all children are READY at once. Fire many concurrent claims; assert
    every claimed node_id is distinct — the SKIP LOCKED guarantee.
    """
    store = PostgresStore(session_factory)
    invoker = _CountingInvoker()
    engine = WorkflowEngine(store=store, invoker=invoker, clock=_MutableClock())

    width = 8
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [{"id": "root", "node_ref": "test/echo"}]
            + [{"id": f"c{i}", "node_ref": "test/echo"} for i in range(width)],
            "edges": [{"from": "root", "to": f"c{i}"} for i in range(width)],
        }
    )
    await _start(engine, graph)

    # Run the root so all children become READY.
    assert await engine.step(graph) is True

    # Now fire `width` concurrent claims directly against the store.
    now = datetime(2026, 1, 1, tzinfo=UTC)
    claims = await asyncio.gather(
        *(store.claim_next_ready(now=now, lease_ttl_seconds=30) for _ in range(width))
    )
    claimed_ids = [c.node_id for c in claims if c is not None]
    # Every claim returned a DISTINCT node — no double-claim under concurrency.
    assert len(claimed_ids) == len(set(claimed_ids))
    assert set(claimed_ids) == {f"c{i}" for i in range(width)}


# ── Chaos: crash recovery, exactly-once (PHASE_1.md §6) ───────────────────────────


@pytest.mark.chaos
async def test_worker_crash_mid_node_recovers_and_completes_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The headline guarantee. A worker claims a node then dies before settling.

    Sequence:
      1. Engine runs n1 (succeeds), promoting n2 to READY.
      2. A 'worker' claims n2 (status RUNNING, lease set) — then crashes: we
         simply never settle it, exactly as a kill -9 between claim and commit.
      3. Time advances past the lease. The reaper finds the expired lease and
         resets n2 to READY (durable state — the run did not die with the worker).
      4. Another worker re-claims n2 and the run completes.

    Assertions: the run ends SUCCEEDED; every node is SUCCEEDED; n2 was settled
    exactly once (no double-settle); n1 was never re-run.
    """
    store = PostgresStore(session_factory)
    invoker = _CountingInvoker()
    clock = _MutableClock()
    engine = WorkflowEngine(
        store=store,
        invoker=invoker,
        clock=clock,
        config=EngineConfig(lease_ttl=timedelta(seconds=10)),
    )
    graph = _linear_graph(3)
    execution = await _start(engine, graph)

    # 1) n1 runs and promotes n2.
    assert await engine.step(graph) is True
    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert states["n1"].status is NodeStatus.SUCCEEDED
    assert states["n2"].status is NodeStatus.READY

    # 2) A worker claims n2 then 'crashes' (we never settle it).
    claimed = await store.claim_next_ready(now=clock.now(), lease_ttl_seconds=10)
    assert claimed is not None and claimed.node_id == "n2"
    assert claimed.attempt == 1
    crashed_states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert crashed_states["n2"].status is NodeStatus.RUNNING  # stuck, lease held

    # 3) Lease expires; reaper recovers n2.
    clock.advance(11)
    recovered = await engine.reap()
    assert [n.node_id for n in recovered] == ["n2"]
    reaped_states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert reaped_states["n2"].status is NodeStatus.READY  # back to claimable

    # 4) Another worker finishes the run.
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None
    assert final.status is ExecutionStatus.SUCCEEDED

    final_states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert all(s.status is NodeStatus.SUCCEEDED for s in final_states.values())
    # n1 ran exactly once and was NOT re-run by recovery.
    assert sum(1 for nid, _ in invoker.invocations if nid == "n1") == 1
    # n2 reached attempt 2 (claim post-recovery bumped it) but is SUCCEEDED once.
    assert final_states["n2"].status is NodeStatus.SUCCEEDED
    assert final_states["n2"].attempt == 2


@pytest.mark.chaos
async def test_no_double_settle_after_recovery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The event log records exactly one terminal settle per node.

    Even with a recovery in the middle, a node must not produce two NodeSucceeded
    events — the projection (and any side effect keyed on it) must be exactly
    once at the settle boundary.
    """
    store = PostgresStore(session_factory)
    invoker = _CountingInvoker()
    clock = _MutableClock()
    engine = WorkflowEngine(
        store=store,
        invoker=invoker,
        clock=clock,
        config=EngineConfig(lease_ttl=timedelta(seconds=5)),
    )
    graph = _linear_graph(2)
    execution = await _start(engine, graph)

    await engine.step(graph)  # n1 succeeds
    # Crash on n2.
    claimed = await store.claim_next_ready(now=clock.now(), lease_ttl_seconds=5)
    assert claimed is not None and claimed.node_id == "n2"
    clock.advance(6)
    await engine.reap()
    await engine.run_to_completion(graph)

    # Count NodeSucceeded events per node directly from the event log.
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT node_id, count(*) FROM execution_event "
                "WHERE execution_id = :eid AND type = 'NodeSucceeded' GROUP BY node_id"
            ),
            {"eid": str(execution.id)},
        )
        succeeded_counts = {r[0]: r[1] for r in rows}

    assert succeeded_counts.get("n1") == 1
    assert succeeded_counts.get("n2") == 1  # exactly once despite the recovery
