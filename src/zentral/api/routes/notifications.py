"""The caller's own notifications."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from zentral.api.deps import CtxDep
from zentral.schemas import NotificationPage, NotificationsRead
from zentral.services import notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    ctx: CtxDep, limit: Annotated[int, Query(ge=1, le=50)] = 30
) -> NotificationPage:
    return await notifications.list_mine(ctx, limit=limit)


@router.post("/read")
async def mark_read(ctx: CtxDep, data: NotificationsRead) -> dict[str, int]:
    count = await notifications.mark_read(ctx, data.ids, data.ticket_id)
    await ctx.commit()
    return {"marked": count}
