"""requester portal: sign-in tokens and ratings

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "contact_id",
            sa.Uuid(),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("expires_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("used_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_portal_tokens_contact_id", "portal_tokens", ["contact_id"])
    op.create_table(
        "ratings",
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", zentral.db.types.EncryptedText(), nullable=True),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ratings")
    op.drop_index("ix_portal_tokens_contact_id", "portal_tokens")
    op.drop_table("portal_tokens")
