"""Workflow Authoring domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §3).

Owns: workflow definitions, immutable workflow_versions, validation, schedules,
triggers.

The design-time concept: pure data + a validator, no execution concerns.
Versioning lives here so a running execution pins the exact definition it started
with — editing a workflow can never alter a run already in flight.

Public surface:
  * ``WorkflowGraph`` / ``GraphValidationError`` — the validated DAG value model
  * ``RetryPolicy`` / ``GraphNode`` / ``GraphEdge`` — graph components
  * ``AuthoringStore`` / ``WorkflowNotFoundError`` — persistence + graph resolution
  * ``WorkflowSummary`` / ``WorkflowVersionSummary`` / ``WorkflowVersionRecord`` —
    the pure read records the store returns (no ORM leaks to the delivery layer)
"""

from __future__ import annotations

from axiom.domains.workflow_authoring.graph import (
    BackoffStrategy,
    GraphEdge,
    GraphNode,
    GraphValidationError,
    RetryPolicy,
    WorkflowGraph,
)
from axiom.domains.workflow_authoring.store import (
    AuthoringStore,
    WorkflowNotFoundError,
    WorkflowSummary,
    WorkflowVersionRecord,
    WorkflowVersionSummary,
)

__all__ = [
    "AuthoringStore",
    "BackoffStrategy",
    "GraphEdge",
    "GraphNode",
    "GraphValidationError",
    "RetryPolicy",
    "WorkflowGraph",
    "WorkflowNotFoundError",
    "WorkflowSummary",
    "WorkflowVersionRecord",
    "WorkflowVersionSummary",
]
