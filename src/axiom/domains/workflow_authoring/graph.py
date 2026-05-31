"""The workflow graph value model + structural validation (DOMAIN_MODEL.md §2).

A workflow's definition is stored as ``graph`` jsonb on an immutable
``workflow_version`` (nodes + edges + per-node config). This module turns that
jsonb into typed, validated value objects and answers the graph questions the
engine asks at run time: who are the roots, what are a node's predecessors and
successors, and is the graph a valid DAG.

Why this lives in *authoring* and not *execution*: validation is a design-time
concern (DOMAIN_MODEL.md — "a workflow that won't run should fail to save, never
fail mysteriously mid-execution"). The execution engine *consumes* a
already-validated ``WorkflowGraph``; it does not re-derive correctness on the hot
path. Keeping the model here also means the editor and the engine share one
definition of "what a graph is".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from axiom.sdk import JsonObject


class GraphValidationError(Exception):
    """A graph is structurally invalid (cycle, dangling edge, dup id, ...).

    Raised at parse/validate time — i.e. when authoring saves — so the failure
    surfaces in the editor, never mid-run.
    """

    def __init__(self, message: str, *, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        # All problems found, so the editor can show them at once.
        self.problems = problems or (message,)


class BackoffStrategy(StrEnum):
    """How the engine spaces retries for a node (DOMAIN_MODEL.md §2 retry block)."""

    FIXED = "fixed"
    EXPONENTIAL = "exponential"


# Engine-wide ceiling so a malformed/hostile graph can't request unbounded
# retries. A node's max_attempts is clamped to this.
MAX_ATTEMPTS_CEILING = 25
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Per-node retry policy, declared in the graph (retry is engine policy).

    ``max_attempts`` counts *total* tries (so 1 = no retry). The engine only
    ever retries errors whose taxonomy class is retryable (SDK_SPEC.md §4.3);
    ``retry_on`` can further *narrow* that set but never widen it to terminal
    classes — a terminal error is terminal regardless of what the graph asks.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    # Optional narrowing of which retryable error classes to retry on. Empty
    # means "all retryable classes". Values are ErrorClass string values.
    retry_on: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> RetryPolicy:
        if not raw:
            return cls()
        max_attempts = int(raw.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
        if max_attempts < 1:
            raise GraphValidationError("retry.max_attempts must be >= 1")
        max_attempts = min(max_attempts, MAX_ATTEMPTS_CEILING)
        backoff_raw = raw.get("backoff", BackoffStrategy.EXPONENTIAL)
        try:
            backoff = BackoffStrategy(backoff_raw)
        except ValueError as exc:
            raise GraphValidationError(f"unknown retry.backoff: {backoff_raw!r}") from exc
        retry_on = frozenset(str(c) for c in raw.get("retry_on", ()))
        return cls(
            max_attempts=max_attempts,
            backoff=backoff,
            base_delay_seconds=float(raw.get("base_delay_seconds", 1.0)),
            max_delay_seconds=float(raw.get("max_delay_seconds", 60.0)),
            retry_on=retry_on,
        )

    def delay_for_attempt(self, attempt: int) -> float:
        """Backoff delay (seconds) before the given attempt number (1-based).

        Jitter is applied by the engine, not here, so this stays pure and
        deterministically testable. attempt=1 is the first try → no delay.
        """
        if attempt <= 1:
            return 0.0
        retries_done = attempt - 1
        if self.backoff is BackoffStrategy.FIXED:
            delay = self.base_delay_seconds
        else:  # EXPONENTIAL
            delay = self.base_delay_seconds * (2 ** (retries_done - 1))
        return min(delay, self.max_delay_seconds)


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One node in the workflow graph (DOMAIN_MODEL.md §2)."""

    id: str  # graph-local, stable within the workflow (e.g. "n1")
    node_ref: str  # which registry node, e.g. "axiom/apollo-enrich"
    config: JsonObject = field(default_factory=dict)
    # provider -> credential id; bound by reference, unwrapped only at run time.
    credentials: dict[str, str] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GraphNode:
        node_id = raw.get("id")
        if not node_id or not isinstance(node_id, str):
            raise GraphValidationError(f"node is missing a string 'id': {raw!r}")
        node_ref = raw.get("node_ref")
        if not node_ref or not isinstance(node_ref, str):
            raise GraphValidationError(f"node {node_id!r} is missing 'node_ref'")
        return cls(
            id=node_id,
            node_ref=node_ref,
            config=dict(raw.get("config", {})),
            credentials=dict(raw.get("credentials", {})),
            retry=RetryPolicy.from_dict(raw.get("retry")),
        )


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A directed dependency from one node to another (DOMAIN_MODEL.md §2).

    ``mapping`` describes how the source's output feeds the target's input
    (resolved at run time by the expression layer). ``condition`` optionally
    gates traversal — the edge is only satisfied if the expression is truthy.
    Both are opaque to structural validation here; only the topology matters for
    DAG checks.
    """

    source: str  # "from" in jsonb (renamed — `from` is a Python keyword)
    target: str  # "to" in jsonb
    mapping: JsonObject = field(default_factory=dict)
    condition: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GraphEdge:
        source = raw.get("from")
        target = raw.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            raise GraphValidationError(f"edge needs string 'from' and 'to': {raw!r}")
        return cls(
            source=source,
            target=target,
            mapping=dict(raw.get("mapping", {})),
            condition=raw.get("condition"),
        )


@dataclass(frozen=True, slots=True)
class WorkflowGraph:
    """A validated DAG of nodes and edges.

    Construct via :meth:`from_dict`, which parses and validates in one shot.
    Once constructed, the graph is known to be a well-formed DAG and the engine
    can traverse it without defensive checks.
    """

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    # Derived adjacency, computed once at construction.
    _by_id: dict[str, GraphNode] = field(repr=False, compare=False, default_factory=dict)
    _successors: dict[str, tuple[str, ...]] = field(repr=False, compare=False, default_factory=dict)
    _predecessors: dict[str, tuple[str, ...]] = field(
        repr=False, compare=False, default_factory=dict
    )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorkflowGraph:
        """Parse and fully validate a graph jsonb document.

        Raises ``GraphValidationError`` collecting every structural problem:
        empty graph, duplicate node ids, edges referencing unknown nodes, and
        cycles. This is the single gate that guarantees the engine only ever
        runs DAGs.
        """
        nodes = tuple(GraphNode.from_dict(n) for n in raw.get("nodes", []))
        edges = tuple(GraphEdge.from_dict(e) for e in raw.get("edges", []))

        problems: list[str] = []

        if not nodes:
            problems.append("graph has no nodes")

        # Duplicate node ids.
        seen: set[str] = set()
        dupes: set[str] = set()
        for n in nodes:
            if n.id in seen:
                dupes.add(n.id)
            seen.add(n.id)
        if dupes:
            problems.append(f"duplicate node ids: {sorted(dupes)}")

        by_id = {n.id: n for n in nodes}

        # Edges must reference existing nodes; self-loops are cycles too.
        for e in edges:
            if e.source not in by_id:
                problems.append(f"edge references unknown source node {e.source!r}")
            if e.target not in by_id:
                problems.append(f"edge references unknown target node {e.target!r}")
            if e.source == e.target:
                problems.append(f"node {e.source!r} has a self-loop edge")

        # Build adjacency only from edges whose endpoints exist (avoid KeyErrors
        # while still reporting the dangling-edge problems above).
        successors: dict[str, list[str]] = {n.id: [] for n in nodes}
        predecessors: dict[str, list[str]] = {n.id: [] for n in nodes}
        for e in edges:
            if e.source in by_id and e.target in by_id:
                successors[e.source].append(e.target)
                predecessors[e.target].append(e.source)

        # Cycle detection via Kahn's algorithm (only meaningful if no dangling
        # edges; if there are dangling edges we still attempt it on the valid
        # subgraph so the user gets cycle feedback too).
        if not dupes:
            cycle = _find_cycle(by_id.keys(), successors)
            if cycle:
                problems.append(f"graph contains a cycle: {' -> '.join(cycle)}")

        if problems:
            raise GraphValidationError(
                f"invalid workflow graph ({len(problems)} problem(s))",
                problems=tuple(problems),
            )

        return cls(
            nodes=nodes,
            edges=edges,
            _by_id=by_id,
            _successors={k: tuple(v) for k, v in successors.items()},
            _predecessors={k: tuple(v) for k, v in predecessors.items()},
        )

    # ── Traversal API the engine relies on ──────────────────────────────────────

    def node(self, node_id: str) -> GraphNode:
        return self._by_id[node_id]

    def node_ids(self) -> tuple[str, ...]:
        return tuple(n.id for n in self.nodes)

    def roots(self) -> tuple[str, ...]:
        """Nodes with no inbound edges — the initial ready set."""
        return tuple(n.id for n in self.nodes if not self._predecessors[n.id])

    def successors(self, node_id: str) -> tuple[str, ...]:
        return self._successors[node_id]

    def predecessors(self, node_id: str) -> tuple[str, ...]:
        return self._predecessors[node_id]

    def topological_order(self) -> tuple[str, ...]:
        """A valid topological ordering (the graph is known acyclic)."""
        order = _toposort(self.node_ids(), self._successors)
        if order is None:  # pragma: no cover - invariant: validated acyclic at construction
            raise GraphValidationError("topological_order called on a cyclic graph")
        return tuple(order)


# DFS colors for cycle detection. Module-level so they read as the constants
# they are (function-local UPPER_CASE trips N806).
_WHITE, _GREY, _BLACK = 0, 1, 2


def _find_cycle(
    node_ids: Any,
    successors: dict[str, list[str]],
) -> list[str] | None:
    """Return a node sequence forming a cycle, or None if the graph is acyclic.

    DFS with a recursion stack so we can reconstruct the offending path for a
    useful error message (rather than just "there is a cycle somewhere").
    """
    color = dict.fromkeys(node_ids, _WHITE)
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = _GREY
        stack.append(n)
        for m in successors.get(n, []):
            if color[m] == _GREY:
                # Found a back-edge → cycle. Slice the stack from m onward.
                idx = stack.index(m)
                return [*stack[idx:], m]
            if color[m] == _WHITE:
                found = visit(m)
                if found:
                    return found
        color[n] = _BLACK
        stack.pop()
        return None

    for start in color:
        if color[start] == _WHITE:
            found = visit(start)
            if found:
                return found
    return None


def _toposort(
    node_ids: tuple[str, ...],
    successors: dict[str, tuple[str, ...]],
) -> list[str] | None:
    """Kahn's algorithm. Returns None if a cycle prevents a full ordering."""
    indegree = dict.fromkeys(node_ids, 0)
    for n in node_ids:
        for m in successors.get(n, ()):
            indegree[m] += 1
    queue = [n for n in node_ids if indegree[n] == 0]
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in successors.get(n, ()):
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)
    return order if len(order) == len(node_ids) else None
