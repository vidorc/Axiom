"""SQLAlchemy ORM models for the credential vault (SECURITY.md §5, DOMAIN_MODEL.md).

A ``credential`` is an org-owned provider secret, stored *only* in envelope-
encrypted form: ``ciphertext`` is the secret under a per-credential DEK, and
``wrapped_dek`` is that DEK under the org's KEK (see ``axiom.platform.crypto``).
The plaintext is never a column — there is no schema-level path by which the
secret can be read back. ``last4`` is the only plaintext-derived value persisted,
and it is the only one any API is allowed to show ("``last4``-only display").

Tenancy: ``org_id`` scopes every row (the multi-tenant spine, DOMAIN_MODEL.md §1)
and is indexed because the hot read is "this org's credentials".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from axiom.platform.db import Base


class CredentialRow(Base):
    """An org-scoped, envelope-encrypted provider secret (DOMAIN_MODEL.md — credential)."""

    __tablename__ = "credential"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # The provider this secret authenticates against (e.g. "apollo", "openai").
    # A node declares the provider name; the graph binds it to a credential id.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # A human label so an operator can tell two keys for the same provider apart.
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Envelope-encrypted material. bytea, never the plaintext (SECURITY.md §5).
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Which KEK wrapped the DEK — recorded for key rotation.
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The only plaintext-derived value safe to persist or display.
    last4: Mapped[str] = mapped_column(String(4), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Bumped on rotate, so "last rotated" is visible without revealing the value.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_credential_org", "org_id"),)
