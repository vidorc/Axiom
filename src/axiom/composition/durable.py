"""Durable-engine assembly + the DB-backed graph resolver (composition layer).

The CLI runs the engine in-process over an in-memory store. The *worker* runs it
durably: state in Postgres, definitions in the authoring store, many workers
cooperating via the SKIP-LOCKED claim. This module assembles that durable engine
and provides the piece the in-process path doesn't need — a ``GraphResolver`` that
rehydrates a claimed run's graph from the database.

Why the resolver lives here: the engine's resolver hook is keyed by
``execution_id``, but graphs are stored by ``workflow_version_id``. Bridging the
two means loading the execution (execution domain) to read its pinned version,
then loading the graph (authoring domain). That chain spans two domains, so it
belongs in composition — neither domain should know about the other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.composition.invoker import (
    CredentialResolver,
    RunnerInvoker,
    build_vault,
    resolver_from_vault,
)
from axiom.domains.credentials import CredentialVault
from axiom.domains.execution.engine import EngineConfig, WorkflowEngine
from axiom.domains.execution.state import Execution
from axiom.domains.execution.store_pg import PostgresStore
from axiom.domains.node_runtime import NodeRegistry, NodeRunner, build_default_registry
from axiom.domains.workflow_authoring import AuthoringStore, WorkflowNotFoundError
from axiom.domains.workflow_authoring.graph import WorkflowGraph
from axiom.platform.crypto import EnvelopeCipher
from axiom.platform.http import build_instrumented_client
from axiom.sdk import JsonObject


class DbGraphResolver:
    """Resolves a run to its graph via execution → workflow_version → graph.

    Satisfies the engine's ``GraphResolver`` protocol. The graph itself is cached
    in the ``AuthoringStore`` (versions are immutable), so the only per-call cost
    after warm-up is one execution-row fetch to read the pinned version id.
    """

    def __init__(self, store: PostgresStore, authoring: AuthoringStore) -> None:
        self._store = store
        self._authoring = authoring

    async def __call__(self, execution_id: UUID) -> WorkflowGraph:
        execution = await self._store.get_execution(execution_id)
        if execution is None:
            raise WorkflowNotFoundError(f"execution {execution_id} not found")
        return await self._authoring.get_graph(execution.workflow_version_id)


@dataclass(frozen=True, slots=True)
class DurableEngine:
    """A Postgres-backed engine plus the stores + resolver a worker needs."""

    engine: WorkflowEngine
    store: PostgresStore
    authoring: AuthoringStore
    resolver: DbGraphResolver
    registry: NodeRegistry
    vault: CredentialVault

    async def enqueue_run(
        self,
        *,
        org_id: UUID,
        workflow_id: UUID,
        workflow_version_id: UUID,
        run_input: JsonObject | None = None,
        trigger: str = "manual",
    ) -> Execution:
        """Persist a new run for a stored workflow version (the durable enqueue).

        Resolves the version's graph (so roots can be computed), then creates the
        execution + node_states + opening events in Postgres. A worker will claim
        and run it — this call does not execute anything itself.
        """
        graph = await self.authoring.get_graph(workflow_version_id)
        return await self.engine.start_execution(
            org_id=org_id,
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            graph=graph,
            run_input=run_input,
            trigger=trigger,
            execution_id=uuid4(),
        )


def build_durable_engine(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    registry: NodeRegistry | None = None,
    credential_resolver: CredentialResolver | None = None,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    cipher: EnvelopeCipher | None = None,
    config: EngineConfig | None = None,
) -> DurableEngine:
    """Assemble the durable engine: Postgres store + runtime + authoring + engine.

    Both the execution store and the authoring store share one session factory
    (one database). The result is everything a worker process needs to enqueue
    and drive runs.

    ``http_client_factory`` defaults to the SSRF-guarded instrumented client (the
    production posture); a test injects a ``MockTransport``-backed factory to drive
    manifest nodes against a fake provider without a network. ``cipher`` overrides
    the vault's envelope cipher (a test passes a deterministic key so a credential
    bound over the API decrypts with the key the engine resolves with).
    """
    registry = registry or build_default_registry()
    store = PostgresStore(session_factory)
    authoring = AuthoringStore(session_factory)
    # The worker path is multi-tenant production, so node HTTP goes through the
    # SSRF-guarded, redaction-aware instrumented client (SECURITY.md §7) — not the
    # plain client the in-process CLI path uses against localhost.
    runner = NodeRunner(
        registry, http_client_factory=http_client_factory or build_instrumented_client
    )
    # Build the vault once and share it: the API manages credentials through it
    # and the engine resolves them through it, so there is a single cipher and a
    # single home for the plaintext-yielding path (SECURITY.md §5).
    vault = build_vault(session_factory, cipher=cipher)
    # The durable path is the production path, so it resolves real credentials
    # through the vault by default. A caller can inject a different resolver (e.g.
    # a fake in tests, or _no_credentials for a credential-free run).
    if credential_resolver is None:
        credential_resolver = resolver_from_vault(vault)
    invoker = RunnerInvoker(runner, credential_resolver=credential_resolver)
    engine = WorkflowEngine(store=store, invoker=invoker, config=config)
    resolver = DbGraphResolver(store, authoring)
    return DurableEngine(
        engine=engine,
        store=store,
        authoring=authoring,
        resolver=resolver,
        registry=registry,
        vault=vault,
    )
