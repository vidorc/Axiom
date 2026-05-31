"""The interfaces the engine depends on — dependency inversion at the core.

The engine (``engine.py``) is pure orchestration logic. It must not know whether
state lives in memory or Postgres, whether a node is HTTP or Python, or what
"now" is. So it depends only on the three Protocols defined here:

  * ``Clock``       — time, made injectable so lease-expiry and backoff are
                      deterministically testable (no ``sleep``, no wall-clock).
  * ``NodeInvoker`` — how the engine runs a node. Defined HERE, in execution, and
                      satisfied *structurally* by ``axiom.domains.node_runtime``.
                      Because it is a Protocol, node_runtime never imports
                      execution (import-linter Contract 2: "node runtime must not
                      depend on execution") while execution still calls into it
                      (BLUEPRINT.md §4: "Execution depends on Node Runtime through
                      an interface, never the reverse").
  * ``Store``       — persistence + the atomic claim primitive. The in-memory
                      implementation powers fast unit tests; the Postgres one
                      provides the durability and crash-recovery guarantees
                      (SKIP LOCKED claim, lease reaper).

This is what makes the most important component in the system unit-testable
without a database — the scheduling bugs (lost wakeups, double-claims, retry
miscounts) are caught in milliseconds, and the Postgres store is then verified
against the same contract plus the chaos lane.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from axiom.domains.execution.state import (
    Execution,
    ExecutionEvent,
    NodeState,
)
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.sdk import JsonObject


@runtime_checkable
class Clock(Protocol):
    """Injectable time source. Tests use a controllable fake; prod uses the wall."""

    def now(self) -> datetime: ...


class SystemClock:
    """Real wall-clock, timezone-aware UTC (all engine timestamps are UTC)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@runtime_checkable
class GraphResolver(Protocol):
    """Resolves a run to its (validated) workflow graph.

    A worker claims a node from *any* run, so — unlike the single-graph CLI
    driver — it cannot be handed one graph up front. It needs to rehydrate the
    claimed run's definition. This is that hook: given an execution_id, return
    the ``WorkflowGraph`` the run is pinned to (from the run's
    ``workflow_version``). The engine calls it after claiming, before invoking.

    Kept as a Protocol (not a hard dependency on workflow_authoring's store) so
    the engine stays storage-agnostic: tests pass a constant resolver, the worker
    passes one backed by the authoring store.
    """

    async def __call__(self, execution_id: UUID) -> WorkflowGraph: ...


@runtime_checkable
class NodeInvoker(Protocol):
    """Runs a single node attempt and returns its outcome.

    The engine hands over everything an invocation needs (which node, its config,
    resolved inputs, the run identity for the context + idempotency key) and gets
    back a structured outcome. The invoker is responsible for: loading the node
    at its pinned version, building the instrumented ExecutionContext, unwrapping
    only the declared credentials, running validate()+execute(), and translating
    a raised ``axiom.sdk.NodeError`` into ``InvocationOutcome``.

    The engine never imports node_runtime; it receives an object satisfying this
    shape (wired at the delivery/worker layer).
    """

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
    ) -> InvocationOutcome: ...


