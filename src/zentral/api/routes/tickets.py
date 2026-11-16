from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from zentral.api.deps import CtxDep
from zentral.db.models import TicketPriority, TicketStatus
from zentral.schemas import (
    Email,
    MessageCreate,
    MessageOut,
    TicketCreate,
    TicketOut,
    TicketPage,
    TicketUpdate,
)
from zentral.services import tickets
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


@router.get("/{ticket_id}/messages")
async def list_messages(ctx: CtxDep, ticket_id: int) -> list[MessageOut]:
    messages = await tickets.list_messages(ctx, ticket_id)
    await ctx.commit()
    return messages


@router.post("/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
async def add_message(ctx: CtxDep, ticket_id: int, data: MessageCreate) -> MessageOut:
    message = await tickets.add_message(ctx, ticket_id, data)
    await ctx.commit()
    return message
