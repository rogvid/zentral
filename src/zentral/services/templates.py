"""Saved replies.

A template is text an agent starts from; nothing is sent until the agent sends
it. Templates may be global or belong to one team, and agents see the global
ones plus those of their own teams. Bodies are encrypted like any other free
text, because in practice they end up containing internal details.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select

from zentral.db.models import ReplyTemplate, Team, utcnow
from zentral.schemas import TemplateIn, TemplateOut, TemplateUpdate
from zentral.security.rbac import Permission as P
from zentral.services.context import Ctx
from zentral.services.errors import NotFound, PermissionDenied, ValidationFailed

MAX_TEMPLATES = 500


def _out(template: ReplyTemplate, teams: dict[uuid.UUID, str]) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        name=template.name,
        body=template.body,
        team_id=template.team_id,
        team_name=teams.get(template.team_id) if template.team_id else None,
        updated_at=template.updated_at,
    )


async def _team_names(ctx: Ctx) -> dict[uuid.UUID, str]:
    return {t.id: t.name for t in (await ctx.session.scalars(select(Team))).all()}


async def list_templates(ctx: Ctx) -> list[TemplateOut]:
    manage = ctx.can(P.TEMPLATE_MANAGE)
    if not (manage or ctx.can(P.TICKET_REPLY) or ctx.can(P.TICKET_NOTE)):
        raise PermissionDenied(P.TICKET_REPLY.value, "template")
    stmt = select(ReplyTemplate).order_by(ReplyTemplate.name)
    if not (manage or ctx.can(P.TICKET_READ_ALL)):
        stmt = stmt.where(
            or_(ReplyTemplate.team_id.is_(None), ReplyTemplate.team_id.in_(ctx.team_ids))
        )
    rows = (await ctx.session.scalars(stmt.limit(MAX_TEMPLATES))).all()
    teams = await _team_names(ctx)
    return [_out(t, teams) for t in rows]


async def _check_team(ctx: Ctx, team_id: uuid.UUID | None) -> None:
    if team_id is not None and await ctx.session.get(Team, team_id) is None:
        raise ValidationFailed("unknown team")


async def create_template(ctx: Ctx, data: TemplateIn) -> TemplateOut:
    ctx.require(P.TEMPLATE_MANAGE, "template")
    await _check_team(ctx, data.team_id)
    template = ReplyTemplate(
        name=data.name, body=data.body, team_id=data.team_id, updated_by=ctx.principal.subject
    )
    ctx.session.add(template)
    await ctx.session.flush()
    ctx.audit("template.created", "template", template.id)
    return _out(template, await _team_names(ctx))


async def update_template(ctx: Ctx, template_id: uuid.UUID, data: TemplateUpdate) -> TemplateOut:
    ctx.require(P.TEMPLATE_MANAGE, "template", str(template_id))
    template = await ctx.session.get(ReplyTemplate, template_id)
    if template is None:
        raise NotFound("template")
    fields = data.model_fields_set
    if "name" in fields and data.name is not None:
        template.name = data.name
    if "body" in fields and data.body is not None:
        template.body = data.body
    if "team_id" in fields:
        await _check_team(ctx, data.team_id)
        template.team_id = data.team_id
    template.updated_by = ctx.principal.subject
    template.updated_at = utcnow()
    ctx.audit("template.updated", "template", template.id, fields=sorted(fields))
    return _out(template, await _team_names(ctx))


async def delete_template(ctx: Ctx, template_id: uuid.UUID) -> None:
    ctx.require(P.TEMPLATE_MANAGE, "template", str(template_id))
    template = await ctx.session.get(ReplyTemplate, template_id)
    if template is None:
        raise NotFound("template")
    await ctx.session.delete(template)
    ctx.audit("template.deleted", "template", template_id)
