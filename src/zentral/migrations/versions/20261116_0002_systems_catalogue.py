"""systems catalogue

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "systems",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("default_team_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["default_team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    # Batch mode keeps the migration runnable on SQLite (development) as well.
    with op.batch_alter_table("ingest_sources") as batch:
        batch.add_column(sa.Column("default_system_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_ingest_sources_default_system",
            "systems",
            ["default_system_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("system_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("ai_system_key", sa.String(length=64), nullable=True))
        batch.create_index("ix_tickets_system_id", ["system_id"])
        batch.create_foreign_key(
            "fk_tickets_system", "systems", ["system_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_constraint("fk_tickets_system", type_="foreignkey")
        batch.drop_index("ix_tickets_system_id")
        batch.drop_column("ai_system_key")
        batch.drop_column("system_id")
    with op.batch_alter_table("ingest_sources") as batch:
        batch.drop_constraint("fk_ingest_sources_default_system", type_="foreignkey")
        batch.drop_column("default_system_id")
    op.drop_table("systems")
