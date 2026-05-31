"""The node registry — resolves a ``node_ref`` (+ optional version) to a node.

The engine pins each graph node to an exact node version (``node_pins`` in
DOMAIN_MODEL.md §2); at run time the runtime must hand back *that* implementation.
This registry is the in-process lookup that backs that resolution. It is
deliberately simple and synchronous — registration happens at startup (built-in
nodes) or at install time (marketplace nodes, Phase 11); lookup is on the hot
path and must not do I/O.

Versioning follows SDK_SPEC.md: node versions are SemVer and immutable. A
``node_ref`` without a version resolves to the highest registered version, which
is what authoring uses when first dropping a node onto the canvas; a pinned
``(node_ref, version)`` resolves exactly, which is what execution uses so a
published new version never changes a running workflow's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from axiom.sdk import BaseNode


class NodeNotFoundError(Exception):
    """No node is registered for the requested ref/version."""


class DuplicateNodeError(Exception):
    """A (node_ref, version) was registered twice — node versions are immutable."""


@dataclass(frozen=True, slots=True)
class _SemVer:
    """Just enough SemVer to order versions. major.minor.patch, ints only."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, raw: str) -> _SemVer:
        parts = raw.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"invalid semver: {raw!r} (expected MAJOR.MINOR.PATCH)")
        major, minor, patch = (int(p) for p in parts)
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


@dataclass(frozen=True, slots=True)
class RegisteredNode:
    """A node implementation registered under an exact ref + version."""

    node_ref: str
    version: str
    node_cls: type[BaseNode]


class NodeRegistry:
    """Maps ``node_ref`` (and version) to a ``BaseNode`` subclass.

    Stores classes, not instances — the runner constructs a fresh instance per
    execution so nodes stay stateless (a guarantee the SDK contract relies on).
    """

    def __init__(self) -> None:
        # node_ref -> {version_string -> RegisteredNode}
        self._nodes: dict[str, dict[str, RegisteredNode]] = {}

    def register(self, node_ref: str, version: str, node_cls: type[BaseNode]) -> None:
        """Register a node class under an exact ref + version.

        Raises ``DuplicateNodeError`` if that exact (ref, version) already
        exists — node versions are immutable, so re-registering is a bug.
        """
        _SemVer.parse(version)  # validate early; raises ValueError on bad input
        versions = self._nodes.setdefault(node_ref, {})
        if version in versions:
            raise DuplicateNodeError(f"{node_ref}@{version} is already registered")
        versions[version] = RegisteredNode(node_ref, version, node_cls)

    def resolve(self, node_ref: str, version: str | None = None) -> RegisteredNode:
        """Resolve a node. With a version → exact match; without → highest.

        Raises ``NodeNotFoundError`` if nothing matches. Exact resolution is what
        execution uses (pinned versions); highest is what authoring uses.
        """
        versions = self._nodes.get(node_ref)
        if not versions:
            raise NodeNotFoundError(f"no node registered for ref {node_ref!r}")
        if version is not None:
            found = versions.get(version)
            if found is None:
                raise NodeNotFoundError(f"{node_ref}@{version} is not registered")
            return found
        # Highest version wins.
        latest = max(versions.values(), key=lambda rn: _SemVer.parse(rn.version).key)
        return latest

    def all_refs(self) -> list[str]:
        """Every registered node_ref (for the node library / marketplace)."""
        return sorted(self._nodes)

    def versions_of(self, node_ref: str) -> list[str]:
        """All registered versions of a ref, ascending."""
        versions = self._nodes.get(node_ref, {})
        return sorted(versions, key=lambda s: _SemVer.parse(s).key)
