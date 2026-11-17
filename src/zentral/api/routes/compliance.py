"""Audit trail and GDPR endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from zentral.api.deps import CtxDep
from zentral.db.models import AuditEvent
from zentral.schemas import (
    AuditEventOut,
    AuditPage,
    AuditVerifyOut,
    ContactLookup,
    ContactOut,
    ErasureOut,
    ObjectionIn,
)
from zentral.security.rbac import Permission
from zentral.services import privacy

router = APIRouter(tags=["compliance"])


@router.get("/audit/events")
async def list_audit_events(
    ctx: CtxDep,
    actor_id: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    resource_type: Annotated[str | None, Query(max_length=32)] = None,
    resource_id: Annotated[str | None, Query(max_length=64)] = None,
    outcome: Annotated[str | None, Query(pattern="^(success|denied|failure)$")] = None,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    ctx.require(Permission.AUDIT_READ, "audit")
    stmt = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit + 1)
    for column, value in (
        (AuditEvent.actor_id, actor_id),
        (AuditEvent.action, action),
        (AuditEvent.resource_type, resource_type),
        (AuditEvent.resource_id, resource_id),
        (AuditEvent.outcome, outcome),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if before_seq is not None:
        stmt = stmt.where(AuditEvent.seq < before_seq)
    rows = list((await ctx.session.scalars(stmt)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    # Reading the audit trail is itself audited.
    ctx.audit("audit.read", "audit", count=len(rows))
    page = AuditPage(
        items=[AuditEventOut.model_validate(r) for r in rows],
        next_before_seq=rows[-1].seq if more and rows else None,
    )
    await ctx.commit()
    return page


@router.post("/audit/verify")
async def verify_audit_chain(ctx: CtxDep) -> AuditVerifyOut:
    ctx.require(Permission.AUDIT_READ, "audit")
    result = await ctx.trail.verify(ctx.session)
    ctx.audit(
        "audit.verified",
        "audit",
        outcome="success" if result.ok else "failure",
        checked=result.checked,
    )
    await ctx.commit()
    return AuditVerifyOut(
        ok=result.ok,
        checked=result.checked,
        first_bad_seq=result.first_bad_seq,
        archived_through=result.archived_through,
    )


# The e-mail address travels in the request body, not the URL, so that it does
# not end up in proxy or browser logs.
@router.post("/privacy/contacts/lookup")
async def lookup_contact(ctx: CtxDep, data: ContactLookup) -> ContactOut:
    try:
        return await privacy.find_contact(ctx, data.email)
    finally:
        await ctx.commit()


@router.get("/privacy/contacts/{contact_id}/export")
async def export_contact(ctx: CtxDep, contact_id: uuid.UUID) -> dict[str, Any]:
    export = await privacy.export_contact(ctx, contact_id)
    await ctx.commit()
    return export


@router.post("/privacy/contacts/{contact_id}/erase")
async def erase_contact(ctx: CtxDep, contact_id: uuid.UUID) -> ErasureOut:
    result = await privacy.erase_contact(ctx, contact_id)
    await ctx.commit()
    return result


@router.put("/privacy/contacts/{contact_id}/ai-objection")
async def set_ai_objection(ctx: CtxDep, contact_id: uuid.UUID, data: ObjectionIn) -> ContactOut:
    result = await privacy.set_ai_objection(ctx, contact_id, objected=data.objected)
    await ctx.commit()
    return result
