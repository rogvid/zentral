"""service-level targets on tickets

Revision ID: 0004
Revises: 0003

Existing tickets get targets computed from their priority and creation time,
so the inbox is consistent from the first minute after upgrading.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("response_due_at", zentral.db.types.UTCDateTime(timezone=True)))
        batch.add_column(sa.Column("resolve_due_at", zentral.db.types.UTCDateTime(timezone=True)))
        batch.add_column(
            sa.Column("first_response_at", zentral.db.types.UTCDateTime(timezone=True))
        )
        batch.add_column(sa.Column("paused_at", zentral.db.types.UTCDateTime(timezone=True)))
        batch.add_column(
            sa.Column("paused_seconds", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_index("ix_tickets_resolve_due_at", ["resolve_due_at"])
    _backfill()


def _backfill() -> None:
    """Targets for existing tickets, using the current configuration."""
    from datetime import timedelta

    from zentral.config import get_settings
    from zentral.db.types import UTCDateTime

    settings = get_settings()
    tickets = sa.table(
        "tickets",
        sa.column("id", sa.Integer),
        sa.column("priority", sa.String),
        sa.column("created_at", UTCDateTime(timezone=True)),
        sa.column("response_due_at", UTCDateTime(timezone=True)),
        sa.column("resolve_due_at", UTCDateTime(timezone=True)),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(tickets.c.id, tickets.c.priority, tickets.c.created_at)).all()
    for ticket_id, priority, created in rows:
        response = settings.sla_response_hours.get(priority)
        resolve = settings.sla_resolve_hours.get(priority)
        bind.execute(
            tickets.update()
            .where(tickets.c.id == ticket_id)
            .values(
                response_due_at=created + timedelta(hours=response) if response else None,
                resolve_due_at=created + timedelta(hours=resolve) if resolve else None,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_index("ix_tickets_resolve_due_at")
        batch.drop_column("paused_seconds")
        batch.drop_column("paused_at")
        batch.drop_column("first_response_at")
        batch.drop_column("resolve_due_at")
        batch.drop_column("response_due_at")
