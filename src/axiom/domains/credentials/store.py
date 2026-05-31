"""CredentialVault — store, mask, and (internally) unwrap org-scoped secrets.

The vault is the only component that touches credential plaintext, and it does so
on exactly two paths:

  * **bind / rotate** (write) — take a plaintext from the operator, envelope-
    encrypt it immediately, persist only the ciphertext. The plaintext is never
    stored and never logged.
  * **resolve** (read, internal) — unwrap a bound credential to plaintext for the
    engine to inject into a node's scoped context at execution time. This is the
    *only* read path that yields plaintext, it is not reachable from any API
    (SECURITY.md §5 — "use ≠ read"), and the result lives in worker memory for the
    duration of one ``execute()``.

Every other read returns a :class:`CredentialView` — label + ``last4`` + metadata,
never the secret. There is deliberately no ``get_plaintext``-style method on the
public read surface: the masking is structural, not a matter of remembering to
redact at the edge.

Tenancy mirrors the AuthoringStore: ``org_id`` scopes every query, and a
credential that isn't the caller's reads as "not found" — indistinguishable from
absent, so one org can't probe another's vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.domains.credentials.models import CredentialRow
from axiom.platform.crypto import EncryptedSecret, EnvelopeCipher
from axiom.platform.logging import get_logger
from axiom.shared.errors import AxiomError

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CredentialNotFoundError(AxiomError):
    """A credential does not exist, or is not the caller's (indistinguishable)."""


@dataclass(frozen=True, slots=True)
class CredentialView:
    """The masked, safe-to-return shape of a credential (SECURITY.md §5).

    Carries everything an operator or the UI needs to identify and manage a
    credential — *except* the secret. There is no field that holds plaintext, by
    construction, so this object can be serialised to any API response without a
    redaction step.
    """

    id: UUID
    org_id: UUID
    provider: str
    label: str
    last4: str
    created_at: datetime
    updated_at: datetime


