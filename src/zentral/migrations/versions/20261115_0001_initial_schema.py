"""initial schema

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import zentral.db.types
from zentral.db import audit_guard

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_roles", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_index(op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"], unique=False)
    op.create_index(
        op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_resource", "audit_events", ["resource_type", "resource_id"], unique=False
    )
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=True),
        sa.Column("email", zentral.db.types.EncryptedText(), nullable=True),
        sa.Column("display_name", zentral.db.types.EncryptedText(), nullable=True),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("erased_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_hash"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_due", "jobs", ["status", "run_after"], unique=False)
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("entra_group_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entra_group_id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("last_seen_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ingest_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets", zentral.db.types.EncryptedJSON(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("default_team_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["default_team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "user_id"),
    )
    op.create_index(op.f("ix_team_members_user_id"), "team_members", ["user_id"], unique=False)
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subject", zentral.db.types.EncryptedText(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("assignee_id", sa.String(length=64), nullable=True),
        sa.Column("requester_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("external_thread_id", sa.String(length=64), nullable=True),
        sa.Column("ai_category", sa.String(length=64), nullable=True),
        sa.Column("ai_priority", sa.String(length=16), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("ai_summary", zentral.db.types.EncryptedText(), nullable=True),
        sa.Column("ai_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("scrubbed_at", zentral.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requester_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["ingest_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tickets_assignee_id"), "tickets", ["assignee_id"], unique=False)
    op.create_index(op.f("ix_tickets_category"), "tickets", ["category"], unique=False)
    op.create_index(op.f("ix_tickets_created_at"), "tickets", ["created_at"], unique=False)
    op.create_index(op.f("ix_tickets_requester_id"), "tickets", ["requester_id"], unique=False)
    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"], unique=False)
    op.create_index(op.f("ix_tickets_team_id"), "tickets", ["team_id"], unique=False)
    op.create_index(
        "ix_tickets_thread", "tickets", ["source_id", "external_thread_id"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("body", zentral.db.types.EncryptedText(), nullable=False),
        sa.Column("author_user_id", sa.String(length=64), nullable=True),
        sa.Column("author_contact_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("delivery_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", zentral.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["ingest_sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_messages_dedupe"),
    )
    op.create_index(
        op.f("ix_messages_author_contact_id"), "messages", ["author_contact_id"], unique=False
    )
    op.create_index(op.f("ix_messages_ticket_id"), "messages", ["ticket_id"], unique=False)
    _protect_audit_trail()


def _protect_audit_trail() -> None:
    """Make the audit table append-only at the database level (PostgreSQL only)."""
    if op.get_bind().dialect.name == "postgresql":
        for statement in audit_guard.INSTALL:
            op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for statement in audit_guard.REMOVE:
            op.execute(statement)
    op.drop_index(op.f("ix_messages_ticket_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_author_contact_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_tickets_thread", table_name="tickets")
    op.drop_index(op.f("ix_tickets_team_id"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_status"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_requester_id"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_created_at"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_category"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_assignee_id"), table_name="tickets")
    op.drop_table("tickets")
    op.drop_index(op.f("ix_team_members_user_id"), table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("ingest_sources")
    op.drop_table("users")
    op.drop_table("teams")
    op.drop_index("ix_jobs_due", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("contacts")
    op.drop_table("automation_rules")
    op.drop_index("ix_audit_resource", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
