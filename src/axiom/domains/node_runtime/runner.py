"""The node runner — executes one node attempt and returns a neutral result.

This is the heart of the node runtime: given a resolved node class, its config,
the (declared-only) credentials, and resolved inputs, it builds the
ExecutionContext, runs ``validate()`` then ``execute()``, and translates the
outcome — including a raised ``axiom.sdk.NodeError`` — into a ``NodeRunResult``.

Boundary note (import-linter Contract 2): ``NodeRunResult`` is defined HERE, not
in ``axiom.domains.execution``. The runtime must never import the engine, so it
cannot return the engine's ``InvocationOutcome``. Instead the worker layer (the
composition root, allowed to import both domains) adapts ``NodeRunner`` to the
engine's ``NodeInvoker`` port, mapping ``NodeRunResult`` → ``InvocationOutcome``.
This keeps the runtime a pure executor that knows nothing about orchestration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import httpx

from axiom.domains.node_runtime.context import NodeExecutionContext, RunContext
from axiom.domains.node_runtime.registry import NodeRegistry
from axiom.platform.logging import get_logger
from axiom.sdk import ErrorClass, JsonObject, NodeError

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NodeRunResult:
    """The neutral outcome of one node attempt (runtime's vocabulary).

    Carries everything the engine needs to decide what happens next, but in a
    form the runtime owns. On success: ``output`` + ``cost_cents``. On failure:
    the taxonomy ``error_class`` (a string from ``sdk.ErrorClass``), whether it
    is ``retryable``, an optional ``retry_after_seconds``, and a message.
    """

    succeeded: bool
    output: JsonObject | None = None
    cost_cents: int = 0
    error_class: str | None = None
    error_message: str | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None


class NodeRunner:
    """Runs nodes resolved from a ``NodeRegistry``.

    Stateless and reusable across runs. A fresh node *instance* is constructed
    per attempt (nodes are stateless by contract), and a fresh
    ``ExecutionContext`` is built per attempt with the run's identity and scoped
    credentials.
    """

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._registry = registry
        # Injected so tests can supply a transport-mocked client and production
        # can supply the instrumented (SSRF-filtered, redacting) client.
        self._http_factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=30.0))

    async def run(
        self,
        *,
        node_ref: str,
        version: str | None,
        config: JsonObject,
        secrets: dict[str, str],
        inputs: JsonObject,
        run_id: UUID,
        org_id: UUID,
        node_id: str,
        attempt: int,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> NodeRunResult:
        """Execute one attempt. Never raises for *node* failures — it maps them
        to a ``NodeRunResult``. It may still raise for *infrastructure* failures
        the engine cannot meaningfully retry differently (e.g. node not found),
        which the worker adapter treats as a terminal internal error.
        """
        registered = self._registry.resolve(node_ref, version)
        node = registered.node_cls()
        run = RunContext(run_id=run_id, node_id=node_id, attempt=attempt, org_id=org_id)

        # Merge static config with resolved inputs: explicit inputs win. (The
        # engine already merges upstream outputs into `inputs`; config provides
        # defaults the author set on the node.)
        effective_inputs: JsonObject = {**config, **inputs}

        # 1) Pure validation — no I/O, no spend. A failure is terminal.
        validation = node.validate(effective_inputs)
        if not validation.ok:
            return NodeRunResult(
                succeeded=False,
                error_class=ErrorClass.INVALID_INPUT.value,
                error_message="; ".join(validation.errors),
                retryable=False,
            )

        # 2) Side-effecting execution inside a managed HTTP client lifecycle.
        async with self._http_factory() as http:
            ctx = NodeExecutionContext.build(
                run=run, secrets=secrets, http=http, is_cancelled=is_cancelled
            )
            try:
                output = await node.execute(ctx, effective_inputs)
            except NodeError as exc:
                logger.info(
                    "node.execute_failed",
                    node_ref=node_ref,
                    node_id=node_id,
                    attempt=attempt,
                    error_class=exc.error_class.value,
                    retryable=exc.retryable,
                )
                return NodeRunResult(
                    succeeded=False,
                    cost_cents=ctx.cost.total_cents,
                    error_class=exc.error_class.value,
                    error_message=str(exc),
                    retryable=exc.retryable,
                    retry_after_seconds=exc.retry_after_seconds,
                )
            except Exception as exc:
                # An unexpected exception is a node bug. Surface it as a terminal
                # `internal` error rather than letting it crash the worker — one
                # bad node must not take down the orchestrator.
                logger.warning(
                    "node.execute_crashed",
                    node_ref=node_ref,
                    node_id=node_id,
                    attempt=attempt,
                    error=type(exc).__name__,
                )
                return NodeRunResult(
                    succeeded=False,
                    cost_cents=ctx.cost.total_cents,
                    error_class=ErrorClass.INTERNAL.value,
                    error_message=f"{type(exc).__name__}: {exc}",
                    retryable=False,
                )

            return NodeRunResult(
                succeeded=True,
                output=output,
                cost_cents=ctx.cost.total_cents,
            )
