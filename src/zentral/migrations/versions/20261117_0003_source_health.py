"""channel health columns

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ingest_sources") as batch:
        batch.add_column(sa.Column("last_ok_at", zentral.db.types.UTCDateTime(timezone=True)))
        batch.add_column(sa.Column("last_error", sa.String(length=300)))
        batch.add_column(sa.Column("last_error_at", zentral.db.types.UTCDateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("ingest_sources") as batch:
        batch.drop_column("last_error_at")
        batch.drop_column("last_error")
        batch.drop_column("last_ok_at")
