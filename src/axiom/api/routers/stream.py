"""Live execution streaming over WebSocket — a durable tail of the event log.

``WS /v1/runs/{execution_id}/stream`` pushes the run's events as they land, then
closes when the run reaches a terminal state. The mechanism is the simplest thing
that is correct for Phase 1: poll the append-only event log by its global
monotonic ``id`` (the same primitive the REST replay uses), tracking a high-water
mark so every event is delivered exactly once and in order.

Why a DB tail and not Redis pub/sub: Postgres is the source of truth (ADR-0001);
Redis is transport we haven't needed yet. A poll-the-log tail is durable
(survives a dropped connection — reconnect resumes from the last id), gap-free,
and has no missed-publish race. If fan-out cost ever matters we can layer Redis
pub/sub *in front* of this exact contract without changing clients.

Auth: browsers can't set custom headers on a WebSocket, so org scope comes from
the ``org_id`` query param (falling back to the ``X-Org-Id`` header for non-browser
clients, then the dev org). A run that isn't the caller's is closed as 1008. This
mirrors the REST ``get_org_id`` placeholder — real auth is WS-4.
"""

from __future__ import annotations

import asyncio
import contextlib
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from axiom.api.dependencies import DEV_ORG_ID
from axiom.composition import DurableEngine
from axiom.domains.execution.state import EventType
from axiom.platform.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["executions"])

# WebSocket close codes.
_CLOSE_POLICY_VIOLATION = 1008
_CLOSE_INTERNAL_ERROR = 1011

# Once one of these is emitted, the run is over and the stream closes.
_TERMINAL_RUN_EVENTS = frozenset(
    {EventType.RUN_SUCCEEDED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}
)

# Poll cadence and an *idle* safety deadline: a connection that produces no new
# events for this long is closed so a run that never finishes (e.g. no worker is
# up) can't become a zombie. The deadline tracks idleness — it resets whenever an
# event is delivered — so an actively-streaming run is never cut off mid-flight.
_POLL_INTERVAL_SECONDS = 0.5
_MAX_IDLE_SECONDS = 600.0


def _resolve_org(websocket: WebSocket) -> UUID:
    """Org scope from the ``org_id`` query param, then ``X-Org-Id``, then dev org."""
    raw = websocket.query_params.get("org_id") or websocket.headers.get("x-org-id")
    if raw is None:
        return DEV_ORG_ID
    try:
        return UUID(raw)
    except ValueError:
        return DEV_ORG_ID


@router.websocket("/v1/runs/{execution_id}/stream")
async def stream_run(websocket: WebSocket, execution_id: UUID) -> None:
    """Stream a run's events live, closing when the run terminates."""
    durable: DurableEngine | None = getattr(websocket.app.state, "durable", None)
    await websocket.accept()
    if durable is None:  # pragma: no cover - lifespan always sets it
        await websocket.close(code=_CLOSE_INTERNAL_ERROR, reason="engine not initialised")
        return

    org_id = _resolve_org(websocket)
    execution = await durable.store.get_execution(execution_id)
    if execution is None or execution.org_id != org_id:
        await websocket.close(code=_CLOSE_POLICY_VIOLATION, reason="run not found")
        return

    logger.info("stream.opened", execution_id=str(execution_id))
    last_id = 0
    idle = 0.0
    try:
        while True:
            # A transient DB blip must not kill a tail the docstring calls
            # "durable" — log it and retry on the next tick rather than closing.
            try:
                events = await durable.store.get_events(execution_id, after_id=last_id)
            except Exception:
                logger.warning("stream.poll_error", execution_id=str(execution_id))
                events = []

            terminal = False
            for ev in events:
                await websocket.send_json(
                    {
                        "id": ev.id,
                        "seq": ev.seq,
                        "type": ev.type.value,
                        "node_id": ev.node_id,
                        "attempt": ev.attempt,
                        "payload": ev.payload,
                        "created_at": ev.created_at.isoformat() if ev.created_at else None,
                    }
                )
                last_id = ev.id
                if ev.type in _TERMINAL_RUN_EVENTS:
                    terminal = True

            if terminal:
                break
            # The deadline bounds *idleness*: progress resets it, so an actively
            # streaming run never gets cut off mid-flight; only a silent run does.
            idle = 0.0 if events else idle + _POLL_INTERVAL_SECONDS
            if idle >= _MAX_IDLE_SECONDS:
                logger.info("stream.idle_deadline", execution_id=str(execution_id))
                break

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        # Graceful close after the terminal event (or deadline).
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.close()
    except WebSocketDisconnect:
        # The client went away mid-stream — normal, not an error. They can
        # reconnect and resume from their last id (the tail is durable).
        logger.info("stream.disconnected", execution_id=str(execution_id), last_id=last_id)
    finally:
        if websocket.client_state is WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError):
                await websocket.close()
