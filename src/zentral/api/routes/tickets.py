from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from zentral.api.deps import CtxDep
from zentral.db.models import Attachment, TicketPriority, TicketStatus
from zentral.schemas import (
    ActivityOut,
    AttachmentOut,
    Email,
    InsightsOut,
    MergeIn,
    MessageCreate,
    MessageOut,
    TicketCreate,
    TicketOut,
    TicketPage,
    TicketUpdate,
)
from zentral.services import attachments, insights, tickets
from zentral.services.context import Ctx
from zentral.services.tickets import TicketFilter

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("")
async def list_tickets(
    ctx: CtxDep,
    status_: Annotated[list[TicketStatus] | None, Query(alias="status")] = None,
    priority: TicketPriority | None = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    team_id: uuid.UUID | None = None,
    assignee_id: Annotated[str | None, Query(max_length=64)] = None,
    system_id: uuid.UUID | None = None,
    no_system: bool = False,
    unassigned: bool = False,
    overdue: bool = False,
    requester_email: Email | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> TicketPage:
    page = await tickets.list_tickets(
        ctx,
        TicketFilter(
            status=[s.value for s in status_] if status_ else None,
            priority=priority.value if priority else None,
            category=category,
            team_id=team_id,
            assignee_id=assignee_id,
            system_id=system_id,
            no_system=no_system,
            unassigned=unassigned,
            overdue=overdue,
            requester_email=requester_email,
            q=q,
            limit=limit,
            offset=offset,
        ),
    )
    await ctx.commit()
    return page


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_ticket(ctx: CtxDep, data: TicketCreate) -> TicketOut:
    ticket = await tickets.create_ticket(ctx, data)
    await ctx.commit()
    return ticket


@router.get("/insights")
async def ticket_insights(
    ctx: CtxDep, days: Annotated[int, Query(ge=7, le=365)] = 30
) -> InsightsOut:
    return await insights.insights(ctx, days)


@router.get("/stats")
async def ticket_stats(ctx: CtxDep) -> dict[str, int]:
    return await tickets.ticket_stats(ctx)


@router.get("/{ticket_id}")
async def get_ticket(ctx: CtxDep, ticket_id: int) -> TicketOut:
    ticket = await tickets.get_ticket(ctx, ticket_id)
    await ctx.commit()
    return ticket


@router.patch("/{ticket_id}")
async def update_ticket(ctx: CtxDep, ticket_id: int, patch: TicketUpdate) -> TicketOut:
    ticket = await tickets.update_ticket(ctx, ticket_id, patch)
    await ctx.commit()
    return ticket


@router.post("/{ticket_id}/merge")
async def merge_ticket(ctx: CtxDep, ticket_id: int, data: MergeIn) -> TicketOut:
    """Fold this ticket into another; returns the ticket it was merged into."""
    ticket = await tickets.merge_ticket(ctx, ticket_id, data.into_ticket_id)
    await ctx.commit()
    return ticket


@router.get("/{ticket_id}/messages")
async def list_messages(ctx: CtxDep, ticket_id: int) -> list[MessageOut]:
    messages = await tickets.list_messages(ctx, ticket_id)
    await ctx.commit()
    return messages


@router.get("/{ticket_id}/activity")
async def ticket_activity(ctx: CtxDep, ticket_id: int) -> list[ActivityOut]:
    return await tickets.ticket_activity(ctx, ticket_id)


@router.post("/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
async def add_message(ctx: CtxDep, ticket_id: int, data: MessageCreate) -> MessageOut:
    message = await tickets.add_message(ctx, ticket_id, data)
    await ctx.commit()
    return message


# --- attachments --------------------------------------------------------------------


async def read_upload(request: Request, ctx: Ctx) -> bytes:
    """The raw request body, bounded by the attachment size limit."""
    limit = ctx.settings.attachment_max_bytes
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    return bytes(data)


def file_response(attachment: Attachment, data: bytes) -> Response:
    """Always a download, never rendered by the browser as a page."""
    ascii_name = attachment.filename.encode("ascii", "replace").decode().replace('"', "_")
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(attachment.filename)}"
            ),
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


@router.post("/{ticket_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    request: Request,
    ctx: CtxDep,
    ticket_id: int,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    purpose: Annotated[str, Query(pattern="^(note|reply)$")] = "note",
) -> AttachmentOut:
    """Attach a file (the request body). It is scanned before anyone can open it.

    ``purpose=note`` adds it as an internal note; ``purpose=reply`` holds it for
    the next reply, which sends it to the requester (``attachment_ids``).
    """
    data = await read_upload(request, ctx)
    out = await attachments.upload(ctx, ticket_id, filename, data, purpose)
    await ctx.commit()
    return out


@router.get("/{ticket_id}/attachments")
async def list_attachments(ctx: CtxDep, ticket_id: int) -> list[AttachmentOut]:
    return await attachments.list_for_ticket(ctx, ticket_id)


@router.get("/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(ctx: CtxDep, ticket_id: int, attachment_id: uuid.UUID) -> Response:
    attachment, data = await attachments.download(ctx, ticket_id, attachment_id)
    await ctx.commit()
    return file_response(attachment, data)
