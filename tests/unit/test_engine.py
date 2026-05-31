"""Unit tests for the workflow engine — the highest-priority component.

These run against the in-memory Store and a programmable fake NodeInvoker, with
an injected fake Clock so lease-expiry and backoff are deterministic. No
database, no real nodes — the scheduling logic (ready-set, data flow, retries,
recovery, termination) is exercised directly in milliseconds. The Postgres store
later reproduces this same behaviour under the chaos lane.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from axiom.domains.execution.engine import EngineConfig, WorkflowEngine
from axiom.domains.execution.protocols import InvocationOutcome
from axiom.domains.execution.state import ExecutionStatus, NodeStatus
from axiom.domains.execution.store_memory import InMemoryStore
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.sdk import JsonObject

pytestmark = pytest.mark.unit


# ── Test doubles ─────────────────────────────────────────────────────────────────


class FakeClock:
    """A clock the test advances by hand. Default start is fixed for determinism."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class _Invocation:
    def __init__(self, node_id: str, attempt: int, inputs: JsonObject) -> None:
        self.node_id = node_id
        self.attempt = attempt
        self.inputs = inputs


class FakeInvoker:
    """Programmable NodeInvoker.

    By default every node succeeds with output ``{"ok": node_id}``. Per-node
    behaviour is programmed via ``program``:
      * a callable ``(invocation) -> InvocationOutcome`` for full control
      * the special "fail_then_succeed" helper via ``fail_times``
    Every call is recorded in ``calls`` for assertions on attempt keys + inputs.
    """

    def __init__(self) -> None:
        self.calls: list[_Invocation] = []
        self._programs: dict[str, object] = {}
        self._fail_times: dict[str, int] = {}
        self._outputs: dict[str, JsonObject] = {}

    def program(self, node_id: str, fn: object) -> None:
        self._programs[node_id] = fn

    def fail_then_succeed(
        self, node_id: str, *, fail_times: int, error_class: str = "timeout", retryable: bool = True
    ) -> None:
        self._fail_times[node_id] = fail_times
        self._programs[node_id] = ("fail_then_succeed", error_class, retryable)

    def set_output(self, node_id: str, output: JsonObject) -> None:
        self._outputs[node_id] = output

    async def invoke(
        self,
        *,
        node_ref: str,
        config: JsonObject,
        credentials: dict[str, str],
        inputs: JsonObject,
        run_id: object,
        org_id: object,
        node_id: str,
        attempt: int,
    ) -> InvocationOutcome:
        self.calls.append(_Invocation(node_id, attempt, dict(inputs)))
        prog = self._programs.get(node_id)

        if callable(prog):
            return prog(self.calls[-1])  # type: ignore[no-any-return, operator]

        if isinstance(prog, tuple) and prog[0] == "fail_then_succeed":
            _, error_class, retryable = prog
            if attempt <= self._fail_times[node_id]:
                return InvocationOutcome.failure(
                    node_id=node_id,
                    attempt=attempt,
                    error_class=error_class,
                    error_message="programmed failure",
                    retryable=retryable,
                )
            # fall through to success

        output = self._outputs.get(node_id, {"ok": node_id})
        return InvocationOutcome.success(
            node_id=node_id, attempt=attempt, output=output, cost_cents=1
        )

    def call_count(self, node_id: str) -> int:
        return sum(1 for c in self.calls if c.node_id == node_id)


# ── Graph builders ───────────────────────────────────────────────────────────────


def linear_graph(n: int = 3) -> WorkflowGraph:
    """n1 -> n2 -> ... -> nN (a chain)."""
    nodes = [{"id": f"n{i}", "node_ref": "test/echo"} for i in range(1, n + 1)]
    edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(1, n)]
    return WorkflowGraph.from_dict({"nodes": nodes, "edges": edges})


def diamond_graph() -> WorkflowGraph:
    """n1 -> {n2, n3} -> n4 (fan-out then fan-in)."""
    return WorkflowGraph.from_dict(
        {
            "nodes": [{"id": n, "node_ref": "test/echo"} for n in ("n1", "n2", "n3", "n4")],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n1", "to": "n3"},
                {"from": "n2", "to": "n4"},
                {"from": "n3", "to": "n4"},
            ],
        }
    )


def make_engine(
    store: InMemoryStore, invoker: FakeInvoker, clock: FakeClock, *, lease_ttl: float = 30.0
) -> WorkflowEngine:
    return WorkflowEngine(
        store=store,
        invoker=invoker,
        clock=clock,
        config=EngineConfig(lease_ttl=timedelta(seconds=lease_ttl)),
    )


