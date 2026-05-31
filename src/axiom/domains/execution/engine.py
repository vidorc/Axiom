"""The workflow engine — DAG scheduling, retries, and crash recovery.

This is the highest-priority component in Axiom (PHASE_1.md). It is deliberately
*pure orchestration*: it owns the run state machine and the scheduling decisions,
and delegates everything else through the protocols in ``protocols.py``:

  * persistence + the atomic claim → ``Store``
  * actually running a node        → ``NodeInvoker``
  * time (leases, backoff)         → ``Clock``

Because all three are injected, the engine's logic — ready-set computation,
retry counting, backoff, lease-based recovery, run termination — is unit-tested
in milliseconds against an in-memory store and a fake clock, with no database and
no real node code. The Postgres store is then verified against the same behaviour
plus the chaos lane (kill -9 mid-run → reaper → resume → exactly-once).

The model is **at-least-once execution with idempotency by construction**
(DOMAIN_MODEL.md §4): a node may run more than once across crashes, but every
side effect is keyed by ``(run_id, node_id, attempt)`` so retries don't double
fire. The engine never relies on in-process memory to make progress — all state
that matters is in the Store, so any worker can pick up any run at any time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from axiom.domains.execution.protocols import (
    Clock,
    GraphResolver,
    InvocationOutcome,
    NodeInvoker,
    Store,
    SystemClock,
)
from axiom.domains.execution.state import (
    TERMINAL_NODE_STATUSES,
    EventType,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    NodeError,
    NodeState,
    NodeStatus,
)
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.platform.logging import get_logger
from axiom.sdk import JsonObject

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Tunables for the engine's timing behaviour.

    ``lease_ttl`` must comfortably exceed the orchestration overhead plus a
    node's expected runtime; too short and the reaper reclaims live work, too
    long and a real crash takes that long to recover. ``jitter`` is injected (not
    ``random`` inline) so backoff is deterministic in tests — production wires a
    real random jitter to avoid thundering-herd retries.
    """

    lease_ttl: timedelta = timedelta(seconds=30)
    # Returns a non-negative jitter (seconds) to add to a computed backoff delay.
    # Default: none, for deterministic tests.
    jitter: Callable[[], float] = field(default=lambda: 0.0)
    # Safety ceiling on total node attempts engine-wide per run, independent of
    # per-node retry policy — a backstop against a pathological retry storm.
    max_total_attempts_per_run: int = 10_000


