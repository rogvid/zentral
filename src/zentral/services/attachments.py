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

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import undefer

from zentral.db.models import Attachment, AttachmentStatus, Job, Ticket, utcnow
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


async def upload(ctx: Ctx, ticket_id: int, filename: str, data: bytes) -> AttachmentOut:
    """Staff attach a file. It appears in the conversation as an internal note."""
    from zentral.db.models import Message, MessageKind

    ticket = await load_ticket(ctx, ticket_id)
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
    """Portal: requesters open only files they sent themselves."""
    ticket = await ctx.session.get(Ticket, ticket_id)
    owned = await ctx.session.scalar(
        select(Attachment.id).where(
            Attachment.id == attachment_id,
            Attachment.ticket_id == ticket_id,
            Attachment.author_contact_id == contact_id,
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
    """For a data-subject access request: the person's files, base64-encoded."""
    rows = (
        await ctx.session.scalars(
            select(Attachment)
            .options(undefer(Attachment.data))
            .where(
                Attachment.ticket_id == ticket_id,
                Attachment.author_contact_id == author_contact_id,
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
