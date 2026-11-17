"""Staff notifications and escalation.

Notifications tell a person *that* something happened to a ticket — never
*what* the ticket says. Each holds a kind and a ticket number; opening the
ticket goes through the usual permission checks and is audited as a read. So a
notification can be shown anywhere (a badge, a desktop alert) without becoming
a side door to personal data.

Who hears about what:

=====================  ==========================================================
Event                  Recipients
=====================  ==========================================================
assigned               the new assignee (not when people assign themselves)
requester_replied      the assignee
*_at_risk              the assignee; if nobody is assigned, the ticket's team
*_breached             the assignee **and** the ticket's team (escalation)
=====================  ==========================================================

"At risk" means the last ``ZENTRAL_ESCALATION_WARN_FRACTION`` of the target's
time has started. Each level is sent once per planned deadline: re-planning a
ticket (new priority, new system, back from pending) arms it again.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update

from zentral.db.models import Notification, TeamMember, Ticket, TicketStatus, utcnow
from zentral.schemas import NotificationOut, NotificationPage
from zentral.security.rbac import Permission as P
from zentral.services.context import Ctx

KINDS = frozenset(
    {
        "assigned",
        "requester_replied",
        "response_at_risk",
        "response_breached",
        "resolve_at_risk",
        "resolve_breached",
    }
)
LEVELS = {None: 0, "at_risk": 1, "breached": 2}
READ_TTL = timedelta(days=30)
UNREAD_TTL = timedelta(days=90)
PAGE = 50


def notify(ctx: Ctx, user_ids: set[str], kind: str, ticket_id: int) -> int:
    """Queue notifications for these people (never for the person acting)."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind!r}")
    recipients = {u for u in user_ids if u and u != ctx.principal.subject}
    for user_id in sorted(recipients):
        ctx.session.add(Notification(user_id=user_id, kind=kind, ticket_id=ticket_id))
    return len(recipients)


async def team_members(ctx: Ctx, team_id: uuid.UUID | None) -> set[str]:
    if team_id is None:
        return set()
    rows = await ctx.session.scalars(
        select(TeamMember.user_id).where(TeamMember.team_id == team_id)
    )
    return set(rows.all())


# --- the escalation sweep ----------------------------------------------------------------------


def _level(
    due: datetime | None, created: datetime | None, now: datetime, fraction: float
) -> str | None:
    if due is None:
        return None
    if now > due:
        return "breached"
    if fraction > 0 and created is not None and (due - now) <= (due - created) * fraction:
        return "at_risk"
    return None


async def escalate(ctx: Ctx, *, batch: int = 500) -> int:
    """Warn about targets at risk and escalate missed ones. Returns notifications sent."""
    now = utcnow()
    fraction = ctx.settings.escalation_warn_fraction
    candidates = (
        (
            await ctx.session.scalars(
                select(Ticket)
                .where(
                    # Pending tickets are waiting on the requester: their clock is paused.
                    Ticket.status.in_([TicketStatus.NEW, TicketStatus.OPEN]),
                    Ticket.merged_into_id.is_(None),
                    or_(
                        and_(
                            Ticket.first_response_at.is_(None),
                            Ticket.requester_id.is_not(None),
                            Ticket.response_due_at.is_not(None),
                            or_(
                                Ticket.response_alert.is_(None), Ticket.response_alert != "breached"
                            ),
                        ),
                        and_(
                            Ticket.resolve_due_at.is_not(None),
                            or_(Ticket.resolve_alert.is_(None), Ticket.resolve_alert != "breached"),
                        ),
                    ),
                )
                .order_by(Ticket.resolve_due_at)
                .limit(batch)
            )
        )
        .unique()
        .all()
    )
    sent = 0
    for ticket in candidates:
        targets = []
        if ticket.first_response_at is None and ticket.requester_id is not None:
            targets.append(("response", ticket.response_due_at, "response_alert"))
        targets.append(("resolve", ticket.resolve_due_at, "resolve_alert"))
        for target, due, field in targets:
            level = _level(due, ticket.created_at, now, fraction)
            if LEVELS[level] <= LEVELS[getattr(ticket, field)]:
                continue
            setattr(ticket, field, level)
            team = await team_members(ctx, ticket.team_id)
            assignee = {ticket.assignee_id} if ticket.assignee_id else set()
            recipients = assignee | team if level == "breached" or not assignee else assignee
            count = notify(ctx, recipients, f"{target}_{level}", ticket.id)
            sent += count
            ctx.audit(
                "ticket.escalated",
                "ticket",
                ticket.id,
                target=target,
                level=level,
                notified=count,
            )
    return sent


# --- reading one's own notifications ------------------------------------------------------------


async def list_mine(ctx: Ctx, *, limit: int = PAGE) -> NotificationPage:
    """The caller's latest notifications. Content-free, so not an audited read."""
    ctx.require(P.TICKET_READ, "notification")
    me = ctx.principal.subject
    rows = (
        await ctx.session.scalars(
            select(Notification)
            .where(Notification.user_id == me)
            .order_by(Notification.created_at.desc())
            .limit(max(1, min(limit, PAGE)))
        )
    ).all()
    unread = await ctx.session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == me, Notification.read_at.is_(None))
    )
    return NotificationPage(
        items=[
            NotificationOut(
                id=n.id,
                kind=n.kind,
                ticket_id=n.ticket_id,
                ticket_key=f"{ctx.settings.ticket_prefix}-{n.ticket_id}",
                created_at=n.created_at,
                read_at=n.read_at,
            )
            for n in rows
        ],
        unread=unread or 0,
    )


async def mark_read(ctx: Ctx, ids: list[uuid.UUID] | None, ticket_id: int | None) -> int:
    """Mark the caller's notifications read: these ids, this ticket's, or all."""
    ctx.require(P.TICKET_READ, "notification")
    stmt = update(Notification).where(
        Notification.user_id == ctx.principal.subject, Notification.read_at.is_(None)
    )
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    if ticket_id is not None:
        stmt = stmt.where(Notification.ticket_id == ticket_id)
    result = await ctx.session.execute(stmt.values(read_at=utcnow()))
    return int(getattr(result, "rowcount", 0) or 0)


async def purge(ctx: Ctx) -> None:
    """Notifications are a to-do list, not a record: drop old ones."""
    now = utcnow()
    await ctx.session.execute(
        delete(Notification).where(
            or_(
                and_(Notification.read_at.is_not(None), Notification.created_at < now - READ_TTL),
                Notification.created_at < now - UNREAD_TTL,
            )
        )
    )