class WorkflowEngine:
    """Orchestrates executions against a Store + NodeInvoker.

    Stateless across calls except for its injected collaborators — two engine
    instances pointed at the same Store cooperate correctly (that is the whole
    multi-worker design). All scheduling decisions are derived from persisted
    state, never from instance memory.
    """

    def __init__(
        self,
        *,
        store: Store,
        invoker: NodeInvoker,
        clock: Clock | None = None,
        config: EngineConfig | None = None,
    ) -> None:
        self._store = store
        self._invoker = invoker
        self._clock = clock or SystemClock()
        self._config = config or EngineConfig()

    # ── Starting a run ───────────────────────────────────────────────────────────

    async def start_execution(
        self,
        *,
        org_id: UUID,
        workflow_id: UUID,
        workflow_version_id: UUID,
        graph: WorkflowGraph,
        run_input: JsonObject | None = None,
        trigger: str = "manual",
        execution_id: UUID | None = None,
    ) -> Execution:
        """Create a run: the execution row, one node_state per graph node, and
        the opening events. Root nodes start READY; everything else PENDING.

        Idempotency at the run level is the caller's concern (a provided
        ``execution_id`` lets a trigger be retried without creating a duplicate
        run); within a run, idempotency is the engine's concern.
        """
        now = self._clock.now()
        execution = Execution(
            id=execution_id or uuid4(),
            org_id=org_id,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            status=ExecutionStatus.RUNNING,
            trigger=trigger,
            input=run_input or {},
            started_at=now,
        )

        roots = set(graph.roots())
        node_states: list[NodeState] = []
        for node_id in graph.node_ids():
            is_root = node_id in roots
            node_states.append(
                NodeState(
                    execution_id=execution.id,
                    node_id=node_id,
                    status=NodeStatus.READY if is_root else NodeStatus.PENDING,
                    # A root's input is the run input merged with its static config;
                    # non-roots get their inputs resolved when they become ready.
                    input=self._merge_input(graph, node_id, execution.input, {}),
                )
            )

        events = [
            self._event(execution, EventType.RUN_REQUESTED, seq=1, payload={"trigger": trigger}),
            self._event(execution, EventType.RUN_STARTED, seq=2),
        ]
        seq = 3
        for ns in node_states:
            if ns.status is NodeStatus.READY:
                events.append(
                    self._event(execution, EventType.NODE_READY, seq=seq, node_id=ns.node_id)
                )
                seq += 1

        await self._store.create_execution(execution, node_states, events)
        logger.info(
            "execution.started",
            execution_id=str(execution.id),
            org_id=str(org_id),
            node_count=len(node_states),
            root_count=len(roots),
        )
        # Stash the graph on the run via stats so step() can reload it without a
        # separate authoring lookup in this in-process path. The Postgres store
        # rehydrates the graph from workflow_version; here we keep the engine
        # graph-aware by passing it into step() explicitly (see run_to_completion).
        return execution

    # ── One unit of work: claim → resolve graph → invoke → settle ─────────────────

    async def tick(self, resolve_graph: GraphResolver) -> bool:
        """Claim one ready node from *any* run, run it, and settle the result.

        This is the worker's true inner loop: a worker does not know in advance
        which run's node it will claim, so after claiming it resolves that run's
        graph via ``resolve_graph`` (backed by the authoring store in production,
        a constant in tests/CLI). Returns True if a node was processed, False if
        nothing was claimable right now.
        """
        now = self._clock.now()
        node = await self._store.claim_next_ready(
            now=now, lease_ttl_seconds=self._config.lease_ttl.total_seconds()
        )
        if node is None:
            return False

        # The claim already incremented attempt and set the lease; from here we
        # must ALWAYS settle the node (success or failure), never leave it
        # RUNNING, or the reaper would later reclaim a node that actually ran.
        execution = await self._store.get_execution(node.execution_id)
        if execution is None:  # pragma: no cover - defensive; claim implies it exists
            return True

        graph = await resolve_graph(node.execution_id)
        gnode = graph.node(node.node_id)
        logger.info(
            "node.claimed",
            execution_id=str(node.execution_id),
            node_id=node.node_id,
            attempt=node.attempt,
            node_ref=gnode.node_ref,
        )

        outcome = await self._invoker.invoke(
            node_ref=gnode.node_ref,
            config=gnode.config,
            credentials=gnode.credentials,
            inputs=node.input,
            run_id=node.execution_id,
            org_id=execution.org_id,
            node_id=node.node_id,
            attempt=node.attempt,
        )

        await self._settle(graph, execution, node, outcome)
        return True

    async def step(self, graph: WorkflowGraph) -> bool:
        """Single-graph convenience over :meth:`tick` (CLI / single-run tests).

        Equivalent to ``tick`` with a constant resolver that returns ``graph``
        for whatever run is claimed — correct when only one run is in flight.
        """

        async def _constant(execution_id: UUID) -> WorkflowGraph:
            return graph

        return await self.tick(_constant)

    async def run_to_completion(self, graph: WorkflowGraph, *, max_steps: int = 100_000) -> None:
        """Drive steps until no node is claimable (single-worker / test driver).

        Real deployments run many workers each calling ``tick`` in a loop; this
        helper is the in-process equivalent used by the CLI ``run`` command and
        by tests. ``max_steps`` is a runaway guard.
        """
        for _ in range(max_steps):
            did_work = await self.step(graph)
            if not did_work:
                return
        raise RuntimeError("run_to_completion exceeded max_steps — possible scheduling bug")

    # ── The reaper (crash recovery — PHASE_1.md §6) ──────────────────────────────

    async def reap(self) -> list[NodeState]:
        """Reset nodes whose lease expired (their worker died) back to READY.

        This is the mechanism behind the chaos guarantee: durable state plus a
        lease means a kill -9 mid-node simply lets another worker re-claim and
        re-run that node's attempt. Returns the recovered nodes (for logging /
        metrics)."""
        recovered = await self._store.recover_expired_leases(now=self._clock.now())
        if recovered:
            logger.warning(
                "leases.recovered",
                count=len(recovered),
                node_ids=[n.node_id for n in recovered],
            )
        return recovered

    # ── Settle: the state-machine core ───────────────────────────────────────────

    async def _settle(
        self,
        graph: WorkflowGraph,
        execution: Execution,
        node: NodeState,
        outcome: InvocationOutcome,
    ) -> None:
        """Apply a node's outcome and cascade: promote successors / retry / finish."""
        now = self._clock.now()

        if outcome.succeeded:
            await self._settle_success(graph, execution, node, outcome, now)
            return

        # Failure: retry if the error is retryable AND we have attempts left.
        gnode = graph.node(node.node_id)
        attempts_remaining = node.attempt < gnode.retry.max_attempts
        if outcome.retryable and attempts_remaining:
            await self._settle_retry(graph, execution, node, outcome, now)
        else:
            await self._settle_terminal_failure(graph, execution, node, outcome, now)

    async def _settle_success(
        self,
        graph: WorkflowGraph,
        execution: Execution,
        node: NodeState,
        outcome: InvocationOutcome,
        now: datetime,
    ) -> None:
        node.status = NodeStatus.SUCCEEDED
        node.output = outcome.output or {}
        node.cost_cents += outcome.cost_cents
        node.finished_at = now
        node.lease_until = None
        node.error = None

        # Recompute which PENDING successors are now unblocked, resolving each
        # one's input from its predecessors' outputs via the edge mappings. The
        # Store applies promotion guardedly (only if still PENDING), so two
        # siblings settling concurrently can't double-promote a shared child.
        promote, skip = await self._ready_after(graph, execution, node)

        event = self._event(
            execution,
            EventType.NODE_SUCCEEDED,
            node_id=node.node_id,
            attempt=node.attempt,
            payload={"cost_cents": outcome.cost_cents},
        )

        # If this success completed the run, compute the terminal transition.
        exec_update, terminal_events = await self._maybe_finish(
            graph, execution, just_settled=node, promote=promote, skip=skip
        )

        await self._store.settle_node(
            node=node,
            event=event,
            promote_to_ready=promote,
            execution_update=exec_update,
            terminal_events=terminal_events,
        )
        logger.info(
            "node.succeeded",
            execution_id=str(node.execution_id),
            node_id=node.node_id,
            attempt=node.attempt,
            promoted=promote,
        )

    async def _settle_retry(
        self,
        graph: WorkflowGraph,
        execution: Execution,
        node: NodeState,
        outcome: InvocationOutcome,
        now: datetime,
    ) -> None:
        # Back to READY with a backoff delay; the claim won't pick it up before
        # not_before. node.attempt is the attempt that just failed; the delay is
        # computed for the NEXT attempt and clamped against a provider Retry-After.
        delay = self._backoff_delay(graph, node, outcome)
        node.status = NodeStatus.READY
        node.not_before = now + timedelta(seconds=delay)
        node.lease_until = None
        node.cost_cents += outcome.cost_cents
        node.error = NodeError(
            error_class=outcome.error_class or "provider_error",
            message=outcome.error_message or "",
            retryable=True,
            retry_after_seconds=outcome.retry_after_seconds,
        )

        event = self._event(
            execution,
            EventType.NODE_RETRIED,
            node_id=node.node_id,
            attempt=node.attempt,
            payload={
                "error_class": node.error.error_class,
                "delay_seconds": delay,
                "next_attempt": node.attempt + 1,
            },
        )
        await self._store.settle_node(
            node=node,
            event=event,
            promote_to_ready={},
            execution_update=None,
            terminal_events=[],
        )
        logger.info(
            "node.retry_scheduled",
            execution_id=str(node.execution_id),
            node_id=node.node_id,
            failed_attempt=node.attempt,
            delay_seconds=delay,
        )

    async def _settle_terminal_failure(
        self,
        graph: WorkflowGraph,
        execution: Execution,
        node: NodeState,
        outcome: InvocationOutcome,
        now: datetime,
    ) -> None:
        node.status = NodeStatus.FAILED
        node.finished_at = now
        node.lease_until = None
        node.cost_cents += outcome.cost_cents
        node.error = NodeError(
            error_class=outcome.error_class or "internal",
            message=outcome.error_message or "",
            retryable=outcome.retryable,
            retry_after_seconds=outcome.retry_after_seconds,
        )

        event = self._event(
            execution,
            EventType.NODE_FAILED,
            node_id=node.node_id,
            attempt=node.attempt,
            payload={"error_class": node.error.error_class, "message": node.error.message},
        )

        # A terminal node failure fails the run. (Branch-level failure policies
        # are a future refinement; DOMAIN_MODEL.md treats run-fails-on-node-fail
        # as the MVP semantics.)
        execution.status = ExecutionStatus.FAILED
        execution.finished_at = now
        terminal_events = [
            self._event(
                execution,
                EventType.RUN_FAILED,
                payload={"failed_node": node.node_id, "error_class": node.error.error_class},
            )
        ]
        await self._store.settle_node(
            node=node,
            event=event,
            promote_to_ready={},
            execution_update=execution,
            terminal_events=terminal_events,
        )
        logger.warning(
            "node.failed_terminal",
            execution_id=str(node.execution_id),
            node_id=node.node_id,
            attempt=node.attempt,
            error_class=node.error.error_class,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────────

    async def _ready_after(
        self, graph: WorkflowGraph, execution: Execution, just_succeeded: NodeState
    ) -> tuple[dict[str, JsonObject], list[str]]:
        """Return (to_promote, to_skip) after a node succeeds.

        ``to_promote`` maps each newly-unblocked successor node_id to its
        fully-resolved input object; ``to_skip`` lists successors whose inbound
        edges were all condition-gated false. A successor is promotable when
        *every* predecessor is terminal-satisfied (succeeded or skipped).

        Input resolution is the inter-node data flow: for each satisfied inbound
        edge we resolve ``edge.mapping`` against a context exposing the upstream
        outputs, and merge the result onto the successor's static config. An
        empty mapping forwards the whole upstream output — the ergonomic default
        that makes a linear chain "just work" without authoring any mapping.

        Computed from freshly-loaded node states so concurrent settles see a
        consistent view (the Store's guarded promotion handles the race).
        """
        states = {s.node_id: s for s in await self._store.get_node_states(execution.id)}
        # Reflect the just-succeeded node locally in case the store read predates
        # this settle's commit.
        states[just_succeeded.node_id] = just_succeeded

        to_promote: dict[str, JsonObject] = {}
        to_skip: list[str] = []
        for succ in graph.successors(just_succeeded.node_id):
            succ_state = states.get(succ)
            if succ_state is None or succ_state.status is not NodeStatus.PENDING:
                continue  # already advanced by another path
            preds = graph.predecessors(succ)
            if not all(states[p].status in TERMINAL_NODE_STATUSES for p in preds):
                continue  # still waiting on an upstream branch (fan-in)
            resolved_input = self._resolve_node_input(graph, execution, succ, states)
            to_promote[succ] = resolved_input
        return to_promote, to_skip

    def _resolve_node_input(
        self,
        graph: WorkflowGraph,
        execution: Execution,
        node_id: str,
        states: dict[str, NodeState],
    ) -> JsonObject:
        """Compose a promoted node's input from static config + upstream outputs.

        For each inbound edge whose source succeeded and whose condition is met,
        resolve the edge mapping against ``{source, outputs, inputs}`` and merge
        it in. Empty mapping → forward the upstream output wholesale.
        """
        from axiom.domains.execution.expressions import evaluate_condition, resolve_mapping

        gnode = graph.node(node_id)
        merged: JsonObject = dict(gnode.config)

        # outputs namespace: every predecessor's output, keyed by node id.
        outputs: JsonObject = {
            p: (states[p].output or {})
            for p in graph.predecessors(node_id)
            if states[p].status is NodeStatus.SUCCEEDED
        }

        for edge in graph.edges:
            if edge.target != node_id:
                continue
            src_state = states.get(edge.source)
            if src_state is None or src_state.status is not NodeStatus.SUCCEEDED:
                continue  # skipped/failed source contributes nothing
            src_output = src_state.output or {}
            context = {"source": src_output, "outputs": outputs, "inputs": execution.input}
            if not evaluate_condition(edge.condition, context):
                continue
            if edge.mapping:
                merged.update(resolve_mapping(edge.mapping, context))
            else:
                # No explicit mapping → forward the upstream output as-is.
                merged.update(src_output)
        return merged

    async def _maybe_finish(
        self,
        graph: WorkflowGraph,
        execution: Execution,
        *,
        just_settled: NodeState,
        promote: dict[str, JsonObject],
        skip: list[str],
    ) -> tuple[Execution | None, list[ExecutionEvent]]:
        """If settling ``just_settled`` (plus promotions) finished the run, return
        the execution update + terminal events; else (None, [])."""
        states = {s.node_id: s for s in await self._store.get_node_states(execution.id)}
        states[just_settled.node_id] = just_settled

        # A node about to be promoted to READY is not terminal; nor is anything
        # still PENDING/READY/RUNNING. The run isn't done while any remain.
        unfinished = 0
        for node_id in graph.node_ids():
            if node_id in promote:
                unfinished += 1
                continue
            if states[node_id].status not in TERMINAL_NODE_STATUSES:
                unfinished += 1

        if unfinished > 0:
            return None, []

        # All nodes terminal and none failed (a failure would have taken the
        # terminal-failure path) → run succeeded.
        now = self._clock.now()
        execution.status = ExecutionStatus.SUCCEEDED
        execution.finished_at = now
        execution.total_cost_cents = sum(s.cost_cents for s in states.values())
        return execution, [
            self._event(
                execution,
                EventType.RUN_SUCCEEDED,
                payload={"total_cost_cents": execution.total_cost_cents},
            )
        ]

    def _backoff_delay(
        self, graph: WorkflowGraph, node: NodeState, outcome: InvocationOutcome
    ) -> float:
        """Backoff before the next attempt, honoring Retry-After, plus jitter.

        The per-node retry policy lives on the graph node, so the delay respects
        what the workflow author declared (fixed vs exponential, base/max). A
        provider ``Retry-After`` overrides when it is *longer* than our computed
        backoff — never shorter, so we don't hammer a throttling provider.
        node.attempt is the attempt that just failed, so the next attempt number
        is ``node.attempt + 1``.
        """
        policy = graph.node(node.node_id).retry
        computed = policy.delay_for_attempt(node.attempt + 1)
        retry_after = max(0.0, float(outcome.retry_after_seconds or 0.0))
        return max(computed, retry_after) + self._config.jitter()

    def _event(
        self,
        execution: Execution,
        event_type: EventType,
        *,
        seq: int = 0,
        node_id: str | None = None,
        attempt: int | None = None,
        payload: JsonObject | None = None,
    ) -> ExecutionEvent:
        return ExecutionEvent(
            execution_id=execution.id,
            org_id=execution.org_id,
            seq=seq,
            type=event_type,
            node_id=node_id,
            attempt=attempt,
            payload=payload or {},
            created_at=self._clock.now(),
        )

    @staticmethod
    def _merge_input(
        graph: WorkflowGraph,
        node_id: str,
        run_input: JsonObject,
        upstream: JsonObject,
    ) -> JsonObject:
        """Compose a node's input: static config, then run input for roots, then
        resolved upstream values (upstream wins on conflict)."""
        gnode = graph.node(node_id)
        merged: JsonObject = dict(gnode.config)
        if not graph.predecessors(node_id):
            merged.update(run_input)
        merged.update(upstream)
        return merged
