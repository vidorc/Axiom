"""Execution service endpoints — start runs, read status/history, stream live.

The run-time surface over the durable engine (``DurableEngine``):

  * ``POST /v1/workflows/{id}/runs`` — pin a version and enqueue a run. A worker
    process (``axiom-worker``) claims and executes it; this call only persists it.
  * ``GET  /v1/runs/{id}`` — a run's status + per-node states.
  * ``GET  /v1/runs/{id}/events`` — the append-only event log (full replay).
  * ``GET  /v1/workflows/{id}/runs`` — a workflow's run history.
  * ``WS   /v1/runs/{id}/stream`` — live event tail (see ``stream.py``).

Every run is org-scoped: a run that isn't the caller's reads as 404. Starting a
run resolves the version through the authoring store (current version by default,
or an explicit version number), so the run pins an exact immutable definition
(DOMAIN_MODEL.md §3).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from axiom.api.dependencies import Durable, OrgId
from axiom.api.schemas import (
    EventResponse,
    ExecutionDetailResponse,
    ExecutionResponse,
    NodeErrorResponse,
    NodeStateResponse,
    StartRunRequest,
)
from axiom.domains.execution.state import Execution, NodeState
from axiom.domains.workflow_authoring import WorkflowNotFoundError

router = APIRouter(tags=["executions"])


# ── Mapping helpers: domain value objects → response schemas ──────────────────


def _execution_response(e: Execution) -> ExecutionResponse:
    return ExecutionResponse(
        id=e.id,
        workflow_id=e.workflow_id,
        workflow_version_id=e.workflow_version_id,
        status=e.status.value,
        trigger=e.trigger,
        total_cost_cents=e.total_cost_cents,
        started_at=e.started_at,
        finished_at=e.finished_at,
    )


def _node_response(n: NodeState) -> NodeStateResponse:
    return NodeStateResponse(
        node_id=n.node_id,
        status=n.status.value,
        attempt=n.attempt,
        output=n.output,
        cost_cents=n.cost_cents,
        error=(
            NodeErrorResponse(
                error_class=n.error.error_class,
                message=n.error.message,
                retryable=n.error.retryable,
                retry_after_seconds=n.error.retry_after_seconds,
            )
            if n.error is not None
            else None
        ),
        started_at=n.started_at,
        finished_at=n.finished_at,
    )


async def _load_owned_execution(durable: Durable, org_id: UUID, execution_id: UUID) -> Execution:
    """Fetch a run, enforcing org ownership. 404 if absent or another org's."""
    execution = await durable.store.get_execution(execution_id)
    if execution is None or execution.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return execution


# ── Start a run ───────────────────────────────────────────────────────────────


@router.post(
    "/v1/workflows/{workflow_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExecutionResponse,
)
async def start_run(
    workflow_id: UUID, body: StartRunRequest, org_id: OrgId, durable: Durable
) -> ExecutionResponse:
    """Enqueue a run of a workflow version (a worker executes it asynchronously).

    Resolves which version to pin (the current version, or ``body.version``),
    then persists the run + node states + opening events. Returns 202 — the run
    is accepted and RUNNING-pending-a-worker, not finished.
    """
    try:
        version_id = await durable.authoring.resolve_runnable_version(
            org_id=org_id, workflow_id=workflow_id, version=body.version
        )
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    execution = await durable.enqueue_run(
        org_id=org_id,
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        run_input=body.input,
    )
    return _execution_response(execution)


# ── Read a run ────────────────────────────────────────────────────────────────


@router.get("/v1/runs/{execution_id}", response_model=ExecutionDetailResponse)
async def get_run(execution_id: UUID, org_id: OrgId, durable: Durable) -> ExecutionDetailResponse:
    """A run's status plus its per-node states."""
    execution = await _load_owned_execution(durable, org_id, execution_id)
    states = await durable.store.get_node_states(execution_id)
    base = _execution_response(execution)
    return ExecutionDetailResponse(
        **base.model_dump(),
        nodes=[_node_response(n) for n in states],
    )


@router.get("/v1/runs/{execution_id}/events", response_model=list[EventResponse])
async def get_run_events(
    execution_id: UUID,
    org_id: OrgId,
    durable: Durable,
    after_id: int = Query(0, ge=0, description="Return only events with id greater than this."),
    limit: int = Query(1000, ge=1, le=5000),
) -> list[EventResponse]:
    """Replay a run's append-only event log (ordered by global monotonic id)."""
    await _load_owned_execution(durable, org_id, execution_id)
    events = await durable.store.get_events(execution_id, after_id=after_id, limit=limit)
    return [
        EventResponse(
            id=e.id,
            seq=e.seq,
            type=e.type.value,
            node_id=e.node_id,
            attempt=e.attempt,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in events
    ]


@router.get("/v1/workflows/{workflow_id}/runs", response_model=list[ExecutionResponse])
async def list_workflow_runs(
    workflow_id: UUID,
    org_id: OrgId,
    durable: Durable,
    limit: int = Query(100, ge=1, le=500),
) -> list[ExecutionResponse]:
    """A workflow's run history, newest first."""
    # Confirm the workflow is the caller's (404 otherwise) before listing its runs.
    try:
        await durable.authoring.get_workflow(org_id=org_id, workflow_id=workflow_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    runs = await durable.store.list_executions(org_id=org_id, workflow_id=workflow_id, limit=limit)
    return [_execution_response(r) for r in runs]
