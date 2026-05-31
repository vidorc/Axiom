"""The execution context handed to a node at run time (SDK_SPEC.md §4.4).

`ExecutionContext` is the node author's whole view of the platform. It is a
`Protocol`, not a concrete class, for two reasons:

1. **SDK independence.** This package must not import `axiom.domains` or
   `axiom.platform` (import-linter Contract 3) so it can be extracted as
   `pip install axiom-sdk` later. Defining the context structurally means the
   real, instrumented implementation can live in `axiom.domains.node_runtime`
   and satisfy this Protocol without the SDK ever importing it.

2. **Testability.** A node test can pass any object with the right shape — a
   fake context — without standing up the platform.

Everything a node is allowed to touch flows through this object. There is no
ambient global state and (critically) **no shared mutable state between nodes**:
data moves only through declared edges and the event log (BLUEPRINT.md §3.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    import httpx

# A JSON-compatible value. Node inputs and outputs are always JSON objects so
# they can be persisted in the event log and type-checked against the manifest's
# JSONSchema at authoring time.
type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


@runtime_checkable
class RunInfo(Protocol):
    """Read-only identity of the current run/node/attempt (SDK_SPEC.md §4.4).

    Deliberately read-only and scalar: a node can know *who* it is for logging,
    idempotency keys, and cache scoping, but it cannot reach sideways into other
    nodes' state. The idempotency key for any side effect is
    ``(run_id, node_id, attempt)`` — all three live here.
    """

    @property
    def run_id(self) -> UUID: ...
    @property
    def node_id(self) -> str: ...
    @property
    def attempt(self) -> int: ...
    @property
    def org_id(self) -> UUID: ...


@runtime_checkable
class CredentialAccessor(Protocol):
    """Scoped, read-at-use access to provider secrets (SECURITY.md §5, SDK_SPEC §4.4).

    Calling ``ctx.credentials("apollo")`` returns the plaintext secret *only* if
    the node's manifest declared ``auth: apollo``. The accessor is constructed
    per node execution holding only the declared providers, so a node that
    declares ``apollo`` can never read the ``openai`` key even though both exist
    in the org. Plaintext lives in worker memory for the duration of execute()
    and is never logged or persisted.
    """

    def __call__(self, provider: str) -> str:
        """Return the plaintext secret for `provider`.

        Raises a lookup error if the provider was not declared in the manifest
        `auth` block — undeclared access is a bug, not a silent empty string.
        """
        ...


@runtime_checkable
class StructuredLogger(Protocol):
    """Redaction-aware structured logging (SECURITY.md §5.4).

    ``ctx.log.info("enriched", email=email)`` emits a structured event. The
    implementation scans values against the redaction filter so a leaked secret
    never reaches a log sink. Node authors should always prefer this over
    `print` (which lint forbids, and which the runtime captures + scans anyway).
    """

    def debug(self, event: str, **fields: JsonValue) -> None: ...
    def info(self, event: str, **fields: JsonValue) -> None: ...
    def warning(self, event: str, **fields: JsonValue) -> None: ...
    def error(self, event: str, **fields: JsonValue) -> None: ...


@runtime_checkable
class CostReporter(Protocol):
    """Per-run cost ledger (DOMAIN_MODEL.md — Observability & Cost).

    A node reports spend as it happens: ``ctx.cost.record(1, "credit")`` or
    ``ctx.cost.record(1200, "token")``. The engine aggregates this into the
    execution's ``total_cost_cents`` / ``stats`` so cost is attributable per
    node and per run — a first-class product surface, not an afterthought.
    """

    def record(self, amount: int, unit: str) -> None: ...


@runtime_checkable
class EnrichmentCache(Protocol):
    """Per-org enrichment cache (DOMAIN_MODEL.md — the data-gravity feature).

    Optional, opt-in via the manifest `cache` block. Keys are namespaced to the
    org by the implementation; a node never sees another org's cached data.
    Returns ``None`` on miss. Values are JSON objects.
    """

    async def get(self, key: str) -> JsonObject | None: ...
    async def set(self, key: str, value: JsonObject, *, ttl_seconds: int | None = None) -> None: ...


@runtime_checkable
class CancelToken(Protocol):
    """Cooperative cancellation (SDK_SPEC.md §4.4).

    A long-running node should check ``ctx.cancel.is_cancelled()`` between units
    of work (e.g. between pages of a paginated API) and stop promptly when a run
    is cancelled. Cancellation is cooperative — the engine cannot safely kill a
    node mid-side-effect, so well-behaved nodes poll.
    """

    def is_cancelled(self) -> bool: ...


@runtime_checkable
class ExecutionContext(Protocol):
    """The complete platform surface available to a node during execute().

    This is the contract between the platform and node authors. The concrete
    implementation (``axiom.domains.node_runtime``) wires each attribute to the
    real, instrumented service; tests provide a fake. A node should treat this
    object as its only window to the outside world.
    """

    @property
    def run(self) -> RunInfo: ...
    @property
    def credentials(self) -> CredentialAccessor: ...
    @property
    def log(self) -> StructuredLogger: ...
    @property
    def cost(self) -> CostReporter: ...
    @property
    def cache(self) -> EnrichmentCache: ...
    @property
    def cancel(self) -> CancelToken: ...
    @property
    def http(self) -> httpx.AsyncClient:
        """A pre-instrumented async HTTP client.

        The runtime supplies a client wired with redaction, egress/SSRF
        filtering, per-provider rate-limit buckets, and sane timeouts
        (SECURITY.md §7). Node authors use it exactly like ``httpx.AsyncClient``;
        the protections are transparent. Typed as ``httpx.AsyncClient`` because
        that is the well-known shape the instrumented client conforms to.
        """
        ...
