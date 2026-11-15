"""GDPR data-subject operations and retention.

* Right of access / portability (Art. 15, 20): ``export_contact``
* Right to erasure (Art. 17): ``erase_contact``
* Storage limitation (Art. 5(1)(e)): ``retention_sweep``

Erasure *scrubs* rather than deletes rows: ticket shells (status, category,
timestamps) remain for statistics, with every free-text field and the link to
the person removed. Audit events are retained — they hold identifiers only,
never content — which is what lets you prove the erasure happened.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from zentral.db.models import Contact, Message, Ticket, TicketStatus, utcnow
from zentral.schemas import ContactOut, ErasureOut
from zentral.security.crypto import get_cipher
from zentral.security.rbac import Permission as P
from zentral.services.context import Ctx
from zentral.services.errors import NotFound
from zentral.services.tickets import ERASED, ticket_key


async def _contact_out(ctx: Ctx, contact: Contact) -> ContactOut:
    count = await ctx.session.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.requester_id == contact.id)
    )
    return ContactOut(
        id=contact.id,
        email=contact.email,
        display_name=contact.display_name,
        created_at=contact.created_at,
        erased_at=contact.erased_at,
        ticket_count=count or 0,
    )


async def find_contact(ctx: Ctx, email: str) -> ContactOut:
    ctx.require(P.PRIVACY_EXPORT, "contact")
    identity = get_cipher().blind_index("email", email)
    contact = await ctx.session.scalar(select(Contact).where(Contact.identity_hash == identity))
    # The lookup itself is audited (without the address) whether or not it matched.
    ctx.audit(
        "privacy.contact_lookup", "contact", contact.id if contact else "", found=bool(contact)
    )
    if contact is None:
        raise NotFound("contact")
    return await _contact_out(ctx, contact)


async def export_contact(ctx: Ctx, contact_id: uuid.UUID) -> dict[str, Any]:
    ctx.require(P.PRIVACY_EXPORT, "contact", str(contact_id))
    contact = await ctx.session.get(Contact, contact_id)
    if contact is None:
        raise NotFound("contact")
    tickets = (
        (
            await ctx.session.scalars(
                select(Ticket).where(Ticket.requester_id == contact.id).order_by(Ticket.id)
            )
        )
        .unique()
        .all()
    )
    exported: list[dict[str, Any]] = []
    for ticket in tickets:
        messages = (
            await ctx.session.scalars(
                select(Message).where(Message.ticket_id == ticket.id).order_by(Message.created_at)
            )
        ).all()
        exported.append(
            {
                "key": ticket_key(ctx, ticket.id),
                "subject": ticket.subject,
                "status": ticket.status,
                "priority": ticket.priority,
                "category": ticket.category,
                "channel": ticket.channel,
                "created_at": ticket.created_at.isoformat(),
                "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
                "ai_summary": ticket.ai_summary,
                "messages": [
                    {
                        "direction": m.kind,
                        "from_data_subject": m.author_contact_id == contact.id,
                        "body": m.body,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in messages
                    # Internal notes are staff working documents and are not
                    # included by default; review them manually if required.
                    if m.kind != "note"
                ],
            }
        )
    ctx.audit("privacy.exported", "contact", contact.id, tickets=len(exported))
    return {
        "generated_at": utcnow().isoformat(),
        "data_subject": {
            "id": str(contact.id),
            "email": contact.email,
            "display_name": contact.display_name,
            "first_seen": contact.created_at.isoformat(),
        },
        "tickets": exported,
    }


async def scrub_ticket(ctx: Ctx, ticket: Ticket) -> int:
    """Remove all free text from a ticket. Returns the number of messages scrubbed."""
    result = await ctx.session.execute(
        update(Message)
        .where(Message.ticket_id == ticket.id)
        .values(body=ERASED, author_contact_id=None, external_id=None)
    )
    ticket.subject = ERASED
    ticket.ai_summary = None
    ticket.requester_id = None
    ticket.requester = None
    ticket.external_thread_id = None
    ticket.tags = []
    ticket.scrubbed_at = utcnow()
    if ticket.status != TicketStatus.CLOSED:
        ticket.status = TicketStatus.CLOSED
        ticket.resolved_at = ticket.resolved_at or utcnow()
    return int(getattr(result, "rowcount", 0) or 0)


async def erase_contact(ctx: Ctx, contact_id: uuid.UUID) -> ErasureOut:
    ctx.require(P.PRIVACY_ERASE, "contact", str(contact_id))
    contact = await ctx.session.get(Contact, contact_id)
    if contact is None:
        raise NotFound("contact")
    tickets = (
        (await ctx.session.scalars(select(Ticket).where(Ticket.requester_id == contact.id)))
        .unique()
        .all()
    )
    messages = 0
    for ticket in tickets:
        messages += await scrub_ticket(ctx, ticket)
    # Messages the person wrote on tickets they did not open.
    stray = await ctx.session.execute(
        update(Message)
        .where(Message.author_contact_id == contact.id)
        .values(body=ERASED, author_contact_id=None, external_id=None)
    )
    messages += int(getattr(stray, "rowcount", 0) or 0)
    contact.email = None
    contact.display_name = None
    contact.identity_hash = None
    contact.erased_at = utcnow()
    ctx.audit("privacy.erased", "contact", contact.id, tickets=len(tickets), messages=messages)
    return ErasureOut(
        contact_id=contact.id, tickets_scrubbed=len(tickets), messages_scrubbed=messages
    )


async def retention_sweep(ctx: Ctx, *, batch: int = 200) -> int:
    """Scrub tickets that have been resolved/closed for longer than the retention period."""
    cutoff = utcnow() - timedelta(days=ctx.settings.retention_days)
    tickets = (
        (
            await ctx.session.scalars(
                select(Ticket)
                .where(
                    Ticket.status.in_([TicketStatus.RESOLVED, TicketStatus.CLOSED]),
                    Ticket.resolved_at < cutoff,
                    Ticket.scrubbed_at.is_(None),
                )
                .limit(batch)
            )
        )
        .unique()
        .all()
    )
    contact_ids = {t.requester_id for t in tickets if t.requester_id}
    for ticket in tickets:
        await scrub_ticket(ctx, ticket)
        ctx.audit("retention.ticket_scrubbed", "ticket", ticket.id)
    await ctx.session.flush()
    # Forget people who no longer have any live ticket.
    for contact_id in contact_ids:
        remaining = await ctx.session.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.requester_id == contact_id)
        )
        if not remaining:
            contact = await ctx.session.get(Contact, contact_id)
            if contact is not None and contact.erased_at is None:
                contact.email = None
                contact.display_name = None
                contact.identity_hash = None
                contact.erased_at = utcnow()
                ctx.audit("retention.contact_erased", "contact", contact.id)
    return len(tickets)
