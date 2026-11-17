"""saved replies, ticket merging, objection to AI processing

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("body", zentral.db.types.EncryptedText(), nullable=False),
        sa.Column(
            "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reply_templates_team_id", "reply_templates", ["team_id"])
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tickets_merged_into", "tickets", ["merged_into_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_tickets_merged_into_id", ["merged_into_id"])
    with op.batch_alter_table("contacts") as batch:
        batch.add_column(
            sa.Column("ai_objected_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.drop_column("ai_objected_at")
    with op.batch_alter_table("tickets") as batch:
        batch.drop_index("ix_tickets_merged_into_id")
        batch.drop_constraint("fk_tickets_merged_into", type_="foreignkey")
        batch.drop_column("merged_into_id")
    op.drop_index("ix_reply_templates_team_id", "reply_templates")
    op.drop_table("reply_templates")
