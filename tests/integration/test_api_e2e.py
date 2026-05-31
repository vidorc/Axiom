"""End-to-end integration tests for the HTTP + WebSocket API surface.

This is the proof of the user-facing goal: the workflow engine is operable
*entirely through APIs*. Each test drives the real FastAPI app against real
Postgres — author a workflow over HTTP, start a run, drive a worker drain, then
read status/events/history back over HTTP, and tail the live stream over a
WebSocket.

Two transports, deliberately:
  * REST tests use ``httpx.ASGITransport`` with ``app.state.durable`` set to a
    durable engine on the test ``session_factory``. Everything runs on the test's
    own event loop — no lifespan, no process-wide engine, no cross-loop asyncpg
    hazard — and the same engine is used to drain the run, so the test is fully
    self-contained and deterministic.
  * The WebSocket test uses Starlette's ``TestClient`` (httpx's ASGI transport
    can't speak WebSocket). It runs the app's lifespan (process-wide engine on the
    same test DB) and drives create/start/drain on the client's portal loop so
    the asyncpg connections live on the loop the WS handler runs on.

Requires the test datastore (``make test-stack-up``); the session fixture skips
cleanly if Postgres is unreachable.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.api.app import create_app
from axiom.api.dependencies import DEV_ORG_ID
from axiom.composition import build_durable_engine
from axiom.worker.loop import OrchestratorLoop

pytestmark = pytest.mark.integration


# A fan-in DAG over credential-free built-ins (mirrors examples/workflows/hello.yaml).
_GRAPH = {
    "nodes": [
        {"id": "prepare", "node_ref": "axiom/echo"},
        {"id": "tag", "node_ref": "axiom/set-fields", "config": {"fields": {"stage": "lead"}}},
        {"id": "finalize", "node_ref": "axiom/echo"},
    ],
    "edges": [
        {"from": "prepare", "to": "tag"},
        {"from": "tag", "to": "finalize"},
    ],
}

_CYCLIC_GRAPH = {
    "nodes": [{"id": "a", "node_ref": "axiom/echo"}, {"id": "b", "node_ref": "axiom/echo"}],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
}


def _client(session_factory: async_sessionmaker[AsyncSession]) -> httpx.AsyncClient:
    """An async HTTP client over the app, with the durable engine on the test DB.

    Sets ``app.state.durable`` directly (bypassing lifespan) so every request and
    the worker drain share one engine on the test's event loop.
    """
    app = create_app()
    app.state.durable = build_durable_engine(session_factory)
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _drain(client: httpx.AsyncClient) -> int:
    """Run the worker loop once to completion against the app's durable engine."""
    durable = client._transport.app.state.durable  # type: ignore[attr-defined]
    loop = OrchestratorLoop(engine=durable.engine, resolver=durable.resolver)
    return await loop.drain()


# ── Workflow CRUD ──────────────────────────────────────────────────────────────


