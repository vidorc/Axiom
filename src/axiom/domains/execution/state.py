"""Execution state vocabulary: the status + event enums and state records.

These mirror the data model in DOMAIN_MODEL.md §4 (the orchestrator's work
tables) but as *pure value objects*, deliberately decoupled from any storage. The
scheduler in ``engine.py`` operates on these; a ``Store`` (in-memory for tests,
Postgres for production) persists them. Keeping them storage-free is what lets
the engine's scheduling logic be unit-tested without a database — the place where
DAG/retry/lease bugs actually live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from axiom.sdk import JsonObject


class ExecutionStatus(StrEnum):
    """Lifecycle of a whole run (DOMAIN_MODEL.md §4 — execution.status)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_EXECUTION_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
)


class NodeStatus(StrEnum):
    """Lifecycle of a single node within a run (DOMAIN_MODEL.md §4 — node_state).

    The ``PENDING → READY → RUNNING → SUCCEEDED|FAILED`` spine is the engine's
    core state machine. ``SKIPPED`` is for nodes whose inbound edge condition was
    falsy. ``COMPENSATING/COMPENSATED`` are the opt-in rollback states.
    """

    PENDING = "pending"  # waiting on upstream dependencies
    READY = "ready"  # all dependencies satisfied; claimable by a worker
    RUNNING = "running"  # claimed and executing (holds a lease)
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # an inbound edge condition was falsy → not run
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"


TERMINAL_NODE_STATUSES: frozenset[NodeStatus] = frozenset(
    {NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.COMPENSATED}
)


class EventType(StrEnum):
    """The append-only execution event log (DOMAIN_MODEL.md §4 — execution_event).

    Execution is event-sourced: every state change is an immutable event, and
    current state is a projection of this log (BLUEPRINT.md §3.3). History
    replay, the live run viewer, and the audit trail all fall out of this one
    log. The enum is the closed set of things that can happen in a run.
    """

    RUN_REQUESTED = "RunRequested"
    RUN_STARTED = "RunStarted"
    NODE_READY = "NodeReady"
    NODE_STARTED = "NodeStarted"
    NODE_RETRIED = "NodeRetried"
    NODE_SUCCEEDED = "NodeSucceeded"
    NODE_FAILED = "NodeFailed"
    NODE_SKIPPED = "NodeSkipped"
    NODE_RECOVERED = "NodeRecovered"  # reaper reset an expired lease (crash recovery)
    NODE_COMPENSATING = "NodeCompensating"
    NODE_COMPENSATED = "NodeCompensated"
    RUN_SUCCEEDED = "RunSucceeded"
    RUN_FAILED = "RunFailed"
    RUN_CANCELLED = "RunCancelled"


@dataclass(frozen=True, slots=True)
class NodeError:
    """A typed node failure as persisted on node_state.error (jsonb).

    Distinct from ``axiom.sdk.NodeError`` (the *exception* a node raises): this
    is the serialized record the engine stores and the retry logic reads. We
    keep the error *class* (string from the SDK taxonomy) and whether it was
    retryable so the projection/UI can explain why a run failed.
    """

    error_class: str
    message: str
    retryable: bool
    retry_after_seconds: float | None = None


@dataclass(slots=True)
class NodeState:
    """One row per node per run — the orchestrator's unit of work.

    Mirrors DOMAIN_MODEL.md §4 node_state. Mutable because the engine advances
    it through the state machine; the ``Store`` persists each transition. The
    idempotency key for any side effect is ``(execution_id, node_id, attempt)``.
    """

    execution_id: UUID
    node_id: str  # graph-local id
    status: NodeStatus = NodeStatus.PENDING
    attempt: int = 0  # incremented to 1 on first claim
    input: JsonObject = field(default_factory=dict)
    output: JsonObject | None = None
    cost_cents: int = 0
    # Crash-recovery lease: a RUNNING node holds this; the reaper resets nodes
    # whose lease has expired (the worker presumably died).
    lease_until: datetime | None = None
    # When status is READY due to a scheduled retry, the engine must not claim
    # it before this time (backoff). None means "claimable now".
    not_before: datetime | None = None
    error: NodeError | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class Execution:
    """A run (DOMAIN_MODEL.md §4 — execution)."""

    id: UUID
    org_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    status: ExecutionStatus = ExecutionStatus.QUEUED
    trigger: str = "manual"
    input: JsonObject = field(default_factory=dict)
    total_cost_cents: int = 0
    stats: JsonObject = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """One immutable entry in the execution event log."""

    execution_id: UUID
    org_id: UUID
    seq: int  # per-execution monotonic ordering
    type: EventType
    payload: JsonObject = field(default_factory=dict)
    node_id: str | None = None
    attempt: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExecutionEventRecord:
    """A persisted event as read back for the API + live stream.

    Distinct from ``ExecutionEvent`` (the write shape, whose ``id`` is unknown
    until insert): this carries the autoincrement ``id`` that is the *global
    monotonic ordering authority* (DOMAIN_MODEL.md §4). The websocket tail uses it
    as the high-water mark — "give me events with id greater than the last I sent"
    — which is durable, gap-free, and survives reconnection.
    """

    id: int
    execution_id: UUID
    org_id: UUID
    seq: int
    type: EventType
    payload: JsonObject
    node_id: str | None
    attempt: int | None
    created_at: datetime | None
