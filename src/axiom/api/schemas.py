"""Request/response schemas for the API layer (Pydantic v2).

These are the HTTP contract — deliberately separate from the domain value
objects (``WorkflowSummary``, ``Execution``, ``ExecutionEventRecord``). The
routers map domain records onto these so the wire format can evolve independently
of the internal model, and so we never serialise an ORM row or leak a field the
API shouldn't expose.

Graphs cross the boundary as opaque JSON objects: structural validation is the
domain's job (``WorkflowGraph.from_dict``, raising ``GraphValidationError`` with
every problem), not Pydantic's — so the editor gets the full problem list, not a
single schema error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ── Workflow authoring ────────────────────────────────────────────────────────


class CreateWorkflowRequest(BaseModel):
    """Create a new workflow and its first immutable version."""

    name: str = Field(min_length=1, max_length=256)
    graph: dict[str, Any] = Field(description="Nodes + edges; validated as a DAG on save.")
    folder: str | None = Field(default=None, max_length=256)
    node_pins: dict[str, Any] = Field(default_factory=dict)


class CreateVersionRequest(BaseModel):
    """Append a new immutable version to an existing workflow."""

    graph: dict[str, Any]
    node_pins: dict[str, Any] = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    """A workflow container's metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    folder: str | None
    current_version_id: UUID | None
    created_at: datetime


class VersionSummaryResponse(BaseModel):
    """Version metadata without the graph payload (for listings)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_id: UUID
    version: int
    created_at: datetime


class VersionResponse(BaseModel):
    """A full immutable version, including its graph."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_id: UUID
    version: int
    graph: dict[str, Any]
    node_pins: dict[str, Any]
    created_at: datetime


class CreateWorkflowResponse(BaseModel):
    """The ids minted by a create — workflow + the version it published."""

    workflow_id: UUID
    version_id: UUID


# ── Execution ─────────────────────────────────────────────────────────────────


class StartRunRequest(BaseModel):
    """Start a run of a workflow.

    ``version`` pins an exact version number; omit it to run the workflow's
    current (last-published) version. ``input`` is the run input handed to root
    nodes.
    """

    input: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None


class NodeErrorResponse(BaseModel):
    error_class: str
    message: str
    retryable: bool
    retry_after_seconds: float | None = None


class NodeStateResponse(BaseModel):
    """One node's state within a run."""

    node_id: str
    status: str
    attempt: int
    output: dict[str, Any] | None
    cost_cents: int
    error: NodeErrorResponse | None
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionResponse(BaseModel):
    """A run's status (without node detail) — the list/summary shape."""

    id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    status: str
    trigger: str
    total_cost_cents: int
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionDetailResponse(ExecutionResponse):
    """A run plus its per-node states — the single-run GET shape."""

    nodes: list[NodeStateResponse]


class EventResponse(BaseModel):
    """One entry from the append-only execution event log."""

    id: int
    seq: int
    type: str
    node_id: str | None
    attempt: int | None
    payload: dict[str, Any]
    created_at: datetime | None


# ── Credential vault (SECURITY.md §5 — "use ≠ read") ────────────────────────────


class BindCredentialRequest(BaseModel):
    """Store a new provider secret.

    ``secret`` is write-only: it is accepted here, envelope-encrypted immediately,
    and never returned by any response (there is no field for it on the way out).
    """

    provider: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    secret: str = Field(min_length=1, repr=False, description="Write-only; never returned.")


class RotateCredentialRequest(BaseModel):
    """Replace a credential's secret in place (same id, new ciphertext + last4)."""

    secret: str = Field(min_length=1, repr=False, description="Write-only; never returned.")


class CredentialResponse(BaseModel):
    """The masked view of a credential — label + ``last4`` + metadata, NEVER the secret.

    Mirrors ``CredentialView``: there is deliberately no ``secret`` field, so no
    API response can carry plaintext. This is the wire-level half of "use ≠ read".
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    provider: str
    label: str
    last4: str
    created_at: datetime
    updated_at: datetime
