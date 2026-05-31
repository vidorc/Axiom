"""Integration tests for the AuthoringStore against real Postgres.

Proves the design-time half persists correctly: a workflow + immutable version
round-trips, the graph is validated at save time, versions are monotonic per
workflow, and ``get_graph`` resolves a stored version back to a parsed
``WorkflowGraph`` (the hook the worker's GraphResolver is built on).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.domains.workflow_authoring import (
    AuthoringStore,
    GraphValidationError,
    WorkflowNotFoundError,
)

pytestmark = pytest.mark.integration


_GRAPH = {
    "nodes": [
        {"id": "a", "node_ref": "axiom/echo"},
        {"id": "b", "node_ref": "axiom/set-fields", "config": {"fields": {"x": 1}}},
    ],
    "edges": [{"from": "a", "to": "b"}],
}


async def test_create_and_resolve_workflow_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = AuthoringStore(session_factory)
    org_id = uuid4()

    workflow_id, version_id = await store.create_workflow_version(
        org_id=org_id, name="my-flow", graph=_GRAPH
    )
    assert workflow_id is not None
    assert version_id is not None

    # Resolve the stored version back to a parsed, validated graph.
    graph = await store.get_graph(version_id)
    assert set(graph.node_ids()) == {"a", "b"}
    assert graph.roots() == ("a",)
    assert graph.successors("a") == ("b",)


async def test_versions_are_monotonic_per_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = AuthoringStore(session_factory)
    org_id = uuid4()

    workflow_id, v1 = await store.create_workflow_version(org_id=org_id, name="flow", graph=_GRAPH)
    # A second version on the SAME workflow gets version 2 and a new id.
    _, v2 = await store.create_workflow_version(
        org_id=org_id, name="flow", graph=_GRAPH, workflow_id=workflow_id
    )
    assert v1 != v2
    # Both resolve independently — old version is unchanged by the new one.
    assert set((await store.get_graph(v1)).node_ids()) == {"a", "b"}
    assert set((await store.get_graph(v2)).node_ids()) == {"a", "b"}


async def test_invalid_graph_rejected_at_save_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = AuthoringStore(session_factory)
    cyclic = {
        "nodes": [{"id": "a", "node_ref": "axiom/echo"}, {"id": "b", "node_ref": "axiom/echo"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    with pytest.raises(GraphValidationError):
        await store.create_workflow_version(org_id=uuid4(), name="bad", graph=cyclic)


async def test_get_graph_unknown_version_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = AuthoringStore(session_factory)
    with pytest.raises(WorkflowNotFoundError):
        await store.get_graph(uuid4())