async def start(engine: WorkflowEngine, graph: WorkflowGraph, run_input: JsonObject | None = None):
    return await engine.start_execution(
        org_id=uuid4(),
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        graph=graph,
        run_input=run_input,
    )


# ── Happy paths ────────────────────────────────────────────────────────────────────


async def test_linear_chain_runs_in_dependency_order() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    engine = make_engine(store, invoker, clock)
    graph = linear_graph(3)

    execution = await start(engine, graph)
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None
    assert final.status is ExecutionStatus.SUCCEEDED
    # Each node ran exactly once, in order.
    assert [c.node_id for c in invoker.calls] == ["n1", "n2", "n3"]
    states = {s.node_id: s for s in await store.get_node_states(execution.id)}
    assert all(s.status is NodeStatus.SUCCEEDED for s in states.values())


async def test_diamond_fan_out_and_fan_in() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    engine = make_engine(store, invoker, clock)
    graph = diamond_graph()

    execution = await start(engine, graph)
    await engine.run_to_completion(graph)

    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.SUCCEEDED
    order = [c.node_id for c in invoker.calls]
    # n1 first, n4 last; n2/n3 in between in either order.
    assert order[0] == "n1"
    assert order[-1] == "n4"
    assert set(order[1:3]) == {"n2", "n3"}
    assert invoker.call_count("n4") == 1  # fan-in ran exactly once


async def test_fan_in_node_waits_for_all_parents() -> None:
    # n4 must NOT be claimable until BOTH n2 and n3 have succeeded.
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    engine = make_engine(store, invoker, clock)
    graph = diamond_graph()
    await start(engine, graph)

    await engine.step(graph)  # n1
    await engine.step(graph)  # one of n2/n3
    # After n1 + one branch, n4 still has an unsatisfied parent → not claimable yet.
    states = {s.node_id: s for s in await store.get_node_states(_only_execution(store))}
    assert states["n4"].status is NodeStatus.PENDING


# ── Data flow between nodes ──────────────────────────────────────────────────────────


async def test_output_forwards_to_next_node_with_empty_mapping() -> None:
    # An empty edge mapping forwards the upstream output wholesale.
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    invoker.set_output("n1", {"email": "ada@example.com", "score": 90})
    engine = make_engine(store, invoker, clock)
    graph = linear_graph(2)

    await start(engine, graph)
    await engine.run_to_completion(graph)

    n2_call = next(c for c in invoker.calls if c.node_id == "n2")
    assert n2_call.inputs["email"] == "ada@example.com"
    assert n2_call.inputs["score"] == 90


async def test_explicit_mapping_remaps_fields() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    invoker.set_output("n1", {"person": {"name": "Ada"}})
    engine = make_engine(store, invoker, clock)
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [
                {"id": "n1", "node_ref": "test/echo"},
                {"id": "n2", "node_ref": "test/echo"},
            ],
            "edges": [
                {
                    "from": "n1",
                    "to": "n2",
                    "mapping": {"full_name": "{{ source.person.name }}", "kind": "lead"},
                }
            ],
        }
    )

    await start(engine, graph)
    await engine.run_to_completion(graph)

    n2_call = next(c for c in invoker.calls if c.node_id == "n2")
    assert n2_call.inputs["full_name"] == "Ada"  # remapped via expression
    assert n2_call.inputs["kind"] == "lead"  # literal in mapping


async def test_run_input_reaches_root_node() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    engine = make_engine(store, invoker, clock)
    graph = linear_graph(1)

    await start(engine, graph, run_input={"seed": "value"})
    await engine.run_to_completion(graph)

    assert invoker.calls[0].inputs["seed"] == "value"


# ── Retries ──────────────────────────────────────────────────────────────────────────


async def test_retryable_error_is_retried_then_succeeds() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    invoker.fail_then_succeed("n1", fail_times=2)  # fail attempts 1,2; succeed on 3
    engine = make_engine(store, invoker, clock)
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [
                {
                    "id": "n1",
                    "node_ref": "test/flaky",
                    "retry": {"max_attempts": 3, "backoff": "fixed", "base_delay_seconds": 5},
                }
            ],
            "edges": [],
        }
    )
    execution = await start(engine, graph)

    # Drive: each failed attempt schedules a retry with not_before in the future.
    await engine.step(graph)  # attempt 1 → fail → retry scheduled (+5s)
    assert invoker.call_count("n1") == 1
    # Not claimable yet (backoff).
    assert await engine.step(graph) is False
    clock.advance(5)
    await engine.step(graph)  # attempt 2 → fail → retry (+5s)
    clock.advance(5)
    await engine.step(graph)  # attempt 3 → success
    assert invoker.call_count("n1") == 3

    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.SUCCEEDED


