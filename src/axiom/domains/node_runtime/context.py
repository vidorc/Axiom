"""Concrete implementations of the SDK ExecutionContext protocols.

The SDK defines ``ExecutionContext`` (and its parts) structurally so the SDK
stays import-independent. This module provides the *real* objects the runtime
hands a node: a scoped credential accessor, a redaction-aware logger backed by
``axiom.platform.logging``, an accumulating cost ledger, a cancel token, and the
run identity. Because the SDK protocols are ``runtime_checkable``, an instance of
``NodeExecutionContext`` satisfies ``axiom.sdk.ExecutionContext`` without the SDK
importing anything here.

Security note (SECURITY.md §5): the credential accessor is constructed holding
*only* the providers the graph node declared. A node asking for an undeclared
provider raises — there is no path by which a node reads a secret it did not
declare, even if that secret exists in the org.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from axiom.platform.logging import get_logger
from axiom.sdk import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class RunContext:
    """Read-only run/node/attempt identity (satisfies sdk.RunInfo)."""

    run_id: UUID
    node_id: str
    attempt: int
    org_id: UUID


class ScopedCredentials:
    """Credential accessor holding only the node's declared providers.

    Satisfies ``sdk.CredentialAccessor``. Constructed per execution from the
    plaintext secrets the runtime unwrapped for *exactly* the providers the graph
    node bound. An undeclared lookup raises ``KeyError`` (surfaced as a node
    error) rather than returning an empty string.
    """

    __slots__ = ("_secrets",)

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def __call__(self, provider: str) -> str:
        try:
            return self._secrets[provider]
        except KeyError as exc:
            raise KeyError(
                f"credential {provider!r} not declared by this node — "
                "declare it in the node's auth/credentials to use it"
            ) from exc


class StructlogAdapter:
    """Redaction-aware structured logger (satisfies sdk.StructuredLogger).

    Routes through ``axiom.platform.logging``, so the mandatory redaction
    processor (SECURITY.md §5.4) applies to everything a node logs. Binds the run
    identity onto every event so node logs are correlated to their run/node.
    """

    __slots__ = ("_log",)

    def __init__(self, run: RunContext) -> None:
        self._log = get_logger("node").bind(
            run_id=str(run.run_id), node_id=run.node_id, attempt=run.attempt
        )

    def debug(self, event: str, **fields: JsonValue) -> None:
        self._log.debug(event, **fields)

    def info(self, event: str, **fields: JsonValue) -> None:
        self._log.info(event, **fields)

    def warning(self, event: str, **fields: JsonValue) -> None:
        self._log.warning(event, **fields)

    def error(self, event: str, **fields: JsonValue) -> None:
        self._log.error(event, **fields)


@dataclass(slots=True)
class CostLedger:
    """Accumulating cost reporter (satisfies sdk.CostReporter).

    A node calls ``record`` as it spends; the runner reads ``entries`` /
    ``total_cents`` afterwards to attribute spend to the node_state. Costs in
    non-cent units (tokens, credits) are kept as raw entries; conversion to cents
    is a billing concern (Phase 12), so here we only sum entries already in
    cents and preserve the rest for the ledger.
    """

    entries: list[tuple[int, str]] = field(default_factory=list)

    def record(self, amount: int, unit: str) -> None:
        self.entries.append((amount, unit))

    @property
    def total_cents(self) -> int:
        return sum(amount for amount, unit in self.entries if unit in ("cent", "cents"))


class CallableCancelToken:
    """Cancel token backed by a callable (satisfies sdk.CancelToken).

    The runtime wires this to the live execution's cancellation state; tests pass
    a simple lambda. A node polls ``is_cancelled()`` between units of work.
    """

    __slots__ = ("_check",)

    def __init__(self, check: Callable[[], bool] | None = None) -> None:
        self._check = check or (lambda: False)

    def is_cancelled(self) -> bool:
        return self._check()


class NullCache:
    """A no-op enrichment cache (satisfies sdk.EnrichmentCache).

    The default until the per-org cache lands (DOMAIN_MODEL.md data-gravity
    feature). Always misses; ``set`` is a no-op. Nodes that opt into caching
    still work — they just never hit.
    """

    async def get(self, key: str) -> JsonObject | None:
        return None

    async def set(self, key: str, value: JsonObject, *, ttl_seconds: int | None = None) -> None:
        return None


class NodeExecutionContext:
    """The concrete ExecutionContext handed to a node's execute().

    Satisfies ``axiom.sdk.ExecutionContext`` structurally. Construct via
    :meth:`build` which assembles the parts from the run identity, the unwrapped
    (declared-only) credentials, and an injected HTTP client.
    """

    __slots__ = ("_cache", "_cancel", "_cost", "_creds", "_http", "_log", "_run")

    def __init__(
        self,
        *,
        run: RunContext,
        credentials: ScopedCredentials,
        log: StructlogAdapter,
        cost: CostLedger,
        cache: NullCache,
        cancel: CallableCancelToken,
        http: httpx.AsyncClient,
    ) -> None:
        self._run = run
        self._creds = credentials
        self._log = log
        self._cost = cost
        self._cache = cache
        self._cancel = cancel
        self._http = http

    @classmethod
    def build(
        cls,
        *,
        run: RunContext,
        secrets: dict[str, str],
        http: httpx.AsyncClient,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> NodeExecutionContext:
        return cls(
            run=run,
            credentials=ScopedCredentials(secrets),
            log=StructlogAdapter(run),
            cost=CostLedger(),
            cache=NullCache(),
            cancel=CallableCancelToken(is_cancelled),
            http=http,
        )

    @property
    def run(self) -> RunContext:
        return self._run

    @property
    def credentials(self) -> ScopedCredentials:
        return self._creds

    @property
    def log(self) -> StructlogAdapter:
        return self._log

    @property
    def cost(self) -> CostLedger:
        return self._cost

    @property
    def cache(self) -> NullCache:
        return self._cache

    @property
    def cancel(self) -> CallableCancelToken:
        return self._cancel

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http


# A small type alias used by the runner's signature for the cancellation hook.
CancelCheck = Callable[[], bool] | None
_AsyncNoop = Callable[..., Awaitable[None]]
