"""Ticket operations. All authorisation for ticket data lives here."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, case, false, func, or_, select, true, update

from zentral.db.models import (
    AuditEvent,
    Contact,
    DeliveryStatus,
    Job,
    Message,
    MessageKind,
    System,
    Team,
    Ticket,
    TicketStatus,
    User,
    utcnow,
)
from zentral.schemas import (
    ActivityOut,
    MessageCreate,
    MessageOut,
    RequesterOut,
    TicketCreate,
    TicketOut,
    TicketPage,
    TicketUpdate,
)
from zentral.security.crypto import get_cipher
from zentral.security.rbac import Permission as P
from zentral.security.redaction import redact
from zentral.services import sla
from zentral.services.context import Ctx
from zentral.services.errors import NotFound, PermissionDenied, ValidationFailed

# Free-text search decrypts in the application, so it only looks at a bounded
# window of the most recent tickets the caller may see.
SEARCH_WINDOW = 1000
ERASED = "[erased]"


@dataclass(slots=True)
class TicketFilter:
    status: list[str] | None = None
    priority: str | None = None
    category: str | None = None
    team_id: uuid.UUID | None = None
    assignee_id: str | None = None
    system_id: uuid.UUID | None = None
    no_system: bool = False
    unassigned: bool = False
    overdue: bool = False
    requester_email: str | None = None
    q: str | None = None
    limit: int = 50
    offset: int = 0


def ticket_key(ctx: Ctx, ticket_id: int) -> str:
    return f"{ctx.settings.ticket_prefix}-{ticket_id}"


# --- visibility ----------------------------------------------------------------


def visible_clause(ctx: Ctx) -> ColumnElement[bool]:
    """SQL predicate selecting the tickets the caller is allowed to see."""
    if not ctx.can(P.TICKET_READ):
        return false()
    scope: ColumnElement[bool]
    if ctx.can(P.TICKET_READ_ALL):
        scope = true()
    else:
        clauses: list[ColumnElement[bool]] = [Ticket.assignee_id == ctx.principal.subject]
        if ctx.team_ids:
            clauses.append(Ticket.team_id.in_(ctx.team_ids))
        if ctx.can(P.TICKET_TRIAGE):
            clauses.append(Ticket.team_id.is_(None))
        scope = or_(*clauses)
    if ctx.is_ai_agent:
        objected = select(Contact.id).where(Contact.ai_objected_at.is_not(None))
        scope = and_(
            scope, or_(Ticket.requester_id.is_(None), Ticket.requester_id.not_in(objected))
        )
    return scope


def ai_excluded(ticket: Ticket) -> bool:
    return ticket.requester is not None and ticket.requester.ai_objected_at is not None


def is_visible(ctx: Ctx, ticket: Ticket) -> bool:
    if not ctx.can(P.TICKET_READ):
        return False
    if ctx.is_ai_agent and ai_excluded(ticket):
        return False
    if ctx.can(P.TICKET_READ_ALL) or ticket.assignee_id == ctx.principal.subject:
        return True
    if ticket.team_id is None:
        return ctx.can(P.TICKET_TRIAGE)
    return ticket.team_id in ctx.team_ids


async def load_ticket(ctx: Ctx, ticket_id: int) -> Ticket:
    """Load a ticket the caller may see.

    A ticket outside the caller's scope is reported as *not found* so its
    existence is not disclosed, while the attempt is still audited as denied.
    """
    ctx.require(P.TICKET_READ, "ticket", str(ticket_id))
    ticket = await ctx.session.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFound("ticket")
    if not is_visible(ctx, ticket):
        raise PermissionDenied("ticket:scope", "ticket", str(ticket_id))
    return ticket


# --- presentation ---------------------------------------------------------------


async def _user_names(ctx: Ctx, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = await ctx.session.execute(
        select(User.id, User.display_name, User.email).where(User.id.in_(ids))
    )
    return {r.id: (r.display_name or r.email or r.id) for r in rows}


def _present(ctx: Ctx, ticket: Ticket, names: dict[str, str]) -> TicketOut:
    pii = ctx.can(P.TICKET_READ_PII)
    response, resolve = sla.response_view(ticket), sla.resolve_view(ticket)
    requester = None
    if ticket.requester is not None:
        contact = ticket.requester
        if pii:
            requester = RequesterOut(
                id=contact.id,
                display_name=contact.display_name or contact.email or "Unknown requester",
                email=contact.email,
            )
        else:
            requester = RequesterOut(
                id=contact.id, display_name=f"Requester {str(contact.id)[:8]}", email=None
            )
    return TicketOut(
        id=ticket.id,
        key=ticket_key(ctx, ticket.id),
        subject=ticket.subject if pii else redact(ticket.subject),
        status=TicketStatus(ticket.status),
        priority=ticket.priority,
        category=ticket.category,
        tags=list(ticket.tags or []),
        channel=ticket.channel,
        team_id=ticket.team_id,
        team_name=ticket.team.name if ticket.team else None,
        assignee_id=ticket.assignee_id,
        assignee_name=names.get(ticket.assignee_id) if ticket.assignee_id else None,
        system_id=ticket.system_id,
        system_key=ticket.system.key if ticket.system else None,
        system_name=ticket.system.name if ticket.system else None,
        requester=requester,
        ai_category=ticket.ai_category,
        ai_priority=ticket.ai_priority,
        ai_system_key=ticket.ai_system_key,
        ai_confidence=ticket.ai_confidence,
        ai_summary=ticket.ai_summary,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        response_due_at=response.due_at,
        response_state=response.state,
        first_response_at=ticket.first_response_at,
        resolve_due_at=resolve.due_at,
        resolve_state=resolve.state,
        merged_into_id=ticket.merged_into_id,
        merged_into_key=ticket_key(ctx, ticket.merged_into_id) if ticket.merged_into_id else None,
        ai_excluded=ai_excluded(ticket),
        pii_redacted=not pii,
    )


async def present_ticket(ctx: Ctx, ticket: Ticket) -> TicketOut:
    names = await _user_names(ctx, {ticket.assignee_id} if ticket.assignee_id else set())
    return _present(ctx, ticket, names)


# --- queries ----------------------------------------------------------------------


async def get_ticket(ctx: Ctx, ticket_id: int) -> TicketOut:
    ticket = await load_ticket(ctx, ticket_id)
    ctx.audit("ticket.read", "ticket", ticket.id, pii=ctx.can(P.TICKET_READ_PII))
    return await present_ticket(ctx, ticket)


async def list_tickets(ctx: Ctx, f: TicketFilter) -> TicketPage:
    ctx.require(P.TICKET_READ, "ticket")
    limit = max(1, min(f.limit, 100))
    offset = max(0, f.offset)
    where: list[ColumnElement[bool]] = [visible_clause(ctx)]
    if f.status:
        where.append(Ticket.status.in_(f.status))
    if f.priority:
        where.append(Ticket.priority == f.priority)
    if f.category:
        where.append(Ticket.category == f.category)
    if f.team_id:
        where.append(Ticket.team_id == f.team_id)
    if f.assignee_id:
        where.append(Ticket.assignee_id == f.assignee_id)
    if f.system_id:
        where.append(Ticket.system_id == f.system_id)
    if f.no_system:
        where.append(Ticket.system_id.is_(None))
    if f.unassigned:
        where.append(Ticket.assignee_id.is_(None))
    if f.overdue:
        where.append(overdue_clause())
    if f.requester_email:
        # Looking tickets up by a person's e-mail is a personal-data operation.
        ctx.require(P.TICKET_READ_PII, "ticket")
        identity = get_cipher().blind_index("email", f.requester_email)
        where.append(
            Ticket.requester_id.in_(select(Contact.id).where(Contact.identity_hash == identity))
        )
    base = select(Ticket).where(and_(*where)).order_by(Ticket.created_at.desc(), Ticket.id.desc())

    if f.q:
        needle = f.q.casefold().strip()
        pii = ctx.can(P.TICKET_READ_PII)
        window = (await ctx.session.scalars(base.limit(SEARCH_WINDOW))).unique().all()
        matches = [
            t
            for t in window
            if needle in (t.subject if pii else redact(t.subject)).casefold()
            or needle == ticket_key(ctx, t.id).casefold()
            or needle == str(t.id)
        ]
        total = len(matches)
        rows = matches[offset : offset + limit]
    else:
        total = (
            await ctx.session.scalar(select(func.count()).select_from(Ticket).where(and_(*where)))
        ) or 0
        rows = list((await ctx.session.scalars(base.limit(limit).offset(offset))).unique().all())

    names = await _user_names(ctx, {t.assignee_id for t in rows if t.assignee_id})
    ctx.audit(
        "ticket.list",
        "ticket",
        count=len(rows),
        searched=bool(f.q),
        by_requester=bool(f.requester_email),
    )
    return TicketPage(
        items=[_present(ctx, t, names) for t in rows], total=total, limit=limit, offset=offset
    )


ACTIVE_STATUSES = (TicketStatus.NEW, TicketStatus.OPEN, TicketStatus.PENDING)


def overdue_clause() -> ColumnElement[bool]:
    """Active tickets past a target: no first reply in time, or not resolved in time."""
    now = utcnow()
    return and_(
        Ticket.status.in_(ACTIVE_STATUSES),
        or_(
            and_(
                Ticket.first_response_at.is_(None),
                Ticket.requester_id.is_not(None),
                Ticket.response_due_at < now,
            ),
            and_(Ticket.paused_at.is_(None), Ticket.resolve_due_at < now),
        ),
    )


async def ticket_stats(ctx: Ctx) -> dict[str, int]:
    """Counts of active tickets in the caller's scope, for the inbox overview.
    Aggregates only — no ticket content — so this is not written to the audit trail."""
    ctx.require(P.TICKET_READ, "ticket")

    def count_if(condition: ColumnElement[bool]) -> ColumnElement[int]:
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    row = (
        await ctx.session.execute(
            select(
                func.count().label("active"),
                count_if(Ticket.status == TicketStatus.NEW).label("new"),
                count_if(Ticket.assignee_id.is_(None)).label("unassigned"),
                count_if(Ticket.assignee_id == ctx.principal.subject).label("mine"),
                count_if(Ticket.priority == "urgent").label("urgent"),
                count_if(Ticket.system_id.is_(None)).label("no_system"),
                count_if(overdue_clause()).label("overdue"),
            ).where(visible_clause(ctx), Ticket.status.in_(ACTIVE_STATUSES))
        )
    ).one()
    return {key: int(value) for key, value in row._mapping.items()}


async def list_messages(ctx: Ctx, ticket_id: int) -> list[MessageOut]:
    ticket = await load_ticket(ctx, ticket_id)
    pii = ctx.can(P.TICKET_READ_PII)
    messages = (
        await ctx.session.scalars(
            select(Message).where(Message.ticket_id == ticket.id).order_by(Message.created_at)
        )
    ).all()
    names = await _user_names(ctx, {m.author_user_id for m in messages if m.author_user_id})
    out: list[MessageOut] = []
    for m in messages:
        if m.author_contact_id is not None:
            author_type, author = "requester", "Requester"
            if pii and ticket.requester and ticket.requester.id == m.author_contact_id:
                author = ticket.requester.display_name or ticket.requester.email or "Requester"
        elif m.author_user_id and not m.author_user_id.startswith("system:"):
            author_type, author = "staff", names.get(m.author_user_id, "Staff")
        else:
            author_type, author = "system", "Zentral"
        out.append(
            MessageOut(
                id=m.id,
                kind=m.kind,
                body=m.body if pii else redact(m.body),
                author_name=author,
                author_type=author_type,
                delivery_status=m.delivery_status,
                created_at=m.created_at,
            )
        )
    ctx.audit("ticket.messages.read", "ticket", ticket.id, count=len(out), pii=pii)
    return out


# What happened to a ticket, as opposed to who looked at it. Reads stay with auditors.
ACTIVITY_ACTIONS = (
    "ticket.created",
    "ticket.ingested",
    "ticket.message_received",
    "ticket.updated",
    "ticket.classified",
    "automation.rule_applied",
    "ticket.note_added",
    "ticket.replied",
    "ticket.acknowledged",
    "ticket.auto_closed",
    "ticket.merged",
    "ticket.merge_received",
    "ticket.classification_skipped",
    "message.delivered",
    "message.delivery_failed",
)


async def ticket_activity(ctx: Ctx, ticket_id: int) -> list[ActivityOut]:
    """The ticket's change history for the people working it."""
    ticket = await load_ticket(ctx, ticket_id)
    events = (
        await ctx.session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.resource_type == "ticket",
                AuditEvent.resource_id == str(ticket.id),
                AuditEvent.action.in_(ACTIVITY_ACTIONS),
                AuditEvent.outcome != "denied",
            )
            .order_by(AuditEvent.seq.desc())
            .limit(200)
        )
    ).all()
    names = await _user_names(ctx, {e.actor_id for e in events if e.actor_kind != "system"})
    return [
        ActivityOut(
            occurred_at=e.occurred_at,
            action=e.action,
            actor_name="Zentral" if e.actor_kind == "system" else names.get(e.actor_id, "Staff"),
            actor_kind=e.actor_kind,
            channel=e.channel,
            details=e.details,
        )
        for e in events
    ]