def _to_view(row: CredentialRow) -> CredentialView:
    return CredentialView(
        id=row.id,
        org_id=row.org_id,
        provider=row.provider,
        label=row.label,
        last4=row.last4,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class CredentialVault:
    """Persistence + envelope crypto for org-scoped provider secrets."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: EnvelopeCipher,
    ) -> None:
        self._sessions = session_factory
        self._cipher = cipher

    # ── Writes (plaintext in, never out) ──────────────────────────────────────

    async def bind(self, *, org_id: UUID, provider: str, label: str, secret: str) -> CredentialView:
        """Encrypt and store a new credential. Returns the masked view, not the secret.

        The plaintext is encrypted *before* the row is built, so a plaintext value
        never reaches the ORM, the session, or a log line. Only the masked view is
        returned to the caller.
        """
        encrypted = self._cipher.encrypt(secret)
        now = _utcnow()
        credential_id = uuid4()
        row = CredentialRow(
            id=credential_id,
            org_id=org_id,
            provider=provider,
            label=label,
            ciphertext=encrypted.ciphertext,
            wrapped_dek=encrypted.wrapped_dek,
            key_id=encrypted.key_id,
            last4=encrypted.last4,
            created_at=now,
            updated_at=now,
        )
        async with self._sessions() as session, session.begin():
            session.add(row)

        # Log the *event*, scoped by ids and last4 only — never the secret, never
        # the ciphertext (SECURITY.md §5.4; "credential" is a redacted key anyway).
        logger.info(
            "credential.bound",
            credential_id=str(credential_id),
            org_id=str(org_id),
            provider=provider,
            last4=encrypted.last4,
        )
        return _to_view(row)

    async def rotate(self, *, org_id: UUID, credential_id: UUID, secret: str) -> CredentialView:
        """Replace a credential's secret in place (same id, new ciphertext + last4).

        Rotation keeps the binding stable: workflows reference the credential by
        id, so rotating the underlying secret takes effect everywhere without
        re-binding. The old ciphertext is overwritten, not retained.
        """
        encrypted = self._cipher.encrypt(secret)
        async with self._sessions() as session, session.begin():
            row = await self._load_owned(session, org_id=org_id, credential_id=credential_id)
            row.ciphertext = encrypted.ciphertext
            row.wrapped_dek = encrypted.wrapped_dek
            row.key_id = encrypted.key_id
            row.last4 = encrypted.last4
            row.updated_at = _utcnow()
            view = _to_view(row)

        logger.info(
            "credential.rotated",
            credential_id=str(credential_id),
            org_id=str(org_id),
            last4=encrypted.last4,
        )
        return view

    async def delete(self, *, org_id: UUID, credential_id: UUID) -> None:
        """Remove a credential. Raises if it isn't the caller's."""
        async with self._sessions() as session, session.begin():
            row = await self._load_owned(session, org_id=org_id, credential_id=credential_id)
            await session.delete(row)
        logger.info("credential.deleted", credential_id=str(credential_id), org_id=str(org_id))

    # ── Reads (masked) ────────────────────────────────────────────────────────

    async def get(self, *, org_id: UUID, credential_id: UUID) -> CredentialView:
        """Fetch one credential's masked view. Raises if absent or not the caller's."""
        async with self._sessions() as session:
            row = await self._load_owned(session, org_id=org_id, credential_id=credential_id)
            return _to_view(row)

    async def list(self, *, org_id: UUID) -> list[CredentialView]:
        """All of an org's credentials as masked views, newest first."""
        async with self._sessions() as session:
            result = await session.execute(
                select(CredentialRow)
                .where(CredentialRow.org_id == org_id)
                .order_by(CredentialRow.created_at.desc())
            )
            return [_to_view(r) for r in result.scalars()]

    # ── Internal: the one plaintext-yielding path (engine only) ───────────────

    async def resolve(self, *, org_id: UUID, credential_id: UUID) -> str:
        """Unwrap a credential to plaintext — for the engine's resolver ONLY.

        This is the single read path that yields a secret, used by the credential
        resolver to inject a node's declared credentials into its scoped context.
        It is *not* exposed by any router or CLI command: there is no HTTP/CLI path
        that reaches it (SECURITY.md §5 — "use ≠ read"). The returned plaintext
        lives only in worker memory for the duration of one node execution.
        """
        async with self._sessions() as session:
            row = await self._load_owned(session, org_id=org_id, credential_id=credential_id)
            encrypted = EncryptedSecret(
                ciphertext=row.ciphertext,
                wrapped_dek=row.wrapped_dek,
                key_id=row.key_id,
                last4=row.last4,
            )
        return self._cipher.decrypt(encrypted)

    async def resolve_many(self, *, org_id: UUID, refs: dict[str, str]) -> dict[str, str]:
        """Resolve a ``{provider: credential_id}`` map to ``{provider: plaintext}``.

        The shape the engine's ``CredentialResolver`` needs: a graph node declares
        ``credentials: {apollo: <cred_id>}`` and the runner injects exactly those
        providers into the scoped context. Each id is org-scoped, so a node can
        never resolve a credential outside its org even if it knows the id.
        """
        resolved: dict[str, str] = {}
        for provider, raw_id in refs.items():
            try:
                credential_id = UUID(raw_id)
            except (ValueError, AttributeError) as exc:
                raise CredentialNotFoundError(
                    f"credential reference for {provider!r} is not a valid id"
                ) from exc
            resolved[provider] = await self.resolve(org_id=org_id, credential_id=credential_id)
        return resolved

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _load_owned(
        self, session: AsyncSession, *, org_id: UUID, credential_id: UUID
    ) -> CredentialRow:
        """Load a row, enforcing org ownership. Absent and cross-org both raise."""
        row = await session.get(CredentialRow, credential_id)
        if row is None or row.org_id != org_id:
            raise CredentialNotFoundError(f"credential {credential_id} not found")
        return row
