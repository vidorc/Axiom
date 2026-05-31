"""Integration test for the durable worker loop (OrchestratorLoop) end to end.

This is the proof that the worker entrypoint's wiring is sound: it drives the
*real* durable path — authoring persistence → durable enqueue → the DB graph
resolver → ``OrchestratorLoop.drain`` → real built-in nodes → ``PostgresStore``
settle — and asserts a workflow runs to SUCCEEDED through the same objects the
``axiom-worker`` process assembles (``build_durable_engine`` +
``OrchestratorLoop``). No engine internals or test doubles: if this passes, a
worker process can take a stored workflow version and run it to completion.

Requires the test datastore (``make test-stack-up``); the session fixture skips
cleanly if Postgres is unreachable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.composition import build_durable_engine
from axiom.domains.execution.state import ExecutionStatus, NodeStatus
from axiom.worker.loop import OrchestratorLoop

pytestmark = pytest.mark.integration


# A fan-in DAG over credential-free built-ins (mirrors examples/workflows/hello.yaml):
# prepare → {tag_lead, tag_source} → finalize. Exercises parallel branches and a
# fan-in join through the real loop, not just a linear chain.
_GRAPH = {
    "nodes": [
        {"id": "prepare", "node_ref": "axiom/echo"},
        {"id": "tag_lead", "node_ref": "axiom/set-fields", "config": {"fields": {"stage": "lead"}}},
        {
            "id": "tag_source",
            "node_ref": "axiom/set-fields",
            "config": {"fields": {"source": "worker-loop-test"}},
        },
        {"id": "finalize", "node_ref": "axiom/echo"},
    ],
    "edges": [
        {"from": "prepare", "to": "tag_lead"},
        {"from": "prepare", "to": "tag_source"},
        {"from": "tag_lead", "to": "finalize"},
        {"from": "tag_source", "to": "finalize"},
    ],
}


async def test_orchestrator_loop_drains_a_stored_workflow_to_completion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    durable = build_durable_engine(session_factory)
    org_id = uuid4()

    # Author + publish a workflow version (the design-time half).
    workflow_id, version_id = await durable.authoring.create_workflow_version(
        org_id=org_id, name="worker-loop-flow", graph=_GRAPH
    )

    # Durable enqueue: persist a run pinned to that version. Nothing executes yet.
    execution = await durable.enqueue_run(
        org_id=org_id,
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_input={"lead_id": "L-1"},
    )

    # Drive the worker loop exactly as the process does: reap once, then tick
    # until nothing is claimable. The resolver rehydrates the graph per claim.
    loop = OrchestratorLoop(engine=durable.engine, resolver=durable.resolver)
    processed = await loop.drain()

    # Every node ran through the loop (4 nodes in the fan-in DAG).
    assert processed == 4

    final = await durable.store.get_execution(execution.id)
    assert final is not None
    assert final.status is ExecutionStatus.SUCCEEDED

    states = {s.node_id: s for s in await durable.store.get_node_states(execution.id)}
    assert all(s.status is NodeStatus.SUCCEEDED for s in states.values())

    # Data flowed: the run input reached the roots, and set-fields merged its
    # configured field onto the echoed input on each branch.
    assert states["tag_lead"].output is not None
    assert states["tag_lead"].output.get("stage") == "lead"
    assert states["tag_lead"].output.get("lead_id") == "L-1"
    assert states["tag_source"].output is not None
    assert states["tag_source"].output.get("source") == "worker-loop-test"


async def test_drain_on_empty_queue_processes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A drain with no enqueued runs is a clean no-op (the idle worker case)."""
    durable = build_durable_engine(session_factory)
    loop = OrchestratorLoop(engine=durable.engine, resolver=durable.resolver)
    assert await loop.drain() == 0