# --- commands ---------------------------------------------------------------------


def _check_category(ctx: Ctx, category: str | None) -> None:
    if category is not None and category not in ctx.settings.categories:
        raise ValidationFailed(f"unknown category '{category}'")


async def _check_team(ctx: Ctx, team_id: uuid.UUID | None) -> None:
    if team_id is not None and await ctx.session.get(Team, team_id) is None:
        raise ValidationFailed("unknown team")


async def load_system(ctx: Ctx, system_id: uuid.UUID) -> System:
    system = await ctx.session.get(System, system_id)
    if system is None or not system.active:
        raise ValidationFailed("unknown or inactive system")
    return system


def apply_system(ticket: Ticket, system: System) -> None:
    """Link a ticket to a system; the system's team takes it if nobody has yet."""
    ticket.system_id = system.id
    ticket.system = system
    if ticket.team_id is None and system.default_team_id is not None:
        ticket.team_id = system.default_team_id


async def get_or_create_contact(
    ctx: Ctx, kind: str, identifier: str, *, email: str | None, name: str | None
) -> Contact:
    identity = get_cipher().blind_index(kind, identifier)
    contact = await ctx.session.scalar(select(Contact).where(Contact.identity_hash == identity))
    if contact is None:
        contact = Contact(identity_hash=identity, email=email, display_name=name or None)
        ctx.session.add(contact)
        await ctx.session.flush()
        ctx.audit("contact.created", "contact", contact.id)
    elif name and not contact.display_name:
        contact.display_name = name
    return contact


