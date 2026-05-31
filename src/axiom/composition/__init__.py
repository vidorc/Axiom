"""Composition root — assembles domains into a runnable engine.

This is the one place allowed to wire the execution engine to the node runtime.
It sits in its own layer *below* the delivery entrypoints (api/worker/cli) and
*above* the domains, so every entrypoint shares one assembly path instead of
each re-implementing the wiring (which would also force one delivery sibling to
import another — forbidden by the layered contract).

It can import both ``axiom.domains.execution`` and ``axiom.domains.node_runtime``
because it is neither of them: the rule "node runtime must not depend on
execution" (and the design rule that execution does not import the runtime) is
satisfied precisely *because* the two meet here, through the ``NodeInvoker`` port,
rather than importing each other directly.

Public surface:
  * ``RunnerInvoker``        — adapts the runtime's NodeRunner to the NodeInvoker port
  * ``CredentialResolver``   — the (org, refs) → plaintext hook (default: none)
  * ``build_vault_resolver`` — wires the real credential vault as that hook
  * ``build_inprocess_engine`` — a ready in-memory engine for the CLI/tests
  * ``build_durable_engine`` / ``DurableEngine`` — the Postgres-backed engine the
    worker drives, with a DB graph resolver and a durable ``enqueue_run``
"""

from __future__ import annotations

from axiom.composition.assembly import InProcessEngine, build_inprocess_engine
from axiom.composition.durable import (
    DbGraphResolver,
    DurableEngine,
    build_durable_engine,
)
from axiom.composition.invoker import (
    CredentialResolver,
    RunnerInvoker,
    build_vault_resolver,
)

__all__ = [
    "CredentialResolver",
    "DbGraphResolver",
    "DurableEngine",
    "InProcessEngine",
    "RunnerInvoker",
    "build_durable_engine",
    "build_inprocess_engine",
    "build_vault_resolver",
]
