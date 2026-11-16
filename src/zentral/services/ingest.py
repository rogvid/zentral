"""Inbound message pipeline.

Every channel funnels into ``ingest``: one place that de-duplicates, threads,
resolves the requester, stores (encrypted), queues classification and audits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from zentral.connectors.base import InboundMessage
from zentral.db.models import (
    IngestSource,
    Job,
    Message,
    MessageKind,
    System,
    Ticket,
    TicketPriority,
    TicketStatus,
    utcnow,
)
from zentral.security.crypto import get_cipher
from zentral.services.context import Ctx
from zentral.services.tickets import apply_system, get_or_create_contact, ticket_key
from zentral.text import clean_text, single_line


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    ticket_id: int
    ticket_key: str
    created: bool
    duplicate: bool = False


async def ingest(ctx: Ctx, source: IngestSource, msg: InboundMessage) -> IngestOutcome:
    cipher = get_cipher()
    external_hash = cipher.blind_index(f"msg:{source.id}", msg.external_id)

    existing = await ctx.session.scalar(
        select(Message.ticket_id).where(
            Message.source_id == source.id, Message.external_id == external_hash
        )
    )
    if existing is not None:
        return IngestOutcome(existing, ticket_key(ctx, existing), created=False, duplicate=True)

    subject = single_line(msg.subject, 200) or "(no subject)"
    body = clean_text(msg.body, ctx.settings.max_message_chars) or subject

    contact = None
    if msg.sender_kind and msg.sender_id:
        contact = await get_or_create_contact(
            ctx, msg.sender_kind, msg.sender_id, email=msg.sender_email, name=msg.sender_name
        )

    thread_hash = (
        cipher.blind_index(f"thread:{source.id}", msg.thread_id) if msg.thread_id else None
    )
    ticket = await _find_thread(ctx, source, subject, thread_hash, contact.id if contact else None)

    created = ticket is None
    if ticket is None:
        priority = msg.priority if msg.priority in set(TicketPriority) else TicketPriority.NORMAL
        ticket = Ticket(
            subject=subject,
            status=TicketStatus.NEW,
            priority=priority,
            channel=source.connector,
            team_id=source.default_team_id,
            requester_id=contact.id if contact else None,
            source_id=source.id,
            external_thread_id=thread_hash,
            tags=[],
        )
        system = await _resolve_system(ctx, source, msg)
        if system is not None:
            apply_system(ticket, system)
        ctx.session.add(ticket)
        await ctx.session.flush()
    else:
        if ticket.status in (TicketStatus.RESOLVED, TicketStatus.PENDING):
            ticket.status = TicketStatus.OPEN
            ticket.resolved_at = None
        ticket.updated_at = utcnow()

    message = Message(
        ticket_id=ticket.id,
        kind=MessageKind.INBOUND,
        body=body,
        author_contact_id=contact.id if contact else None,
        source_id=source.id,
        external_id=external_hash,
        created_at=msg.received_at or utcnow(),
    )
    try:
        async with ctx.session.begin_nested():
            ctx.session.add(message)
    except IntegrityError:
        # A concurrent delivery of the same message won the race.
        winner = await ctx.session.scalar(
            select(Message.ticket_id).where(
                Message.source_id == source.id, Message.external_id == external_hash
            )
        )
        if winner is None:
            raise
        if created:
            await ctx.session.delete(ticket)
        return IngestOutcome(winner, ticket_key(ctx, winner), created=False, duplicate=True)

    if created:
        ctx.session.add(Job(kind="classify_ticket", payload={"ticket_id": ticket.id}))
    ctx.audit(
        "ticket.ingested" if created else "ticket.message_received",
        "ticket",
        ticket.id,
        source_id=str(source.id),
        connector=source.connector,
    )
    return IngestOutcome(ticket.id, ticket_key(ctx, ticket.id), created=created)


async def _resolve_system(ctx: Ctx, source: IngestSource, msg: InboundMessage) -> System | None:
    """The sender's explicit hint wins; otherwise the channel's configured default."""
    if msg.system:
        system = await ctx.session.scalar(
            select(System).where(System.key == msg.system.strip().lower(), System.active.is_(True))
        )
        if system is not None:
            return system
    if source.default_system_id is not None:
        system = await ctx.session.get(System, source.default_system_id)
        if system is not None and system.active:
            return system
    return None


async def _find_thread(
    ctx: Ctx,
    source: IngestSource,
    subject: str,
    thread_hash: str | None,
    contact_id: object | None,
) -> Ticket | None:
    # 1. "[ZEN-123]" in the subject — but only if the sender is that ticket's
    #    requester. Ticket numbers are guessable; without this check anyone
    #    could inject text into somebody else's ticket.
    match = re.search(rf"\[{re.escape(ctx.settings.ticket_prefix)}-(\d{{1,12}})\]", subject)
    if match and contact_id is not None:
        ticket = await ctx.session.get(Ticket, int(match.group(1)))
        if (
            ticket is not None
            and ticket.requester_id == contact_id
            and ticket.status != TicketStatus.CLOSED
            and ticket.scrubbed_at is None
        ):
            return ticket
    # 2. Same external conversation on the same (authenticated) source.
    if thread_hash:
        return await ctx.session.scalar(
            select(Ticket)
            .where(
                Ticket.source_id == source.id,
                Ticket.external_thread_id == thread_hash,
                Ticket.status != TicketStatus.CLOSED,
                Ticket.scrubbed_at.is_(None),
            )
            .order_by(Ticket.id.desc())
            .limit(1)
        )
    return None
