"""End-to-end: the engine driving REAL nodes through the real runtime.

Unlike test_engine.py (which fakes the invoker to isolate scheduling), this wires
the whole stack together: WorkflowEngine → RunnerInvoker (worker adapter) →
NodeRunner (runtime) → real BaseNode subclasses from the built-in registry. It is
the proof that the four components built independently actually compose — that a
graph of real nodes executes, moves data through edges, and produces output, with
no database (in-memory store) and no network (nodes that don't make calls).

This lives in the unit lane because it touches no external services; it is the
fast confidence that "the engine runs real nodes" holds on every commit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from axiom.composition.invoker import RunnerInvoker
from axiom.domains.execution.engine import EngineConfig, WorkflowEngine
from axiom.domains.execution.state import ExecutionStatus, NodeStatus
from axiom.domains.execution.store_memory import InMemoryStore
from axiom.domains.node_runtime import NodeRunner, build_default_registry
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.sdk import JsonObject

pytestmark = pytest.mark.unit


class _FixedClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now


def _make_stack() -> tuple[WorkflowEngine, InMemoryStore]:
    """Wire the real stack: engine → adapter → runner → built-in registry."""
    store = InMemoryStore()
    registry = build_default_registry()
    runner = NodeRunner(registry)
    invoker = RunnerInvoker(runner)
    engine = WorkflowEngine(
        store=store,
        invoker=invoker,
        clock=_FixedClock(),
        config=EngineConfig(lease_ttl=timedelta(seconds=30)),
    )
    return engine, store


async def _start(engine: WorkflowEngine, graph: WorkflowGraph, run_input: JsonObject):
    return await engine.start_execution(
        org_id=uuid4(),
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        graph=graph,
        run_input=run_input,
    )


async def test_echo_then_set_fields_chain_with_real_nodes() -> None:
    # n1 echoes the run input; n2 merges static fields onto it. Real nodes, real
    # data flow through the empty-mapping edge.
    engine, store = _make_stack()
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [
                {"id": "n1", "node_ref": "axiom/echo"},
                {
                    "id": "n2",
                    "node_ref": "axiom/set-fields",
                    "config": {"fields": {"stage": "enriched"}},
                },
            ],
            "edges": [{"from": "n1", "to": "n2"}],
        }
    )

    execution = await _start(engine, graph, {"email": "ada@example.com"})
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None
    assert final.status is ExecutionStatus.SUCCEEDED

    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    # n1 echoed the run input.
    assert states["n1"].output == {"email": "ada@example.com"}
    # n2 received n1's output (data flow) and merged its static field onto it.
    assert states["n2"].output == {"email": "ada@example.com", "stage": "enriched"}


async def test_validation_failure_from_real_node_fails_run_without_retry() -> None:
    # set-fields rejects a non-object `fields` in validate() — terminal, no spend.
    engine, store = _make_stack()
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [
                {
                    "id": "n1",
                    "node_ref": "axiom/set-fields",
                    "config": {"fields": "not-an-object"},
                    "retry": {"max_attempts": 5},
                }
            ],
            "edges": [],
        }
    )

    execution = await _start(engine, graph, {})
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None
    assert final.status is ExecutionStatus.FAILED
    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    # Validation failure is terminal regardless of max_attempts: exactly 1 try.
    assert states["n1"].attempt == 1
    assert states["n1"].status is NodeStatus.FAILED
    assert states["n1"].error is not None
    assert states["n1"].error.error_class == "invalid_input"


async def test_diamond_of_real_nodes_merges_at_fan_in() -> None:
    # n1 → {n2, n3} → n4. Each branch sets a distinct field; n4 echoes the merge.
    engine, store = _make_stack()
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [
                {"id": "n1", "node_ref": "axiom/echo"},
                {"id": "n2", "node_ref": "axiom/set-fields", "config": {"fields": {"a": 1}}},
                {"id": "n3", "node_ref": "axiom/set-fields", "config": {"fields": {"b": 2}}},
                {"id": "n4", "node_ref": "axiom/echo"},
            ],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n1", "to": "n3"},
                {"from": "n2", "to": "n4"},
                {"from": "n3", "to": "n4"},
            ],
        }
    )

    execution = await _start(engine, graph, {"seed": True})
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.SUCCEEDED
    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    # n4 saw both branches' contributions merged (fan-in data flow).
    out = states["n4"].output or {}
    assert out.get("a") == 1
    assert out.get("b") == 2


async def test_unregistered_node_ref_fails_terminally() -> None:
    # A graph pinned to a node that isn't installed → terminal internal error,
    # surfaced cleanly, not a worker crash.
    engine, store = _make_stack()
    graph = WorkflowGraph.from_dict(
        {"nodes": [{"id": "n1", "node_ref": "axiom/does-not-exist"}], "edges": []}
    )

    execution = await _start(engine, graph, {})
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.FAILED
    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert states["n1"].error is not None
    assert states["n1"].error.error_class == "internal"
