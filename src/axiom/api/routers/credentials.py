"""Credential vault endpoints (SECURITY.md §5 — "use ≠ read").

The management surface over the :class:`CredentialVault`. Operators bind, list,
rotate, and delete provider secrets — but **no endpoint returns plaintext**. The
secret crosses the boundary only inbound (bind/rotate); every response is a masked
:class:`CredentialResponse` (label + ``last4`` + metadata). The plaintext-yielding
``resolve`` path is *not* mounted here at all — it is reachable only by the engine,
in-process, when injecting a node's declared credentials.

All operations are org-scoped through ``get_org_id``; a credential that isn't the
caller's reads as 404 (indistinguishable from absent — no cross-tenant probing).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from axiom.api.dependencies import OrgId, Vault
from axiom.api.schemas import (
    BindCredentialRequest,
    CredentialResponse,
    RotateCredentialRequest,
)
from axiom.domains.credentials import CredentialNotFoundError

router = APIRouter(prefix="/v1/credentials", tags=["credentials"])


def _not_found(exc: CredentialNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CredentialResponse)
async def bind_credential(
    body: BindCredentialRequest, org_id: OrgId, vault: Vault
) -> CredentialResponse:
    """Store a new provider secret. The response is masked — the secret is not echoed."""
    view = await vault.bind(
        org_id=org_id, provider=body.provider, label=body.label, secret=body.secret
    )
    return CredentialResponse.model_validate(view)


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(org_id: OrgId, vault: Vault) -> list[CredentialResponse]:
    """List the org's credentials as masked views, newest first."""
    views = await vault.list(org_id=org_id)
    return [CredentialResponse.model_validate(v) for v in views]


@router.get("/{credential_id}", response_model=CredentialResponse)
async def get_credential(credential_id: UUID, org_id: OrgId, vault: Vault) -> CredentialResponse:
    """Fetch one credential's masked view."""
    try:
        view = await vault.get(org_id=org_id, credential_id=credential_id)
    except CredentialNotFoundError as exc:
        raise _not_found(exc) from exc
    return CredentialResponse.model_validate(view)


@router.post("/{credential_id}/rotate", response_model=CredentialResponse)
async def rotate_credential(
    credential_id: UUID, body: RotateCredentialRequest, org_id: OrgId, vault: Vault
) -> CredentialResponse:
    """Replace a credential's secret in place (same id, so bindings stay valid)."""
    try:
        view = await vault.rotate(org_id=org_id, credential_id=credential_id, secret=body.secret)
    except CredentialNotFoundError as exc:
        raise _not_found(exc) from exc
    return CredentialResponse.model_validate(view)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(credential_id: UUID, org_id: OrgId, vault: Vault) -> None:
    """Delete a credential. 404 if it isn't the caller's."""
    try:
        await vault.delete(org_id=org_id, credential_id=credential_id)
    except CredentialNotFoundError as exc:
        raise _not_found(exc) from exc
