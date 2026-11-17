"""audit archiving: checkpoints and a prune-aware guard

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types
from zentral.db import audit_guard

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_checkpoints",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("first_seq", sa.Integer(), nullable=False),
        sa.Column("events", sa.Integer(), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("prev_signature", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        for statement in audit_guard.PRUNE_AWARE:
            op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for statement in audit_guard.PRUNE_AWARE_REMOVE:
            op.execute(statement)
    op.drop_table("audit_checkpoints")
