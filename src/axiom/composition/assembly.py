"""One-call assembly of a runnable in-process engine.

The CLI ``run`` command and many tests want "an engine that runs real built-in
nodes against an in-memory store" without repeating the four-object wiring. This
helper is that assembly: registry → runner → invoker → engine, plus the
in-memory store. The result bundles the engine with its store so callers can
both drive the run and read back its final state.

For the durable, multi-worker path (the worker process), the same ``RunnerInvoker``
is wired to a ``PostgresStore`` instead — that assembly lives with the worker
entrypoint since it also owns the claim loop and reaper scheduling.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiom.composition.invoker import CredentialResolver, RunnerInvoker
from axiom.domains.execution.engine import EngineConfig, WorkflowEngine
from axiom.domains.execution.store_memory import InMemoryStore
from axiom.domains.node_runtime import NodeRegistry, NodeRunner, build_default_registry


@dataclass(frozen=True, slots=True)
class InProcessEngine:
    """An engine bundled with the in-memory store backing it (for read-back)."""

    engine: WorkflowEngine
    store: InMemoryStore
    registry: NodeRegistry


def build_inprocess_engine(
    *,
    registry: NodeRegistry | None = None,
    credential_resolver: CredentialResolver | None = None,
    config: EngineConfig | None = None,
) -> InProcessEngine:
    """Assemble an engine over an in-memory store with the built-in node registry.

    Pass a custom ``registry`` to add nodes beyond the built-ins, a
    ``credential_resolver`` to supply provider secrets, or an ``EngineConfig`` to
    tune lease/backoff. Defaults give a zero-config engine that runs the built-in
    nodes — the CLI's ``run`` path.
    """
    registry = registry or build_default_registry()
    runner = NodeRunner(registry)
    invoker = RunnerInvoker(runner, credential_resolver=credential_resolver)
    store = InMemoryStore()
    engine = WorkflowEngine(store=store, invoker=invoker, config=config)
    return InProcessEngine(engine=engine, store=store, registry=registry)