async def create_ticket(ctx: Ctx, data: TicketCreate) -> TicketOut:
    ctx.require(P.TICKET_CREATE, "ticket")
    _check_category(ctx, data.category)
    await _check_team(ctx, data.team_id)
    if data.team_id and not (ctx.can(P.TICKET_READ_ALL) or data.team_id in ctx.team_ids):
        ctx.require(P.TICKET_ASSIGN, "ticket")
    requester = None
    if data.requester_email:
        ctx.require(P.TICKET_READ_PII, "ticket")
        requester = await get_or_create_contact(
            ctx, "email", data.requester_email, email=data.requester_email, name=data.requester_name
        )
    ticket = Ticket(
        subject=data.subject,
        status=TicketStatus.NEW,
        priority=data.priority,
        category=data.category,
        tags=sorted(set(data.tags)),
        channel="manual" if ctx.meta.channel == "api" else ctx.meta.channel,
        team_id=data.team_id,
        requester_id=requester.id if requester else None,
    )
    ticket.requester = requester
    if data.system_id is not None:
        apply_system(ticket, await load_system(ctx, data.system_id))
    ctx.session.add(ticket)
    await ctx.session.flush()
    sla.refresh_targets(ticket, ctx.settings)
    ctx.session.add(
        Message(
            ticket_id=ticket.id,
            kind=MessageKind.NOTE if requester is None else MessageKind.INBOUND,
            body=data.body,
            author_user_id=ctx.principal.subject if requester is None else None,
            author_contact_id=requester.id if requester else None,
        )
    )
    ctx.session.add(Job(kind="classify_ticket", payload={"ticket_id": ticket.id}))
    ctx.audit("ticket.created", "ticket", ticket.id, channel=ticket.channel)
    await ctx.session.refresh(ticket, ["team", "system"])
    return await present_ticket(ctx, ticket)