async def test_create_get_list_workflow(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        # Create.
        resp = await client.post("/v1/workflows", json={"name": "crud-flow", "graph": _GRAPH})
        assert resp.status_code == 201, resp.text
        ids = resp.json()
        workflow_id = ids["workflow_id"]
        assert ids["version_id"]

        # Get — current version points at the published v1.
        resp = await client.get(f"/v1/workflows/{workflow_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "crud-flow"
        assert body["current_version_id"] == ids["version_id"]

        # List — the workflow we just created is present.
        resp = await client.get("/v1/workflows")
        assert resp.status_code == 200
        assert any(w["id"] == workflow_id for w in resp.json())

        # Get the full version back, with its graph.
        resp = await client.get(f"/v1/workflows/{workflow_id}/versions/1")
        assert resp.status_code == 200
        assert resp.json()["graph"] == _GRAPH


async def test_append_version_bumps_current(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        created = (
            await client.post("/v1/workflows", json={"name": "versioned", "graph": _GRAPH})
        ).json()
        workflow_id = created["workflow_id"]

        # Append a second version.
        resp = await client.post(f"/v1/workflows/{workflow_id}/versions", json={"graph": _GRAPH})
        assert resp.status_code == 201, resp.text
        v2_id = resp.json()["version_id"]
        assert v2_id != created["version_id"]

        # Current version advanced to v2; the list shows both, newest first.
        wf = (await client.get(f"/v1/workflows/{workflow_id}")).json()
        assert wf["current_version_id"] == v2_id
        versions = (await client.get(f"/v1/workflows/{workflow_id}/versions")).json()
        assert [v["version"] for v in versions] == [2, 1]


async def test_invalid_graph_is_422_with_problems(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.post("/v1/workflows", json={"name": "bad", "graph": _CYCLIC_GRAPH})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "invalid_graph"
        assert any("cycle" in p for p in detail["problems"])


async def test_unknown_workflow_is_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.get(f"/v1/workflows/{uuid4()}")
        assert resp.status_code == 404


async def test_cross_org_workflow_is_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A workflow created by one org is invisible (404) to another."""
    async with _client(session_factory) as client:
        created = (
            await client.post("/v1/workflows", json={"name": "owned", "graph": _GRAPH})
        ).json()
        other_org = str(uuid4())
        resp = await client.get(
            f"/v1/workflows/{created['workflow_id']}", headers={"X-Org-Id": other_org}
        )
        assert resp.status_code == 404


# ── Execution: start, drive, read back ───────────────────────────────────────────


async def test_start_run_drive_and_read_status_and_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        workflow_id = (
            await client.post("/v1/workflows", json={"name": "runnable", "graph": _GRAPH})
        ).json()["workflow_id"]

        # Start a run (default = current version). 202 Accepted — pending a worker.
        resp = await client.post(
            f"/v1/workflows/{workflow_id}/runs", json={"input": {"lead_id": "L-7"}}
        )
        assert resp.status_code == 202, resp.text
        run = resp.json()
        run_id = run["id"]
        assert run["status"] == "running"

        # No worker process in the test → drive the loop ourselves.
        processed = await _drain(client)
        assert processed == 3

        # Read status: SUCCEEDED, with all three nodes succeeded.
        resp = await client.get(f"/v1/runs/{run_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["status"] == "succeeded"
        node_status = {n["node_id"]: n["status"] for n in detail["nodes"]}
        assert node_status == {"prepare": "succeeded", "tag": "succeeded", "finalize": "succeeded"}

        # The event log replays the run; ids are strictly increasing and a
        # terminal RunSucceeded is present.
        events = (await client.get(f"/v1/runs/{run_id}/events")).json()
        types = [e["type"] for e in events]
        assert "RunStarted" in types and "RunSucceeded" in types
        ids = [e["id"] for e in events]
        assert ids == sorted(ids)

        # after_id paginates: nothing strictly greater than the last id.
        last_id = ids[-1]
        tail = (await client.get(f"/v1/runs/{run_id}/events?after_id={last_id}")).json()
        assert tail == []

        # Run history for the workflow includes this run.
        history = (await client.get(f"/v1/workflows/{workflow_id}/runs")).json()
        assert any(r["id"] == run_id for r in history)


async def test_start_run_unknown_workflow_is_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(session_factory) as client:
        resp = await client.post(f"/v1/workflows/{uuid4()}/runs", json={"input": {}})
        assert resp.status_code == 404


# ── WebSocket live stream ─────────────────────────────────────────────────────────


def test_websocket_streams_run_to_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The live stream replays a finished run's events and closes on terminal.

    Synchronous on purpose: Starlette's TestClient runs the app lifespan (so the
    WS handler uses the process-wide engine on the portal loop) and exposes that
    portal, on which we author + start + drain the run — keeping every asyncpg
    connection on the loop the WebSocket handler will run on.
    """
    with TestClient(create_app()) as client:
        durable = client.app.state.durable
        portal = client.portal  # the blocking portal TestClient runs the app on

        # Author + enqueue + drain entirely on the portal loop.
        async def _setup() -> str:
            _, version_id = await durable.authoring.create_workflow_version(
                org_id=DEV_ORG_ID, name="ws-flow", graph=_GRAPH
            )
            execution = await durable.enqueue_run(
                org_id=DEV_ORG_ID,
                workflow_id=uuid4(),
                workflow_version_id=version_id,
            )
            loop = OrchestratorLoop(engine=durable.engine, resolver=durable.resolver)
            await loop.drain()
            return str(execution.id)

        run_id = portal.call(_setup)

        # Tail the (already-complete) run. We should receive every event ending in
        # a terminal RunSucceeded, after which the server closes the socket.
        received: list[dict] = []
        with client.websocket_connect(f"/v1/runs/{run_id}/stream") as ws:
            while True:
                try:
                    received.append(ws.receive_json())
                except Exception:
                    break  # server closed after the terminal event

        types = [e["type"] for e in received]
        assert "RunStarted" in types
        assert types[-1] == "RunSucceeded"
        # Strictly increasing ids — gap-free, ordered tail.
        ids = [e["id"] for e in received]
        assert ids == sorted(ids) and len(ids) == len(set(ids))