async def test_retry_exhaustion_fails_the_run() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    invoker.fail_then_succeed("n1", fail_times=99)  # always fails
    engine = make_engine(store, invoker, clock)
    graph = WorkflowGraph.from_dict(
        {
            "nodes": [
                {
                    "id": "n1",
                    "node_ref": "test/flaky",
                    "retry": {"max_attempts": 2, "backoff": "fixed", "base_delay_seconds": 1},
                }
            ],
            "edges": [],
        }
    )
    execution = await start(engine, graph)

    await engine.step(graph)  # attempt 1 → fail → retry
    clock.advance(1)
    await engine.step(graph)  # attempt 2 → fail → exhausted → run fails
    assert invoker.call_count("n1") == 2

    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.FAILED


async def test_non_retryable_error_fails_immediately() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    invoker.fail_then_succeed("n1", fail_times=99, error_class="auth_error", retryable=False)
    engine = make_engine(store, invoker, clock)
    graph = WorkflowGraph.from_dict(
        {"nodes": [{"id": "n1", "node_ref": "test/x", "retry": {"max_attempts": 5}}], "edges": []}
    )
    execution = await start(engine, graph)

    await engine.step(graph)  # one attempt, terminal (not retryable) despite attempts left
    assert invoker.call_count("n1") == 1
    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.FAILED


async def test_downstream_does_not_run_when_upstream_fails() -> None:
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    invoker.fail_then_succeed("n1", fail_times=99, error_class="internal", retryable=False)
    engine = make_engine(store, invoker, clock)
    graph = linear_graph(2)
    await start(engine, graph)

    await engine.run_to_completion(graph)
    assert invoker.call_count("n1") == 1
    assert invoker.call_count("n2") == 0  # never reached


# ── Crash recovery (the chaos guarantee, exercised in-memory) ────────────────────────


async def test_reaper_recovers_expired_lease() -> None:
    # Simulate a worker that claimed n1 then "died" (never settled). After the
    # lease expires the reaper resets it; another step re-claims and completes.
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    engine = make_engine(store, invoker, clock, lease_ttl=30)
    graph = linear_graph(1)
    execution = await start(engine, graph)

    # Claim directly (simulate the worker grabbing it but crashing before settle).
    claimed = await store.claim_next_ready(now=clock.now(), lease_ttl_seconds=30)
    assert claimed is not None and claimed.node_id == "n1"
    assert claimed.attempt == 1

    # Before lease expiry: nothing to recover.
    assert await engine.reap() == []

    # Advance past the lease; reaper resets the node to READY.
    clock.advance(31)
    recovered = await engine.reap()
    assert [n.node_id for n in recovered] == ["n1"]

    # Another worker now completes it. Recovery means attempt advances to 2.
    await engine.step(graph)
    final = await store.get_execution(execution.id)
    assert final is not None and final.status is ExecutionStatus.SUCCEEDED
    assert invoker.call_count("n1") == 1  # the fake only counts the post-recovery run


async def test_recovered_run_resumes_from_where_it_stopped() -> None:
    # In a 3-chain, crash after n1 succeeded and n2 was claimed. Recovery should
    # only re-run n2, never re-run the already-succeeded n1.
    store, invoker, clock = InMemoryStore(), FakeInvoker(), FakeClock()
    engine = make_engine(store, invoker, clock, lease_ttl=10)
    graph = linear_graph(3)
    await start(engine, graph)

    await engine.step(graph)  # n1 succeeds, promotes n2
    claimed = await store.claim_next_ready(now=clock.now(), lease_ttl_seconds=10)
    assert claimed is not None and claimed.node_id == "n2"  # worker grabs n2 then "crashes"

    clock.advance(11)
    await engine.reap()  # n2 back to READY
    await engine.run_to_completion(graph)  # n2 (re-run), then n3

    assert invoker.call_count("n1") == 1  # NOT re-run
    assert invoker.call_count("n3") == 1


def _only_execution(store: InMemoryStore):
    # tiny helper for tests that started exactly one run
    return next(iter(store._executions))