async def update_ticket(ctx: Ctx, ticket_id: int, patch: TicketUpdate) -> TicketOut:
    ticket = await load_ticket(ctx, ticket_id)
    fields = patch.model_fields_set
    changed: dict[str, Any] = {}

    if fields & {"status", "priority", "category", "tags", "system_id"}:
        ctx.require(P.TICKET_UPDATE, "ticket", str(ticket_id))
    if "team_id" in fields and not ctx.can(P.TICKET_ASSIGN):
        # Routing to a team is allowed with ticket:update; picking a person is not.
        ctx.require(P.TICKET_UPDATE, "ticket", str(ticket_id))
    if "assignee_id" in fields:
        ctx.require(P.TICKET_ASSIGN, "ticket", str(ticket_id))

    if "system_id" in fields and patch.system_id != ticket.system_id:
        changed["system_id"] = [str(ticket.system_id or ""), str(patch.system_id or "")]
        if patch.system_id is None:
            ticket.system_id, ticket.system = None, None
        else:
            apply_system(ticket, await load_system(ctx, patch.system_id))
    if "status" in fields and patch.status is not None and patch.status != ticket.status:
        closing = patch.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        if closing and ctx.settings.require_system_to_resolve and ticket.system_id is None:
            raise ValidationFailed("select the system this ticket concerns before resolving it")
        changed["status"] = [ticket.status, patch.status.value]
        sla.status_changed(ticket, ticket.status, patch.status, ctx.settings)
        ticket.status = patch.status
        if patch.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            ticket.resolved_at = ticket.resolved_at or utcnow()
        else:
            ticket.resolved_at = None
    if "priority" in fields and patch.priority is not None and patch.priority != ticket.priority:
        changed["priority"] = [ticket.priority, patch.priority.value]
        ticket.priority = patch.priority
        sla.refresh_targets(ticket, ctx.settings)
    if "category" in fields and patch.category != ticket.category:
        _check_category(ctx, patch.category)
        changed["category"] = [ticket.category, patch.category]
        ticket.category = patch.category
    if "tags" in fields and patch.tags is not None:
        tags = sorted(set(patch.tags))
        if tags != ticket.tags:
            changed["tags"] = True
            ticket.tags = tags
    if "team_id" in fields and patch.team_id != ticket.team_id:
        await _check_team(ctx, patch.team_id)
        changed["team_id"] = [str(ticket.team_id or ""), str(patch.team_id or "")]
        ticket.team_id = patch.team_id
    if "assignee_id" in fields and patch.assignee_id != ticket.assignee_id:
        if patch.assignee_id is not None:
            user = await ctx.session.get(User, patch.assignee_id)
            if user is None or user.kind != "user":
                raise ValidationFailed("unknown assignee")
        changed["assignee_id"] = [ticket.assignee_id or "", patch.assignee_id or ""]
        ticket.assignee_id = patch.assignee_id
        if ticket.status == TicketStatus.NEW and patch.assignee_id:
            ticket.status = TicketStatus.OPEN

    if changed:
        ticket.updated_at = utcnow()
        ctx.audit("ticket.updated", "ticket", ticket.id, changes=changed)
        await ctx.session.flush()
        await ctx.session.refresh(ticket, ["team", "system"])
    return await present_ticket(ctx, ticket)


