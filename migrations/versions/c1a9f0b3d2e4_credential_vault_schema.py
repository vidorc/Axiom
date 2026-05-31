"""credential vault schema

Revision ID: c1a9f0b3d2e4
Revises: ea4927ed6500
Create Date: 2026-05-31 17:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'c1a9f0b3d2e4'
down_revision: str | None = 'ea4927ed6500'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Envelope-encrypted, org-scoped provider secrets (SECURITY.md §5). The
    # plaintext is NOT a column — only ciphertext + wrapped_dek + last4 are stored.
    op.create_table(
        'credential',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=False),
        sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
        sa.Column('wrapped_dek', sa.LargeBinary(), nullable=False),
        sa.Column('key_id', sa.String(length=64), nullable=False),
        sa.Column('last4', sa.String(length=4), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_credential_org', 'credential', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_credential_org', table_name='credential')
    op.drop_table('credential')
