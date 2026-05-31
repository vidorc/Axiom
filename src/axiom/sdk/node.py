"""The node behavioral contract (SDK_SPEC.md §4).

`BaseNode` is what a code-node author subclasses. It is deliberately tiny: two
methods of substance (`validate`, `execute`) plus one optional hook
(`compensate`). Everything else a node could want — retry policy, metadata,
credential scoping, rate limits — was *removed* from the behavioral interface
and pushed into the declarative manifest or into engine policy (SDK_SPEC.md §4.2):

  * retry policy  → the graph's per-node `retry` block + the error taxonomy
  * metadata      → the manifest (id, version, schemas, display, cost)
  * rollback      → renamed `compensate`, made optional, default Unsupported

This keeps the author's job to "given inputs, do the thing, return output or
raise a typed error" and lets the engine own everything operational.

Sync vs async — a deliberate decision (documented here because the prose in
SDK_SPEC.md shows sync pseudocode):

  * ``validate`` is **sync**. The contract is that it is pure and performs no
    I/O; a synchronous signature makes that structural — you cannot ``await`` a
    network call from a sync method, so the "no spend in validate" guarantee is
    enforced by the type, not just by convention.
  * ``execute`` and ``compensate`` are **async**. They perform the external
    provider I/O, and the entire platform is async (asyncpg, httpx, FastAPI).
    Making them async avoids forcing every node onto a thread pool and lets a
    single worker run many I/O-bound nodes concurrently.
"""

from __future__ import annotations

import abc

from axiom.sdk.context import ExecutionContext, JsonObject
from axiom.sdk.results import CompensationResult, ValidationResult


class BaseNode(abc.ABC):
    """Base class for all code nodes.

    A node instance is cheap and stateless: the engine may construct one per
    execution. Do not stash run state on ``self`` — there is no guarantee the
    same instance handles a retry, and (by design) nodes share no mutable state.
    Anything a node needs at run time arrives via ``ctx`` and ``inputs``.
    """

    def validate(self, inputs: JsonObject) -> ValidationResult:
        """Pure, no-I/O pre-flight check. Runs before any external call.

        The manifest's input JSONSchema is the *structural* first line of
        defence (types, required fields, formats) and is checked by the runtime
        before this method is called. Override ``validate`` only for *semantic*
        checks the schema cannot express — e.g. "exactly one of `email` or
        `linkedin_url` must be set", or "`end_date` must be after `start_date`".

        Returning a failure here fails the node terminally *without spending
        money*, which is the whole point of separating it from ``execute``.

        The default accepts any structurally-valid input.
        """
        return ValidationResult.success()

    @abc.abstractmethod
    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        """Perform the node's work and return its output object.

        On success, return a JSON object conforming to the manifest's output
        schema. On failure, raise ``axiom.sdk.NodeError`` with the appropriate
        ``ErrorClass`` — the engine reads the class to decide retry-vs-terminal.
        Do not return error sentinels or catch-and-swallow; a raised typed error
        is the contract.

        Side effects must be idempotent with respect to
        ``(ctx.run.run_id, ctx.run.node_id, ctx.run.attempt)`` where possible,
        because a crash after the side effect but before the engine commits the
        result will cause a retry at the same attempt key (SDK_SPEC.md §4.3,
        PHASE_1.md §6 idempotency test).
        """
        raise NotImplementedError

    async def compensate(self, ctx: ExecutionContext, output: JsonObject) -> CompensationResult:
        """Best-effort undo of a previously successful ``execute`` (optional).

        Called by the engine, in reverse topological order, only when a run
        fails *and* compensation is enabled for that run. The default declares
        the node cannot compensate — which is correct for most GTM side effects
        (you cannot un-send an email). Override only when a clean reversal exists
        (e.g. delete a row this node created).

        Compensation is best-effort and never a transactional guarantee
        (SDK_SPEC.md §4.5); return ``CompensationResult.failed(...)`` rather than
        raising if the undo itself fails.
        """
        return CompensationResult.unsupported()
