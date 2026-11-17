"""staff notifications and escalation state

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("read_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_user", "notifications", ["user_id", "created_at"])
    op.create_index("ix_notifications_ticket_id", "notifications", ["ticket_id"])
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("response_alert", sa.String(16), nullable=True))
        batch.add_column(sa.Column("resolve_alert", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("resolve_alert")
        batch.drop_column("response_alert")
    op.drop_index("ix_notifications_ticket_id", "notifications")
    op.drop_index("ix_notifications_user", "notifications")
    op.drop_table("notifications")
