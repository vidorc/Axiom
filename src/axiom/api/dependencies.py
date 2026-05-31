"""Shared FastAPI dependencies for the API layer.

Two cross-cutting concerns every domain router needs:

  * **The durable engine** — built once at startup and shared via ``app.state``
    (the AuthoringStore's immutable-version graph cache is worth keeping warm
    across requests), exposed to handlers through ``get_durable``.
  * **Tenant scope** — every store call is org-scoped (the multi-tenant spine,
    DOMAIN_MODEL.md §1). Full auth (sessions, API keys → org) is WS-4 and out of
    scope here; until then ``get_org_id`` reads an ``X-Org-Id`` header and falls
    back to a fixed dev org, so the surface is *built* org-scoped from day one and
    wiring real auth later is additive, not a refactor.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from axiom.composition import DurableEngine
from axiom.domains.credentials import CredentialVault

# A stable dev/default org so the CLI and tests address the same tenant without
# sending a header. NOT a security boundary — it exists only until WS-4 wires
# real auth that derives org_id from an authenticated principal.
DEV_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def get_durable(request: Request) -> DurableEngine:
    """The process-wide durable engine, assembled in the app lifespan."""
    durable: DurableEngine | None = getattr(request.app.state, "durable", None)
    if durable is None:  # pragma: no cover - lifespan always sets it
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="engine not initialised",
        )
    return durable


def get_vault(request: Request) -> CredentialVault:
    """The process-wide credential vault, shared with the engine's resolver."""
    return get_durable(request).vault


async def get_org_id(
    x_org_id: Annotated[str | None, Header(alias="X-Org-Id")] = None,
) -> UUID:
    """Resolve the caller's org from the ``X-Org-Id`` header (dev default if absent).

    Placeholder for real auth (WS-4). Returns 400 on a malformed header rather
    than silently falling back, so a client sending a bad id finds out.
    """
    if x_org_id is None:
        return DEV_ORG_ID
    try:
        return UUID(x_org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Org-Id must be a valid UUID",
        ) from exc


# Type aliases so handler signatures read cleanly.
OrgId = Annotated[UUID, Depends(get_org_id)]
Durable = Annotated[DurableEngine, Depends(get_durable)]
Vault = Annotated[CredentialVault, Depends(get_vault)]
