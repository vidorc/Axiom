"""Adapter: NodeRunner (node_runtime) → NodeInvoker (execution).

The composition seam. The engine depends on the ``NodeInvoker`` port it defines
in ``axiom.domains.execution``; the node runtime provides a ``NodeRunner`` that
knows nothing about orchestration. Neither domain imports the other — they meet
*here*, in the composition layer, which is allowed to import both (it sits below
the delivery entrypoints and above the domains).

The adapter also owns **credential resolution**: the engine hands over the graph
node's ``credentials`` map (provider → credential id), and a ``CredentialResolver``
turns those references into the plaintext secrets the runner injects into the
scoped context. Resolution lives here (not in the runtime) because it spans the
credential vault domain; :func:`build_vault_resolver` wires the real vault, while
the default ``_no_credentials`` resolves to no secrets — correct for credential-
free nodes and the in-process CLI path that has no database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.domains.credentials import CredentialVault
from axiom.domains.execution.protocols import InvocationOutcome
from axiom.domains.node_runtime import NodeRunner
from axiom.domains.node_runtime.registry import NodeNotFoundError
from axiom.platform.config import Settings
from axiom.platform.crypto import EnvelopeCipher, build_key_provider
from axiom.sdk import ErrorClass, JsonObject

# Resolves (org_id, {provider: credential_id}) → {provider: plaintext_secret}.
# The Credential Vault domain provides the real implementation
# (:func:`build_vault_resolver`); the default below resolves to no secrets, which
# is correct for credential-free nodes and keeps the in-process path runnable.
CredentialResolver = Callable[[UUID, dict[str, str]], Awaitable[dict[str, str]]]


async def _no_credentials(_org_id: UUID, _refs: dict[str, str]) -> dict[str, str]:
    return {}


def resolver_from_vault(vault: CredentialVault) -> CredentialResolver:
    """Wrap an existing vault as the engine's positional ``CredentialResolver``.

    Lets the API and the engine share *one* vault instance (and thus one cipher),
    rather than each constructing its own. The vault's ``resolve_many`` is the only
    plaintext-yielding path and is org-scoped, so a node can never resolve a
    credential outside its run's org even by id (SECURITY.md §5).
    """

    async def _resolve(org_id: UUID, refs: dict[str, str]) -> dict[str, str]:
        if not refs:
            return {}
        return await vault.resolve_many(org_id=org_id, refs=refs)

    return _resolve


def build_vault(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings | None = None,
    cipher: EnvelopeCipher | None = None,
) -> CredentialVault:
    """Construct the credential vault (envelope cipher over the configured KEK)."""
    cipher = cipher or EnvelopeCipher(build_key_provider(settings))
    return CredentialVault(session_factory, cipher)


def build_vault_resolver(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings | None = None,
    cipher: EnvelopeCipher | None = None,
) -> CredentialResolver:
    """Build a vault and adapt it to the engine's ``CredentialResolver`` port.

    Convenience for the durable assembly. Composition owns this adapter because it
    spans two areas the engine must not know about — the credential domain and the
    platform key provider — meeting them behind the resolver port the engine
    already depends on.
    """
    return resolver_from_vault(build_vault(session_factory, settings=settings, cipher=cipher))


class RunnerInvoker:
    """Wraps a ``NodeRunner`` to satisfy the engine's ``NodeInvoker`` protocol.

    Translates the engine's invocation request into a ``NodeRunner.run`` call,
    resolves declared credentials to plaintext, and maps the neutral
    ``NodeRunResult`` back onto the engine's ``InvocationOutcome``. A node that is
    not registered (an integrity failure — the graph was pinned to a version that
    isn't installed) becomes a terminal ``internal`` outcome rather than a crash.
    """

    def __init__(
        self,
        runner: NodeRunner,
        *,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self._runner = runner
        self._resolve_credentials = credential_resolver or _no_credentials

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
    ) -> InvocationOutcome:
        # Resolve only the declared provider references to plaintext. The runner
        # builds a ScopedCredentials holding exactly these — a node cannot reach
        # a secret it didn't declare (SECURITY.md §5).
        try:
            secrets = await self._resolve_credentials(org_id, credentials)
        except Exception as exc:
            return InvocationOutcome.failure(
                node_id=node_id,
                attempt=attempt,
                error_class=ErrorClass.AUTH_ERROR.value,
                error_message=f"credential resolution failed: {exc}",
                retryable=False,
            )

        try:
            result = await self._runner.run(
                node_ref=node_ref,
                version=None,  # pinned-version resolution is wired with node_pins (authoring)
                config=config,
                secrets=secrets,
                inputs=inputs,
                run_id=run_id,
                org_id=org_id,
                node_id=node_id,
                attempt=attempt,
            )
        except NodeNotFoundError as exc:
            # The workflow was pinned to a node/version that isn't installed —
            # a reference-integrity failure, terminal and surfaced to the author.
            return InvocationOutcome.failure(
                node_id=node_id,
                attempt=attempt,
                error_class=ErrorClass.INTERNAL.value,
                error_message=str(exc),
                retryable=False,
            )

        if result.succeeded:
            return InvocationOutcome.success(
                node_id=node_id,
                attempt=attempt,
                output=result.output or {},
                cost_cents=result.cost_cents,
            )
        return InvocationOutcome.failure(
            node_id=node_id,
            attempt=attempt,
            error_class=result.error_class or ErrorClass.INTERNAL.value,
            error_message=result.error_message or "",
            retryable=result.retryable,
            retry_after_seconds=result.retry_after_seconds,
            cost_cents=result.cost_cents,
        )
