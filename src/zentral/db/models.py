"""Database schema.

Conventions
-----------
* Columns holding personal data or free text use ``EncryptedText``.
* Identifiers that must be searchable are stored as blind indexes
  (``*_hash`` columns), never in clear.
* Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from zentral.db.types import EncryptedJSON, EncryptedText, UTCDateTime


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}  # noqa: RUF012


class TicketStatus(StrEnum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class MessageKind(StrEnum):
    INBOUND = "inbound"  # from the requester
    OUTBOUND = "outbound"  # to the requester
    NOTE = "note"  # internal, never leaves the organisation


class DeliveryStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class User(Base):
    """Staff member or service principal, mirrored from token claims on sign-in."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Entra oid
    kind: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(256), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # Optional: members of this Entra security group belong to the team.
    entra_group_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)


class System(Base):
    """A business system or service that tickets are about (payments, HR portal, …)."""

    __tablename__ = "systems"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Stable machine identifier used by integrations, e.g. "payments".
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="")
    # Words that identify the system in free text (used by AI and the rules fallback).
    keywords: Mapped[list[str]] = mapped_column(default=list)
    # Tickets about this system go to this team unless something else routed them.
    default_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Per-priority hours that replace the global targets for tickets about this
    # system, e.g. {"urgent": 0.5}. Priorities not listed use the global value.
    sla_response_hours: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sla_resolve_hours: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Contact(Base):
    """A requester (data subject)."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Blind index of "<kind>:<identifier>", e.g. "email:jane@example.com".
    identity_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    email: Mapped[str | None] = mapped_column(EncryptedText)
    display_name: Mapped[str | None] = mapped_column(EncryptedText)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    erased_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The person objected to automated analysis (GDPR Art. 21): their tickets
    # are never sent to a model and are hidden from AI agents.
    ai_objected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class IngestSource(Base):
    """A configured inbound/outbound channel instance."""

    __tablename__ = "ingest_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    connector: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secrets: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    default_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL")
    )
    default_system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("systems.id", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cursor: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Health of polling/delivery, shown to administrators. Error text comes from
    # ConnectorError messages only, which never contain content.
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(EncryptedText)
    status: Mapped[str] = mapped_column(String(16), default=TicketStatus.NEW, index=True)
    priority: Mapped[str] = mapped_column(String(16), default=TicketPriority.NORMAL)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    tags: Mapped[list[str]] = mapped_column(default=list)
    channel: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    assignee_id: Mapped[str | None] = mapped_column(String(64), index=True)
    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("systems.id", ondelete="SET NULL"), index=True
    )
    requester_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingest_sources.id", ondelete="SET NULL")
    )
    external_thread_id: Mapped[str | None] = mapped_column(String(64))  # blind index
    # The channel's own conversation reference, kept (encrypted) so replies can
    # be posted into the same thread.
    thread_ref: Mapped[str | None] = mapped_column(EncryptedText)

    ai_category: Mapped[str | None] = mapped_column(String(64))
    ai_priority: Mapped[str | None] = mapped_column(String(16))
    ai_system_key: Mapped[str | None] = mapped_column(String(64))
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_summary: Mapped[str | None] = mapped_column(EncryptedText)
    ai_model: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    scrubbed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Service-level targets (see zentral.services.sla).
    response_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolve_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    first_response_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paused_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # The furthest escalation sent per target ("at_risk", "breached"); cleared
    # whenever the targets are re-planned, so new deadlines warn again.
    response_alert: Mapped[str | None] = mapped_column(String(16))
    resolve_alert: Mapped[str | None] = mapped_column(String(16))

    # Set when this ticket was folded into another; replies follow it there.
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), index=True
    )

    requester: Mapped[Contact | None] = relationship(lazy="joined")
    team: Mapped[Team | None] = relationship(lazy="joined")
    system: Mapped[System | None] = relationship(lazy="joined")
    messages: Mapped[list[Message]] = relationship(
        back_populates="ticket", order_by="Message.created_at", lazy="raise"
    )

    __table_args__ = (Index("ix_tickets_thread", "source_id", "external_thread_id"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(EncryptedText)
    author_user_id: Mapped[str | None] = mapped_column(String(64))
    author_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingest_sources.id", ondelete="SET NULL")
    )
    external_id: Mapped[str | None] = mapped_column(String(64))  # blind index, dedupe key
    delivery_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.NOT_APPLICABLE)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_messages_dedupe"),)


class Notification(Base):
    """Something a staff member should know about a ticket.

    Deliberately content-free: a kind and a ticket number. Whoever opens the
    ticket goes through the normal permission checks and audit trail.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_notifications_user", "user_id", "created_at"),)


class ReplyTemplate(Base):
    """A saved reply agents can start from. Global, or limited to one team."""

    __tablename__ = "reply_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(EncryptedText)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    """Durable background work (transactional outbox)."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_jobs_due", "status", "run_after"),)


class AuditEvent(Base):
    """Append-only, hash-chained audit trail. Never updated, never deleted."""

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_roles: Mapped[list[str]] = mapped_column(default=list)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(16))  # success | denied | failure
    channel: Mapped[str] = mapped_column(String(16))  # api | mcp | hook | worker | cli
    request_id: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (Index("ix_audit_resource", "resource_type", "resource_id"),)