class InvocationOutcome:
    """The result of one node invocation, as the engine consumes it.

    Deliberately a plain class (not the SDK's exception) so the boundary is
    explicit: the invoker catches the SDK exception and reports a value. Success
    carries the output + cost; failure carries the taxonomy class, whether it is
    retryable, and an optional Retry-After hint for rate limits.
    """

    __slots__ = (
        "attempt",
        "cost_cents",
        "error_class",
        "error_message",
        "node_id",
        "output",
        "retry_after_seconds",
        "retryable",
        "succeeded",
    )

    def __init__(
        self,
        *,
        node_id: str,
        attempt: int,
        succeeded: bool,
        output: JsonObject | None = None,
        cost_cents: int = 0,
        error_class: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.node_id = node_id
        self.attempt = attempt
        self.succeeded = succeeded
        self.output = output
        self.cost_cents = cost_cents
        self.error_class = error_class
        self.error_message = error_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds

    @classmethod
    def success(
        cls, *, node_id: str, attempt: int, output: JsonObject, cost_cents: int = 0
    ) -> InvocationOutcome:
        return cls(
            node_id=node_id,
            attempt=attempt,
            succeeded=True,
            output=output,
            cost_cents=cost_cents,
        )

    @classmethod
    def failure(
        cls,
        *,
        node_id: str,
        attempt: int,
        error_class: str,
        error_message: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
        cost_cents: int = 0,
    ) -> InvocationOutcome:
        return cls(
            node_id=node_id,
            attempt=attempt,
            succeeded=False,
            error_class=error_class,
            error_message=error_message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            cost_cents=cost_cents,
        )


class Store(Protocol):
    """Persistence + the atomic claim primitive the engine orchestrates against.

    Every method that mutates is expected to be atomic with respect to its event
    append (event-sourcing: state and its event move together). The contract here
    is storage-agnostic; the Postgres implementation realizes the atomicity with
    transactions and the claim with ``SELECT ... FOR UPDATE SKIP LOCKED``.
    """

    async def create_execution(
        self,
        execution: Execution,
        node_states: list[NodeState],
        events: list[ExecutionEvent],
    ) -> None:
        """Persist a brand-new run: the execution row, one node_state per graph
        node, and the initial events (RunRequested/RunStarted/NodeReady). One
        unit of work."""
        ...

    async def get_execution(self, execution_id: UUID) -> Execution | None: ...

    async def get_node_states(self, execution_id: UUID) -> list[NodeState]:
        """All node rows for a run (used to recompute the ready set and to
        decide run termination)."""
        ...

    async def claim_next_ready(
        self, *, now: datetime, lease_ttl_seconds: float
    ) -> NodeState | None:
        """Atomically claim one claimable node across ALL running executions.

        "Claimable" = status READY and (not_before is null or <= now). The
        implementation must transition it to RUNNING, set ``lease_until = now +
        lease_ttl_seconds``, increment attempt, and emit NodeStarted — all
        atomically — and must guarantee no two concurrent callers ever claim the
        same node (Postgres: FOR UPDATE SKIP LOCKED). The lease TTL is passed in
        because it is engine policy (EngineConfig.lease_ttl), while *applying* it
        atomically with the claim is the store's job. Returns the claimed
        NodeState, or None if nothing is claimable right now.
        """
        ...

    async def settle_node(
        self,
        *,
        node: NodeState,
        event: ExecutionEvent,
        promote_to_ready: dict[str, JsonObject],
        execution_update: Execution | None,
        terminal_events: list[ExecutionEvent],
    ) -> None:
        """Persist the outcome of a node and any cascading transitions, atomically.

        ``node`` is the settled node (SUCCEEDED, FAILED, or READY-again for a
        retry). ``promote_to_ready`` maps each newly-unblocked successor node_id
        to its engine-resolved input object; promotion must be guarded (only if
        the successor is still PENDING) and must set that input, so concurrent
        settles of sibling nodes are idempotent and data flow is durable.
        ``execution_update`` + ``terminal_events`` carry a run-level terminal
        transition (RunSucceeded/RunFailed) when this settle finished the run.
        """
        ...

    async def recover_expired_leases(self, *, now: datetime) -> list[NodeState]:
        """The reaper. Find RUNNING nodes whose lease has expired (worker died),
        reset them to READY, emit NodeRecovered, and return them. This is the
        crash-recovery guarantee (PHASE_1.md §6): durable state means a run
        resumes exactly where it stopped."""
        ...

    async def renew_lease(self, *, node: NodeState, lease_until: datetime) -> bool:
        """Extend the lease on a node a worker still holds (long-running node).
        Returns False if the node is no longer ours (e.g. the reaper already
        reclaimed it) — the worker should then abandon the result."""
        ...
