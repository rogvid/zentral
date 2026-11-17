"""Attachments: screenshots, logs and documents on tickets.

The path of every file, whichever door it came through::

    received → type checked by content (allow-list) → metadata stripped
             → encrypted and stored (status "scanning") → malware scan (worker)
             → "clean": can be opened by people allowed to see personal data
             → "blocked": content destroyed, the record says why

Rules that hold everywhere:

* A file's name and declared type are never trusted.
* Nothing can be opened before the scanner cleared it.
* Opening a file needs ``ticket:read_pii`` — a screenshot cannot be masked —
  and is audited. AI models and AI agents never receive attachments.
* Audit records hold type and size, never the file name (names can identify
  people: "passport_jane_doe.pdf").
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import undefer

from zentral.db.models import (
    Attachment,
    AttachmentStatus,
    Job,
    Message,
    MessageKind,
    Ticket,
    utcnow,
)
from zentral.files import FileRejected, safe_filename, sanitize, sniff
from zentral.schemas import AttachmentOut
from zentral.security.rbac import Permission as P
from zentral.security.scanner import Scanner
from zentral.services.context import Ctx
from zentral.services.errors import NotFound, PermissionDenied, ValidationFailed
from zentral.services.tickets import load_ticket

GENERIC_NAME = "attachment"


def out(ctx: Ctx, a: Attachment, *, names: bool | None = None) -> AttachmentOut:
    show = ctx.can(P.TICKET_READ_PII) if names is None else names
    return AttachmentOut(
        id=a.id,
        message_id=a.message_id,
        filename=a.filename if show else f"{GENERIC_NAME}.{a.filename.rsplit('.', 1)[-1]}",
        content_type=a.content_type,
        size=a.size,
        status=a.status,
        from_requester=a.author_contact_id is not None,
        created_at=a.created_at,
    )


async def add(
    ctx: Ctx,
    ticket: Ticket,
    filename: str,
    data: bytes,
    *,
    message_id: uuid.UUID | None = None,
    author_user_id: str | None = None,
    author_contact_id: uuid.UUID | None = None,
) -> Attachment:
    """Check, clean, encrypt and store a file; queue its malware scan."""
    settings = ctx.settings
    if not settings.attachments_enabled:
        raise ValidationFailed("attachments are not enabled on this service desk")
    if ticket.scrubbed_at is not None:
        raise ValidationFailed("ticket has been scrubbed under the retention policy")
    if not data:
        raise ValidationFailed("the file is empty")
    if len(data) > settings.attachment_max_bytes:
        raise ValidationFailed(
            f"the file is larger than {settings.attachment_max_bytes // (1024 * 1024)} MB"
        )
    count = await ctx.session.scalar(
        select(func.count()).select_from(Attachment).where(Attachment.ticket_id == ticket.id)
    )
    if (count or 0) >= settings.attachments_per_ticket:
        raise ValidationFailed("this ticket has reached its number of attachments")
    try:
        kind = sniff(data)
        clean = sanitize(kind, data)
    except FileRejected as exc:
        raise ValidationFailed(str(exc)) from None
    attachment = Attachment(
        ticket_id=ticket.id,
        message_id=message_id,
        filename=safe_filename(filename, kind),
        content_type=kind.content_type,
        size=len(clean),
        sha256=hashlib.sha256(clean).hexdigest(),
        status=AttachmentStatus.SCANNING,
        data=clean,
        author_user_id=author_user_id,
        author_contact_id=author_contact_id,
        created_at=utcnow(),
    )
    ctx.session.add(attachment)
    await ctx.session.flush()
    ctx.session.add(Job(kind="scan_attachment", payload={"attachment_id": str(attachment.id)}))
    ctx.audit(
        "attachment.added",
        "ticket",
        ticket.id,
        attachment_id=str(attachment.id),
        content_type=kind.content_type,
        size=len(clean),
        stripped=len(data) - len(clean),
    )
    return attachment


async def scan(ctx: Ctx, scanner: Scanner, attachment_id: uuid.UUID) -> None:
    """Worker: clear a file, or destroy it."""
    attachment = await ctx.session.scalar(
        select(Attachment).options(undefer(Attachment.data)).where(Attachment.id == attachment_id)
    )
    if attachment is None or attachment.status != AttachmentStatus.SCANNING:
        return
    if attachment.data is None:
        return
    result = await scanner.scan(attachment.data)
    attachment.scanned_at = utcnow()
    if result.clean:
        attachment.status = AttachmentStatus.CLEAN
        ctx.audit(
            "attachment.cleared",
            "ticket",
            attachment.ticket_id,
            attachment_id=str(attachment.id),
            scanner=scanner.name,
        )
    else:
        attachment.status = AttachmentStatus.BLOCKED
        attachment.data = None
        ctx.audit(
            "attachment.blocked",
            "ticket",
            attachment.ticket_id,
            outcome="failure",
            attachment_id=str(attachment.id),
            scanner=scanner.name,
            signature=result.signature,
        )


async def list_for_ticket(ctx: Ctx, ticket_id: int) -> list[AttachmentOut]:
    ticket = await load_ticket(ctx, ticket_id)
    rows = (
        await ctx.session.scalars(
            select(Attachment)
            .where(Attachment.ticket_id == ticket.id)
            .order_by(Attachment.created_at)
        )
    ).all()
    return [out(ctx, a) for a in rows]


async def upload(
    ctx: Ctx, ticket_id: int, filename: str, data: bytes, purpose: str = "note"
) -> AttachmentOut:
    """Staff attach a file.

    ``note``: it appears in the conversation at once, as an internal note.
    ``reply``: it waits to be sent with a reply (see ``link_to_reply``); a file
    that is never sent is removed after a day.
    """
    ticket = await load_ticket(ctx, ticket_id)
    if purpose == "reply":
        ctx.require(P.TICKET_REPLY, "ticket", str(ticket_id))
        attachment = await add(ctx, ticket, filename, data, author_user_id=ctx.principal.subject)
        return out(ctx, attachment)
    ctx.require(P.TICKET_NOTE, "ticket", str(ticket_id))
    note = Message(
        ticket_id=ticket.id,
        kind=MessageKind.NOTE,
        body="Attached a file.",
        author_user_id=ctx.principal.subject,
        created_at=utcnow(),
    )
    ctx.session.add(note)
    await ctx.session.flush()
    attachment = await add(
        ctx, ticket, filename, data, message_id=note.id, author_user_id=ctx.principal.subject
    )
    note.body = f"Attached {attachment.filename}."
    ticket.updated_at = utcnow()
    return out(ctx, attachment)


# What a reply may carry by e-mail: Graph takes at most ~4 MB per message.
OUTBOUND_MAX_BYTES = 3 * 1024 * 1024


async def link_to_reply(
    ctx: Ctx, ticket: Ticket, message_id: uuid.UUID, ids: list[uuid.UUID], *, by_mail: bool
) -> list[Attachment]:
    """Attach files uploaded for a reply to that reply. Only files that passed the scan."""
    wanted = set(ids)
    rows = list(
        (
            await ctx.session.scalars(
                select(Attachment).where(
                    Attachment.id.in_(wanted),
                    Attachment.ticket_id == ticket.id,
                    Attachment.author_user_id.is_not(None),
                    Attachment.message_id.is_(None),
                )
            )
        ).all()
    )
    if len(rows) != len(wanted):
        raise ValidationFailed("a file is unknown, belongs elsewhere, or was already sent")
    for a in rows:
        if a.status != AttachmentStatus.CLEAN:
            raise ValidationFailed(
                f"{a.filename} is still being checked for malware"
                if a.status == AttachmentStatus.SCANNING
                else f"{a.filename} was blocked by the malware scanner"
            )
    if by_mail and sum(a.size for a in rows) > OUTBOUND_MAX_BYTES:
        raise ValidationFailed(
            "these files are too large for e-mail together (3 MB); send fewer, or share them "
            "through the requester portal"
        )
    for a in rows:
        a.message_id = message_id
    return rows


async def outbound_files(ctx: Ctx, message_id: uuid.UUID) -> list[Attachment]:
    """Worker: the files to deliver with a reply."""
    return list(
        (
            await ctx.session.scalars(
                select(Attachment)
                .options(undefer(Attachment.data))
                .where(
                    Attachment.message_id == message_id,
                    Attachment.status == AttachmentStatus.CLEAN,
                )
                .order_by(Attachment.created_at)
            )
        ).all()
    )


async def purge_unsent(ctx: Ctx) -> int:
    """Files uploaded for a reply that was never sent do not linger."""
    cutoff = utcnow() - timedelta(days=1)
    result = await ctx.session.execute(
        delete(Attachment).where(
            Attachment.message_id.is_(None),
            Attachment.author_user_id.is_not(None),
            Attachment.created_at < cutoff,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def download(ctx: Ctx, ticket_id: int, attachment_id: uuid.UUID) -> tuple[Attachment, bytes]:
    ticket = await load_ticket(ctx, ticket_id)
    # A screenshot cannot be masked: only people who may see personal data open files.
    ctx.require(P.TICKET_READ_PII, "attachment", str(attachment_id))
    if ctx.is_ai_agent:
        raise PermissionDenied(P.TICKET_READ_PII.value, "attachment", str(attachment_id))
    return await _content(ctx, ticket.id, attachment_id)


async def _content(ctx: Ctx, ticket_id: int, attachment_id: uuid.UUID) -> tuple[Attachment, bytes]:
    attachment = await ctx.session.scalar(
        select(Attachment)
        .options(undefer(Attachment.data))
        .where(Attachment.id == attachment_id, Attachment.ticket_id == ticket_id)
    )
    if attachment is None:
        raise NotFound("attachment")
    if attachment.status != AttachmentStatus.CLEAN or attachment.data is None:
        raise ValidationFailed(
            "this file is still being checked for malware"
            if attachment.status == AttachmentStatus.SCANNING
            else "this file was blocked by the malware scanner"
        )
    ctx.audit(
        "attachment.downloaded",
        "ticket",
        ticket_id,
        attachment_id=str(attachment.id),
        pii=True,
    )
    return attachment, attachment.data


async def requester_download(
    ctx: Ctx, contact_id: uuid.UUID, ticket_id: int, attachment_id: uuid.UUID
) -> tuple[Attachment, bytes]:
    """Portal: requesters open files they sent, and files sent to them."""
    ticket = await ctx.session.get(Ticket, ticket_id)
    shared = select(Message.id).where(
        Message.ticket_id == ticket_id, Message.kind == MessageKind.OUTBOUND
    )
    owned = await ctx.session.scalar(
        select(Attachment.id).where(
            Attachment.id == attachment_id,
            Attachment.ticket_id == ticket_id,
            # Files they sent, and files the desk sent them — never internal ones.
            (Attachment.author_contact_id == contact_id) | Attachment.message_id.in_(shared),
        )
    )
    if ticket is None or ticket.requester_id != contact_id or owned is None:
        raise NotFound("attachment")
    return await _content(ctx, ticket_id, attachment_id)


async def remove_for_ticket(ctx: Ctx, ticket_id: int) -> int:
    """Retention and erasure: files go with the ticket's content."""
    result = await ctx.session.execute(delete(Attachment).where(Attachment.ticket_id == ticket_id))
    return int(getattr(result, "rowcount", 0) or 0)


async def move(ctx: Ctx, source_id: int, target_id: int) -> None:
    await ctx.session.execute(
        update(Attachment).where(Attachment.ticket_id == source_id).values(ticket_id=target_id)
    )


async def export(
    ctx: Ctx, ticket_id: int, *, author_contact_id: uuid.UUID
) -> list[dict[str, object]]:
    """For a data-subject access request: files the person sent and was sent, base64."""
    rows = (
        await ctx.session.scalars(
            select(Attachment)
            .options(undefer(Attachment.data))
            .where(
                Attachment.ticket_id == ticket_id,
                (Attachment.author_contact_id == author_contact_id)
                | Attachment.message_id.in_(
                    select(Message.id).where(
                        Message.ticket_id == ticket_id, Message.kind == MessageKind.OUTBOUND
                    )
                ),
            )
            .order_by(Attachment.created_at)
        )
    ).all()
    return [
        {
            "filename": a.filename,
            "content_type": a.content_type,
            "size": a.size,
            "sha256": a.sha256,
            "status": a.status,
            "content_base64": base64.b64encode(a.data).decode() if a.data else None,
        }
        for a in rows
    ]
