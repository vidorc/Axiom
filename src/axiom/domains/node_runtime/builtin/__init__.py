"""Built-in node registration.

``register_builtins`` populates a ``NodeRegistry`` with the reference nodes under
their ``axiom/*`` refs and an initial ``1.0.0`` version. This is called at
worker/CLI startup so the engine can resolve them. Marketplace-installed nodes
(Phase 11) register through the same path.

Two flavours ship: code nodes (``BaseNode`` subclasses in ``nodes.py``) and the
declarative ``http_manifest`` example nodes (``manifests.py``), the latter turned
into node classes by :func:`build_manifest_node`. Both register identically — the
registry doesn't care how a node's behavior is fulfilled.
"""

from __future__ import annotations

from axiom.domains.node_runtime.builtin.manifests import EXAMPLE_MANIFESTS
from axiom.domains.node_runtime.builtin.nodes import (
    DelayNode,
    EchoNode,
    HttpRequestNode,
    SetFieldsNode,
)
from axiom.domains.node_runtime.manifest import parse_manifest
from axiom.domains.node_runtime.manifest_node import build_manifest_node
from axiom.domains.node_runtime.registry import NodeRegistry

# ref -> (version, class). One place to see every built-in CODE node the platform
# ships. The declarative manifest nodes are registered from EXAMPLE_MANIFESTS.
BUILTIN_NODES = (
    ("axiom/echo", "1.0.0", EchoNode),
    ("axiom/set-fields", "1.0.0", SetFieldsNode),
    ("axiom/delay", "1.0.0", DelayNode),
    ("axiom/http-request", "1.0.0", HttpRequestNode),
)


def register_builtins(registry: NodeRegistry) -> None:
    """Register every built-in node (code + manifest) into the given registry."""
    for node_ref, version, node_cls in BUILTIN_NODES:
        registry.register(node_ref, version, node_cls)
    # Declarative example nodes: parse each manifest and register the generated
    # class under the manifest's own id/version.
    for raw in EXAMPLE_MANIFESTS:
        manifest = parse_manifest(raw)
        registry.register(manifest.id, manifest.version, build_manifest_node(manifest))


def build_default_registry() -> NodeRegistry:
    """A fresh registry pre-populated with the built-ins."""
    registry = NodeRegistry()
    register_builtins(registry)
    return registry
