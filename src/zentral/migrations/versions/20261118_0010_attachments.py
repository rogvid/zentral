"""attachments

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", zentral.db.types.EncryptedText(), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("data", zentral.db.types.EncryptedBytes(), nullable=True),
        sa.Column("author_user_id", sa.String(64), nullable=True),
        sa.Column(
            "author_contact_id",
            sa.Uuid(),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("scanned_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_attachments_ticket_id", "attachments", ["ticket_id"])
    op.create_index("ix_attachments_message_id", "attachments", ["message_id"])
    op.create_index("ix_attachments_author_contact_id", "attachments", ["author_contact_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_author_contact_id", "attachments")
    op.drop_index("ix_attachments_message_id", "attachments")
    op.drop_index("ix_attachments_ticket_id", "attachments")
    op.drop_table("attachments")