async def add_message(ctx: Ctx, ticket_id: int, data: MessageCreate) -> MessageOut:
    ticket = await load_ticket(ctx, ticket_id)
    if ticket.scrubbed_at is not None:
        raise ValidationFailed("ticket has been scrubbed under the retention policy")
    outbound = data.kind == "outbound"
    ctx.require(P.TICKET_REPLY if outbound else P.TICKET_NOTE, "ticket", str(ticket_id))
    delivery = DeliveryStatus.NOT_APPLICABLE
    if outbound:
        if ticket.requester_id is None:
            raise ValidationFailed("ticket has no requester to reply to")
        delivery = DeliveryStatus.PENDING if ticket.source_id else DeliveryStatus.NOT_APPLICABLE
    message = Message(
        ticket_id=ticket.id,
        kind=MessageKind.OUTBOUND if outbound else MessageKind.NOTE,
        body=data.body,
        author_user_id=ctx.principal.subject,
        delivery_status=delivery,
    )
    ctx.session.add(message)
    await ctx.session.flush()
    if delivery == DeliveryStatus.PENDING:
        ctx.session.add(Job(kind="send_message", payload={"message_id": str(message.id)}))
    if outbound:
        sla.responded(ticket)
    if outbound and ticket.status == TicketStatus.NEW:
        ticket.status = TicketStatus.OPEN
    ticket.updated_at = utcnow()
    ctx.audit(
        "ticket.replied" if outbound else "ticket.note_added",
        "ticket",
        ticket.id,
        message_id=str(message.id),
    )
    return MessageOut(
        id=message.id,
        kind=message.kind,
        body=message.body,
        author_name=ctx.principal.display_name or "Staff",
        author_type="staff",
        delivery_status=message.delivery_status,
        created_at=message.created_at,
    )


