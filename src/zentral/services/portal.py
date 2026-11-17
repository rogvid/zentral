"""The requester portal.

People who raised a request can follow it, add to it, open a new one and rate
the help they got — without a staff account. They sign in with a one-time link
sent to their e-mail address.

Security model
--------------
* **Who you are** is proven by access to your mailbox: a sign-in link is sent
  only to an address Zentral already knows as a requester. The answer to a
  sign-in request is the same whether or not the address is known, and the
  link is created and sent by the worker, so neither the response nor its
  timing reveals who is a requester.
* **Links** are single-use, expire after ``ZENTRAL_PORTAL_LINK_MINUTES``, and
  are limited per address per hour. **Sessions** last
  ``ZENTRAL_PORTAL_SESSION_HOURS``. Only SHA-256 hashes of either are stored;
  both are revoked when the person's data is erased.
* **What you see** is your own tickets and their public conversation — your
  messages and the desk's replies. Internal notes, AI output, staff names,
  routing and other people's data are never part of a portal response.
* Every portal action is audited with the requester as actor
  (``contact:<id>``).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import delete, func, select

from zentral.connectors.base import OutboundMessage
from zentral.db.models import (
    Contact,
    DeliveryStatus,
    IngestSource,
    Job,
    Message,
    MessageKind,
    PortalToken,
    Rating,
    Ticket,
    TicketStatus,
    utcnow,
)
from zentral.schemas import (
    PortalMessageOut,
    PortalProfile,
    PortalTicketDetail,
    PortalTicketIn,
    PortalTicketOut,
    RatingIn,
)
from zentral.security.crypto import get_cipher
from zentral.services import notifications, sla
from zentral.services.context import Ctx
from zentral.services.errors import NotFound, ValidationFailed
from zentral.services.tickets import ERASED, apply_system, load_system, ticket_key
from zentral.text import clean_text, single_line

RATING_WINDOW = timedelta(days=30)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


async def portal_source(ctx: Ctx) -> IngestSource | None:
    """The mail channel that carries portal links and replies."""
    name = ctx.settings.portal_mail_channel
    if not name:
        return None
    return await ctx.session.scalar(
        select(IngestSource).where(IngestSource.name == name, IngestSource.enabled.is_(True))
    )


# --- signing in ------------------------------------------------------------------------------


async def request_link(ctx: Ctx, email: str) -> None:
    """Queue a sign-in link if this address belongs to a requester. Says nothing either way."""
    identity = get_cipher().blind_index("email", email)
    contact = await ctx.session.scalar(select(Contact).where(Contact.identity_hash == identity))
    if contact is None or contact.erased_at is not None:
        ctx.audit("portal.link_requested", "contact", "", found=False)
        return
    since = utcnow() - timedelta(hours=1)
    recent = await ctx.session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.kind == "send_portal_link", Job.created_at >= since)
        .where(Job.payload["contact_id"].as_string() == str(contact.id))
    )
    if (recent or 0) >= ctx.settings.portal_links_per_hour:
        ctx.audit("portal.link_requested", "contact", contact.id, found=True, throttled=True)
        return
    ctx.session.add(Job(kind="send_portal_link", payload={"contact_id": str(contact.id)}))
    ctx.audit("portal.link_requested", "contact", contact.id, found=True)


async def send_link(
    ctx: Ctx, contact_id: uuid.UUID, connector_send: Callable[[OutboundMessage], Awaitable[None]]
) -> None:
    """Worker side: create the one-time link and e-mail it."""
    contact = await ctx.session.get(Contact, contact_id)
    if contact is None or contact.erased_at is not None or not contact.email:
        return
    token = _new_token()
    ctx.session.add(
        PortalToken(
            contact_id=contact.id,
            kind="link",
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(minutes=ctx.settings.portal_link_minutes),
        )
    )
    base = ctx.settings.public_base_url.rstrip("/")
    minutes = ctx.settings.portal_link_minutes
    await connector_send(
        OutboundMessage(
            ticket_key="",
            subject="Your sign-in link for the service desk",
            body=(
                "Use this link to see and answer your requests to the service desk:\n\n"
                f"{base}/portal#token={token}\n\n"
                f"It works once, for the next {minutes} minutes. If you did not ask "
                "for it, you can ignore this message — nobody can sign in without it."
            ),
            recipient_email=contact.email,
            recipient_name=contact.display_name,
        )
    )
    ctx.audit("portal.link_sent", "contact", contact.id)


async def sign_in(ctx: Ctx, token: str) -> tuple[str, Contact] | None:
    """Exchange a link for a session. ``None`` for any invalid, used or expired link."""
    now = utcnow()
    link = await ctx.session.scalar(
        select(PortalToken).where(
            PortalToken.token_hash == hash_token(token), PortalToken.kind == "link"
        )
    )
    if link is None or link.used_at is not None or link.expires_at < now:
        return None
    contact = await ctx.session.get(Contact, link.contact_id)
    if contact is None or contact.erased_at is not None:
        return None
    link.used_at = now
    session_token = _new_token()
    ctx.session.add(
        PortalToken(
            contact_id=contact.id,
            kind="session",
            token_hash=hash_token(session_token),
            expires_at=now + timedelta(hours=ctx.settings.portal_session_hours),
        )
    )
    ctx.audit("portal.signed_in", "contact", contact.id)
    return session_token, contact


async def session_contact(ctx: Ctx, token: str) -> Contact | None:
    session = await ctx.session.scalar(
        select(PortalToken).where(
            PortalToken.token_hash == hash_token(token), PortalToken.kind == "session"
        )
    )
    if session is None or session.expires_at < utcnow():
        return None
    contact = await ctx.session.get(Contact, session.contact_id)
    if contact is None or contact.erased_at is not None:
        return None
    return contact


async def sign_out(ctx: Ctx, token: str) -> None:
    await ctx.session.execute(
        delete(PortalToken).where(PortalToken.token_hash == hash_token(token))
    )
    ctx.audit("portal.signed_out", "contact", ctx.principal.subject.removeprefix("contact:"))


async def purge_tokens(ctx: Ctx) -> None:
    await ctx.session.execute(delete(PortalToken).where(PortalToken.expires_at < utcnow()))


# --- what a requester sees -----------------------------------------------------------------------


def profile(contact: Contact) -> PortalProfile:
    return PortalProfile(display_name=contact.display_name or "", email=contact.email or "")


async def _rating(ctx: Ctx, ticket_id: int) -> Rating | None:
    return await ctx.session.get(Rating, ticket_id)


def _can_rate(ticket: Ticket, rating: Rating | None) -> bool:
    return (
        rating is None
        and ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        and ticket.merged_into_id is None
        and ticket.scrubbed_at is None
        and ticket.resolved_at is not None
        and utcnow() - ticket.resolved_at <= RATING_WINDOW
    )


def _out(ctx: Ctx, ticket: Ticket, rating: Rating | None) -> PortalTicketOut:
    return PortalTicketOut(
        id=ticket.id,
        key=ticket_key(ctx, ticket.id),
        subject=ticket.subject,
        status=TicketStatus(ticket.status),
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        merged_into_key=ticket_key(ctx, ticket.merged_into_id) if ticket.merged_into_id else None,
        rating=rating.score if rating else None,
        can_rate=_can_rate(ticket, rating),
        can_reply=ticket.status != TicketStatus.CLOSED and ticket.scrubbed_at is None,
    )


async def _own_ticket(ctx: Ctx, contact: Contact, ticket_id: int) -> Ticket:
    ticket = await ctx.session.get(Ticket, ticket_id)
    # Someone else's ticket is indistinguishable from one that does not exist.
    if ticket is None or ticket.requester_id != contact.id:
        raise NotFound("request")
    return ticket


async def list_tickets(ctx: Ctx, contact: Contact) -> list[PortalTicketOut]:
    tickets = (
        (
            await ctx.session.scalars(
                select(Ticket)
                .where(Ticket.requester_id == contact.id, Ticket.scrubbed_at.is_(None))
                .order_by(Ticket.created_at.desc())
                .limit(200)
            )
        )
        .unique()
        .all()
    )
    ratings = {
        r.ticket_id: r
        for r in (
            await ctx.session.scalars(
                select(Rating).where(Rating.ticket_id.in_([t.id for t in tickets]))
            )
        ).all()
    }
    ctx.audit("portal.tickets_listed", "contact", contact.id, count=len(tickets))
    return [_out(ctx, t, ratings.get(t.id)) for t in tickets]


async def get_ticket(ctx: Ctx, contact: Contact, ticket_id: int) -> PortalTicketDetail:
    ticket = await _own_ticket(ctx, contact, ticket_id)
    messages = (
        await ctx.session.scalars(
            select(Message)
            .where(
                Message.ticket_id == ticket.id,
                # The public conversation only: never internal notes.
                Message.kind.in_([MessageKind.INBOUND, MessageKind.OUTBOUND]),
            )
            .order_by(Message.created_at)
        )
    ).all()
    out = [
        PortalMessageOut(
            from_you=m.kind == MessageKind.INBOUND,
            author="You" if m.kind == MessageKind.INBOUND else "Service desk",
            body=m.body,
            created_at=m.created_at,
        )
        for m in messages
        if m.body != ERASED
    ]
    ctx.audit("portal.ticket_read", "ticket", ticket.id)
    return PortalTicketDetail(ticket=_out(ctx, ticket, await _rating(ctx, ticket.id)), messages=out)


# --- what a requester does ----------------------------------------------------------------------


async def add_message(ctx: Ctx, contact: Contact, ticket_id: int, body: str) -> PortalMessageOut:
    ticket = await _own_ticket(ctx, contact, ticket_id)
    if ticket.status == TicketStatus.CLOSED or ticket.scrubbed_at is not None:
        raise ValidationFailed("this request is closed; please start a new one")
    text = clean_text(body, ctx.settings.max_message_chars)
    if not text:
        raise ValidationFailed("the message is empty")
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.PENDING):
        sla.status_changed(ticket, ticket.status, TicketStatus.OPEN, ctx.settings)
        ticket.status = TicketStatus.OPEN
        ticket.resolved_at = None
    ticket.updated_at = utcnow()
    message = Message(
        ticket_id=ticket.id,
        kind=MessageKind.INBOUND,
        body=text,
        author_contact_id=contact.id,
        created_at=utcnow(),
    )
    ctx.session.add(message)
    if ticket.assignee_id:
        notifications.notify(ctx, {ticket.assignee_id}, "requester_replied", ticket.id)
    ctx.audit("ticket.message_received", "ticket", ticket.id, via="portal")
    return PortalMessageOut(from_you=True, author="You", body=text, created_at=message.created_at)


async def create_ticket(ctx: Ctx, contact: Contact, data: PortalTicketIn) -> PortalTicketOut:
    subject = single_line(data.subject, 200)
    body = clean_text(data.body, ctx.settings.max_message_chars)
    if not subject or not body:
        raise ValidationFailed("subject and description are required")
    source = await portal_source(ctx)
    ticket = Ticket(
        subject=subject,
        status=TicketStatus.NEW,
        priority="normal",
        channel="portal",
        requester_id=contact.id,
        # Replies from the desk are e-mailed through the portal's mail channel.
        source_id=source.id if source else None,
        team_id=source.default_team_id if source else None,
        tags=[],
    )
    ticket.requester = contact
    if data.system_id is not None:
        apply_system(ticket, await load_system(ctx, data.system_id))
    ctx.session.add(ticket)
    await ctx.session.flush()
    sla.refresh_targets(ticket, ctx.settings)
    ctx.session.add(
        Message(
            ticket_id=ticket.id,
            kind=MessageKind.INBOUND,
            body=body,
            author_contact_id=contact.id,
            delivery_status=DeliveryStatus.NOT_APPLICABLE,
        )
    )
    ctx.session.add(Job(kind="classify_ticket", payload={"ticket_id": ticket.id}))
    ctx.audit("ticket.created", "ticket", ticket.id, channel="portal")
    return _out(ctx, ticket, None)


async def rate(ctx: Ctx, contact: Contact, ticket_id: int, data: RatingIn) -> PortalTicketOut:
    ticket = await _own_ticket(ctx, contact, ticket_id)
    existing = await _rating(ctx, ticket.id)
    if not _can_rate(ticket, existing):
        raise ValidationFailed("this request cannot be rated (yet, or any more)")
    comment = clean_text(data.comment or "", 2000) or None
    rating = Rating(ticket_id=ticket.id, score=data.score, comment=comment)
    ctx.session.add(rating)
    ctx.audit("ticket.rated", "ticket", ticket.id, score=data.score, commented=bool(comment))
    return _out(ctx, ticket, rating)
