"""Node Runtime domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §6, SDK_SPEC.md).

Owns: node loading, the three node kinds (http_manifest / code / mcp), I/O
contracts, version pinning, and trust-tier enforcement at execution.

The plugin boundary — third-party code crosses it, so it is the most rigorously
contracted seam in the system. The *public* node contract lives in axiom.sdk
(extractable in Phase 2); this domain is the engine-side runtime that loads and
executes nodes against that contract.

It is a *pure executor* — it knows nothing about DAGs, scheduling, or the event
log. The engine drives it through the ``NodeInvoker`` port (defined in
``axiom.domains.execution``), adapted at the worker layer so this package never
imports the engine (import-linter Contract 2).

Public surface:
  * ``NodeRegistry`` / ``RegisteredNode`` — version-pinned node resolution
  * ``NodeRunner`` / ``NodeRunResult``     — execute one attempt, neutral result
  * ``NodeExecutionContext``               — the concrete sdk.ExecutionContext
  * ``Manifest`` / ``parse_manifest`` / ``ManifestError`` — the http_manifest contract
  * ``build_manifest_node``                — turn a manifest into a BaseNode class
  * ``build_default_registry`` / ``register_builtins`` — the built-in nodes
"""

from __future__ import annotations

from axiom.domains.node_runtime.builtin import (
    BUILTIN_NODES,
    build_default_registry,
    register_builtins,
)
from axiom.domains.node_runtime.context import NodeExecutionContext, RunContext
from axiom.domains.node_runtime.manifest import (
    Manifest,
    ManifestError,
    parse_manifest,
)
from axiom.domains.node_runtime.manifest_node import build_manifest_node
from axiom.domains.node_runtime.registry import (
    DuplicateNodeError,
    NodeNotFoundError,
    NodeRegistry,
    RegisteredNode,
)
from axiom.domains.node_runtime.runner import NodeRunner, NodeRunResult

__all__ = [
    "BUILTIN_NODES",
    "DuplicateNodeError",
    "Manifest",
    "ManifestError",
    "NodeExecutionContext",
    "NodeNotFoundError",
    "NodeRegistry",
    "NodeRunResult",
    "NodeRunner",
    "RegisteredNode",
    "RunContext",
    "build_default_registry",
    "build_manifest_node",
    "parse_manifest",
    "register_builtins",
]
