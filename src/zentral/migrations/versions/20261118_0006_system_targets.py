"""per-system service-level targets

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("systems") as batch:
        batch.add_column(
            sa.Column("sla_response_hours", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("sla_resolve_hours", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("systems") as batch:
        batch.drop_column("sla_resolve_hours")
        batch.drop_column("sla_response_hours")