async def auto_close(ctx: Ctx, *, batch: int = 500) -> int:
    """Close tickets that stayed resolved for ``auto_close_days``.

    A resolved ticket reopens when the requester answers; a closed one does not
    (their next message starts a new ticket). The grace period is the window in
    which "that didn't fix it" still lands on the same ticket.
    """
    days = ctx.settings.auto_close_days
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    tickets = (
        (
            await ctx.session.scalars(
                select(Ticket)
                .where(Ticket.status == TicketStatus.RESOLVED, Ticket.resolved_at < cutoff)
                .limit(batch)
            )
        )
        .unique()
        .all()
    )
    for ticket in tickets:
        ticket.status = TicketStatus.CLOSED
        ctx.audit("ticket.auto_closed", "ticket", ticket.id, after_days=days)
    return len(tickets)


MAX_MERGE_HOPS = 5


async def follow_merges(ctx: Ctx, ticket: Ticket) -> Ticket:
    """The ticket a merged ticket now lives on (bounded, in case of a cycle)."""
    for _ in range(MAX_MERGE_HOPS):
        if ticket.merged_into_id is None:
            return ticket
        target = await ctx.session.get(Ticket, ticket.merged_into_id)
        if target is None:
            return ticket
        ticket = target
    return ticket


async def merge_ticket(ctx: Ctx, source_id: int, target_id: int) -> TicketOut:
    """Fold a duplicate into another ticket.

    The conversation moves to the target, the duplicate is closed and points at
    it, and later replies on the duplicate's thread land on the target. Only
    tickets from the same requester can be merged: merging two people's
    requests would put one person's words on the other's ticket and send
    replies to only one of them.
    """
    if source_id == target_id:
        raise ValidationFailed("a ticket cannot be merged into itself")
    source = await load_ticket(ctx, source_id)
    target = await load_ticket(ctx, target_id)
    ctx.require(P.TICKET_UPDATE, "ticket", str(source_id))
    if source.merged_into_id is not None:
        raise ValidationFailed("this ticket has already been merged")
    if target.merged_into_id is not None:
        raise ValidationFailed(
            "the target ticket was itself merged; merge into the ticket it points to"
        )
    if source.scrubbed_at is not None or target.scrubbed_at is not None:
        raise ValidationFailed("a ticket scrubbed under the retention policy cannot be merged")
    if target.status == TicketStatus.CLOSED:
        raise ValidationFailed("the target ticket is closed")
    if source.requester_id is not None and source.requester_id != target.requester_id:
        raise ValidationFailed("only tickets from the same requester can be merged")

    moved = await ctx.session.execute(
        update(Message).where(Message.ticket_id == source.id).values(ticket_id=target.id)
    )
    count = int(getattr(moved, "rowcount", 0) or 0)
    source_key, target_key = ticket_key(ctx, source.id), ticket_key(ctx, target.id)
    ctx.session.add(
        Message(
            ticket_id=target.id,
            kind=MessageKind.NOTE,
            body=f"{source_key} was merged into this ticket ({count} messages moved).",
            author_user_id=ctx.principal.subject,
        )
    )
    ctx.session.add(
        Message(
            ticket_id=source.id,
            kind=MessageKind.NOTE,
            body=f"Merged into {target_key}. The conversation continues there.",
            author_user_id=ctx.principal.subject,
        )
    )
    target.tags = sorted(set(target.tags or []) | set(source.tags or []))
    if source.first_response_at and not target.first_response_at:
        target.first_response_at = source.first_response_at
    if target.status in (TicketStatus.RESOLVED, TicketStatus.PENDING):
        sla.status_changed(target, target.status, TicketStatus.OPEN, ctx.settings)
        target.status = TicketStatus.OPEN
        target.resolved_at = None
    target.updated_at = utcnow()

    sla.status_changed(source, source.status, TicketStatus.CLOSED, ctx.settings)
    source.status = TicketStatus.CLOSED
    source.resolved_at = source.resolved_at or utcnow()
    source.merged_into_id = target.id
    source.updated_at = utcnow()

    ctx.audit("ticket.merged", "ticket", source.id, into=target.id, messages=count)
    ctx.audit("ticket.merge_received", "ticket", target.id, source=source.id, messages=count)
    await ctx.session.flush()
    await ctx.session.refresh(target, ["team", "system"])
    return await present_ticket(ctx, target)
