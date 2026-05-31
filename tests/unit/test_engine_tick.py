"""Unit tests for multi-graph worker execution via engine.tick(resolver).

The single-graph path (step/run_to_completion) is covered in test_engine.py. This
file proves the worker-shaped path: a single engine draining a queue that holds
nodes from *different* runs with *different* graphs, resolving each run's graph on
demand. This is what lets one worker process serve every tenant's workflows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from uuid import UUID, uuid4

import pytest

from axiom.domains.execution.engine import WorkflowEngine
from axiom.domains.execution.protocols import InvocationOutcome
from axiom.domains.execution.state import ExecutionStatus
from axiom.domains.execution.store_memory import InMemoryStore
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.sdk import JsonObject

pytestmark = pytest.mark.unit


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class _EchoInvoker:
    """Succeeds every node, echoing which node ran so order is observable."""

    def __init__(self) -> None:
        self.ran: list[str] = []

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
        self.ran.append(node_id)
        return InvocationOutcome.success(node_id=node_id, attempt=attempt, output={"id": node_id})


def _graph(*node_ids: str) -> WorkflowGraph:
    nodes = [{"id": n, "node_ref": "test/echo"} for n in node_ids]
    edges = [{"from": a, "to": b} for a, b in pairwise(node_ids)]
    return WorkflowGraph.from_dict({"nodes": nodes, "edges": edges})


async def test_tick_drains_two_runs_with_different_graphs() -> None:
    """One engine, two runs, two distinct graphs, resolved per-run by tick.

    Proves a worker can process nodes from interleaved runs without knowing any
    graph up front — it resolves each claimed node's graph on demand.
    """
    store = InMemoryStore()
    invoker = _EchoInvoker()
    engine = WorkflowEngine(store=store, invoker=invoker, clock=_FixedClock())

    graph_a = _graph("a1", "a2")
    graph_b = _graph("b1", "b2", "b3")

    exec_a = await engine.start_execution(
        org_id=uuid4(), workflow_id=uuid4(), workflow_version_id=uuid4(), graph=graph_a
    )
    exec_b = await engine.start_execution(
        org_id=uuid4(), workflow_id=uuid4(), workflow_version_id=uuid4(), graph=graph_b
    )

    # A resolver mapping each run to its own graph — exactly what the worker does
    # (backed by the authoring store) but here as an in-test dict.
    graphs = {exec_a.id: graph_a, exec_b.id: graph_b}

    async def resolver(execution_id: UUID) -> WorkflowGraph:
        return graphs[execution_id]

    # Drain the whole queue with a single worker loop.
    ticks = 0
    while await engine.tick(resolver):
        ticks += 1
        assert ticks < 100  # runaway guard

    # Both runs completed; every node of both graphs ran exactly once.
    final_a = await store.get_execution(exec_a.id)
    final_b = await store.get_execution(exec_b.id)
    assert final_a is not None and final_a.status is ExecutionStatus.SUCCEEDED
    assert final_b is not None and final_b.status is ExecutionStatus.SUCCEEDED
    assert sorted(invoker.ran) == ["a1", "a2", "b1", "b2", "b3"]


async def test_step_is_tick_with_constant_resolver() -> None:
    # The single-graph step() must remain a faithful special case of tick().
    store = InMemoryStore()
    invoker = _EchoInvoker()
    engine = WorkflowEngine(store=store, invoker=invoker, clock=_FixedClock())
    graph = _graph("n1", "n2")

    execution = await engine.start_execution(
        org_id=uuid4(), workflow_id=uuid4(), workflow_version_id=uuid4(), graph=graph
    )
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.SUCCEEDED
    assert invoker.ran == ["n1", "n2"]
