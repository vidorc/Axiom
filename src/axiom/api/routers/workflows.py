"""Workflow authoring CRUD endpoints (PHASE_1.md WS-1 / DOMAIN_MODEL.md §3).

The design-time surface: create workflows, append immutable versions, and read
them back. Graph validation is delegated to the domain
(``WorkflowGraph.from_dict`` via ``AuthoringStore.create_workflow_version``); an
invalid graph becomes a 422 carrying *every* structural problem, so the editor
can show them all at once rather than one-at-a-time.

All reads are org-scoped through ``get_org_id``; a workflow that isn't the
caller's reads as 404 (indistinguishable from absent — no cross-tenant probing).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from axiom.api.dependencies import Durable, OrgId
from axiom.api.schemas import (
    CreateVersionRequest,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    VersionResponse,
    VersionSummaryResponse,
    WorkflowResponse,
)
from axiom.domains.workflow_authoring import GraphValidationError, WorkflowNotFoundError

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])


def _invalid_graph(exc: GraphValidationError) -> HTTPException:
    """Turn a domain validation error into a 422 with the full problem list."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": "invalid_graph", "problems": list(exc.problems)},
    )


def _not_found(exc: WorkflowNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateWorkflowResponse)
async def create_workflow(
    body: CreateWorkflowRequest, org_id: OrgId, durable: Durable
) -> CreateWorkflowResponse:
    """Create a workflow and publish its first immutable version."""
    try:
        workflow_id, version_id = await durable.authoring.create_workflow_version(
            org_id=org_id,
            name=body.name,
            graph=body.graph,
            node_pins=body.node_pins,
            folder=body.folder,
        )
    except GraphValidationError as exc:
        raise _invalid_graph(exc) from exc
    return CreateWorkflowResponse(workflow_id=workflow_id, version_id=version_id)


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(org_id: OrgId, durable: Durable) -> list[WorkflowResponse]:
    """List the org's workflows, newest first."""
    summaries = await durable.authoring.list_workflows(org_id=org_id)
    return [WorkflowResponse.model_validate(s) for s in summaries]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: UUID, org_id: OrgId, durable: Durable) -> WorkflowResponse:
    """Fetch one workflow's metadata."""
    try:
        summary = await durable.authoring.get_workflow(org_id=org_id, workflow_id=workflow_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    return WorkflowResponse.model_validate(summary)


@router.post(
    "/{workflow_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateWorkflowResponse,
)
async def create_version(
    workflow_id: UUID, body: CreateVersionRequest, org_id: OrgId, durable: Durable
) -> CreateWorkflowResponse:
    """Append a new immutable version to an existing workflow.

    The workflow must be the caller's (else 404); the new version becomes the
    workflow's current version.
    """
    # Authorize the workflow belongs to this org before mutating it.
    try:
        existing = await durable.authoring.get_workflow(org_id=org_id, workflow_id=workflow_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc

    try:
        _, version_id = await durable.authoring.create_workflow_version(
            org_id=org_id,
            name=existing.name,
            graph=body.graph,
            node_pins=body.node_pins,
            workflow_id=workflow_id,
        )
    except GraphValidationError as exc:
        raise _invalid_graph(exc) from exc
    return CreateWorkflowResponse(workflow_id=workflow_id, version_id=version_id)


@router.get("/{workflow_id}/versions", response_model=list[VersionSummaryResponse])
async def list_versions(
    workflow_id: UUID, org_id: OrgId, durable: Durable
) -> list[VersionSummaryResponse]:
    """List a workflow's versions, highest version first."""
    try:
        versions = await durable.authoring.list_versions(org_id=org_id, workflow_id=workflow_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    return [VersionSummaryResponse.model_validate(v) for v in versions]


@router.get("/{workflow_id}/versions/{version}", response_model=VersionResponse)
async def get_version(
    workflow_id: UUID, version: int, org_id: OrgId, durable: Durable
) -> VersionResponse:
    """Fetch one version in full (graph + pins) by its version number."""
    try:
        # Resolve the version number → id within this org's workflow, then load it.
        version_id = await durable.authoring.resolve_runnable_version(
            org_id=org_id, workflow_id=workflow_id, version=version
        )
        record = await durable.authoring.get_version(version_id)
    except WorkflowNotFoundError as exc:
        raise _not_found(exc) from exc
    return VersionResponse.model_validate(record)
