"""Administration: teams, ingest sources, automation rules."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from zentral.connectors.base import Capability
from zentral.connectors.registry import ConnectorRegistry
from zentral.db.models import AutomationRule, IngestSource, Team, TeamMember, User
from zentral.schemas import (
    RuleIn,
    RuleOut,
    SourceCreated,
    SourceIn,
    SourceOut,
    SourceUpdate,
    TeamIn,
    TeamOut,
    UserOut,
)
from zentral.security.crypto import new_ingest_token
from zentral.security.rbac import Permission as P
from zentral.services.context import Ctx
from zentral.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

# --- teams & users ---------------------------------------------------------------


async def _team_out(ctx: Ctx, team: Team, *, with_members: bool) -> TeamOut:
    members: list[str] = []
    if with_members:
        members = list(
            (
                await ctx.session.scalars(
                    select(TeamMember.user_id).where(TeamMember.team_id == team.id)
                )
            ).all()
        )
    return TeamOut(
        id=team.id, name=team.name, entra_group_id=team.entra_group_id, member_ids=sorted(members)
    )


async def list_teams(ctx: Ctx) -> list[TeamOut]:
    """Team names are needed for routing by anyone who can see tickets; membership is admin-only."""
    if not (ctx.can(P.TICKET_READ) or ctx.can(P.TEAM_MANAGE)):
        raise PermissionDenied(P.TICKET_READ.value, "team")
    manage = ctx.can(P.TEAM_MANAGE)
    teams = (await ctx.session.scalars(select(Team).order_by(Team.name))).all()
    out = [await _team_out(ctx, t, with_members=manage) for t in teams]
    if not manage:
        for team in out:
            team.entra_group_id = None
    return out


async def create_team(ctx: Ctx, data: TeamIn) -> TeamOut:
    ctx.require(P.TEAM_MANAGE, "team")
    team = Team(name=data.name, entra_group_id=data.entra_group_id)
    ctx.session.add(team)
    try:
        await ctx.session.flush()
    except IntegrityError as exc:
        raise Conflict("a team with this name or group already exists") from exc
    ctx.audit("team.created", "team", team.id)
    return await _team_out(ctx, team, with_members=True)


async def delete_team(ctx: Ctx, team_id: uuid.UUID) -> None:
    ctx.require(P.TEAM_MANAGE, "team", str(team_id))
    team = await ctx.session.get(Team, team_id)
    if team is None:
        raise NotFound("team")
    await ctx.session.delete(team)
    ctx.audit("team.deleted", "team", team_id)


async def add_member(ctx: Ctx, team_id: uuid.UUID, user_id: str) -> TeamOut:
    ctx.require(P.TEAM_MANAGE, "team", str(team_id))
    team = await ctx.session.get(Team, team_id)
    if team is None:
        raise NotFound("team")
    if await ctx.session.get(TeamMember, (team_id, user_id)) is None:
        ctx.session.add(TeamMember(team_id=team_id, user_id=user_id))
        await ctx.session.flush()
        ctx.audit("team.member_added", "team", team_id, user_id=user_id)
    return await _team_out(ctx, team, with_members=True)


async def remove_member(ctx: Ctx, team_id: uuid.UUID, user_id: str) -> None:
    ctx.require(P.TEAM_MANAGE, "team", str(team_id))
    await ctx.session.execute(
        delete(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    )
    ctx.audit("team.member_removed", "team", team_id, user_id=user_id)


async def list_users(ctx: Ctx) -> list[UserOut]:
    if not (ctx.can(P.TICKET_ASSIGN) or ctx.can(P.TEAM_MANAGE)):
        raise PermissionDenied(P.TICKET_ASSIGN.value, "user")
    users = (await ctx.session.scalars(select(User).order_by(User.display_name))).all()
    return [UserOut.model_validate(u) for u in users]


# --- ingest sources ---------------------------------------------------------------


def _source_out(ctx: Ctx, source: IngestSource, registry: ConnectorRegistry) -> SourceOut:
    klass = registry.get(source.connector)
    hook = None
    if klass and Capability.WEBHOOK in klass.capabilities:
        hook = f"{ctx.settings.public_base_url.rstrip('/')}/api/v1/hooks/{source.id}"
    return SourceOut(
        id=source.id,
        name=source.name,
        connector=source.connector,
        config=source.config,
        default_team_id=source.default_team_id,
        enabled=source.enabled,
        has_secrets=bool(source.secrets),  # secrets are write-only: never returned
        has_token=source.token_hash is not None,
        hook_url=hook,
        created_at=source.created_at,
    )


def _validate_source(
    registry: ConnectorRegistry, connector: str, config: dict[str, Any], secrets: dict[str, str]
) -> dict[str, Any]:
    klass = registry.get(connector)
    if klass is None:
        raise ValidationFailed(f"unknown or disabled connector '{connector}'")
    try:
        validated = klass.config_model.model_validate(config)
    except ValidationError as exc:
        raise ValidationFailed(f"invalid connector config: {exc.error_count()} error(s)") from exc
    unknown = set(secrets) - klass.secret_fields
    if unknown:
        raise ValidationFailed(f"unknown secret field(s): {', '.join(sorted(unknown))}")
    missing = klass.required_secrets - {k for k, v in secrets.items() if v}
    if missing:
        raise ValidationFailed(f"missing secret(s): {', '.join(sorted(missing))}")
    return validated.model_dump(mode="json")


async def list_sources(ctx: Ctx, registry: ConnectorRegistry) -> list[SourceOut]:
    ctx.require(P.SOURCE_MANAGE, "source")
    sources = (await ctx.session.scalars(select(IngestSource).order_by(IngestSource.name))).all()
    return [_source_out(ctx, s, registry) for s in sources]


async def create_source(ctx: Ctx, registry: ConnectorRegistry, data: SourceIn) -> SourceCreated:
    ctx.require(P.SOURCE_MANAGE, "source")
    config = _validate_source(registry, data.connector, data.config, data.secrets)
    klass = registry.get(data.connector)
    assert klass is not None  # noqa: S101 - established by _validate_source
    token, token_hash = new_ingest_token() if klass.uses_ingest_token else (None, None)
    source = IngestSource(
        name=data.name,
        connector=data.connector,
        config=config,
        secrets=dict(data.secrets) or None,
        token_hash=token_hash,
        default_team_id=data.default_team_id,
        enabled=data.enabled,
    )
    ctx.session.add(source)
    try:
        await ctx.session.flush()
    except IntegrityError as exc:
        raise Conflict("a source with this name already exists") from exc
    ctx.audit("source.created", "source", source.id, connector=source.connector)
    return SourceCreated(
        **_source_out(ctx, source, registry).model_dump(), ingest_token=token
    )


async def _load_source(ctx: Ctx, source_id: uuid.UUID) -> IngestSource:
    ctx.require(P.SOURCE_MANAGE, "source", str(source_id))
    source = await ctx.session.get(IngestSource, source_id)
    if source is None:
        raise NotFound("source")
    return source


async def update_source(
    ctx: Ctx, registry: ConnectorRegistry, source_id: uuid.UUID, data: SourceUpdate
) -> SourceOut:
    source = await _load_source(ctx, source_id)
    fields = data.model_fields_set
    config = data.config if "config" in fields and data.config is not None else source.config
    secrets = (
        data.secrets if "secrets" in fields and data.secrets is not None else source.secrets or {}
    )
    source.config = _validate_source(registry, source.connector, config, secrets)
    if "secrets" in fields and data.secrets is not None:
        source.secrets = dict(data.secrets) or None
    if "default_team_id" in fields:
        source.default_team_id = data.default_team_id
    if "enabled" in fields and data.enabled is not None:
        source.enabled = data.enabled
    ctx.audit("source.updated", "source", source.id, fields=sorted(fields))
    return _source_out(ctx, source, registry)


async def rotate_source_token(
    ctx: Ctx, registry: ConnectorRegistry, source_id: uuid.UUID
) -> SourceCreated:
    source = await _load_source(ctx, source_id)
    klass = registry.get(source.connector)
    if klass is None or not klass.uses_ingest_token:
        raise ValidationFailed("this connector does not use an ingest token")
    token, source.token_hash = new_ingest_token()
    ctx.audit("source.token_rotated", "source", source.id)
    return SourceCreated(**_source_out(ctx, source, registry).model_dump(), ingest_token=token)


async def delete_source(ctx: Ctx, source_id: uuid.UUID) -> None:
    source = await _load_source(ctx, source_id)
    await ctx.session.delete(source)
    ctx.audit("source.deleted", "source", source_id)


# --- automation rules ---------------------------------------------------------------


def _rule_payload(ctx: Ctx, data: RuleIn) -> tuple[dict[str, Any], dict[str, Any]]:
    if data.conditions.category and data.conditions.category not in ctx.settings.categories:
        raise ValidationFailed("unknown category in rule conditions")
    return (
        data.conditions.model_dump(mode="json", exclude_defaults=True),
        data.actions.model_dump(mode="json", exclude_defaults=True),
    )


async def list_rules(ctx: Ctx) -> list[RuleOut]:
    ctx.require(P.RULE_MANAGE, "rule")
    rules = (
        await ctx.session.scalars(select(AutomationRule).order_by(AutomationRule.position))
    ).all()
    return [RuleOut.model_validate(r) for r in rules]


async def create_rule(ctx: Ctx, data: RuleIn) -> RuleOut:
    ctx.require(P.RULE_MANAGE, "rule")
    conditions, actions = _rule_payload(ctx, data)
    rule = AutomationRule(
        name=data.name,
        position=data.position,
        enabled=data.enabled,
        conditions=conditions,
        actions=actions,
    )
    ctx.session.add(rule)
    await ctx.session.flush()
    ctx.audit("rule.created", "rule", rule.id)
    return RuleOut.model_validate(rule)


async def update_rule(ctx: Ctx, rule_id: uuid.UUID, data: RuleIn) -> RuleOut:
    ctx.require(P.RULE_MANAGE, "rule", str(rule_id))
    rule = await ctx.session.get(AutomationRule, rule_id)
    if rule is None:
        raise NotFound("rule")
    rule.conditions, rule.actions = _rule_payload(ctx, data)
    rule.name, rule.position, rule.enabled = data.name, data.position, data.enabled
    ctx.audit("rule.updated", "rule", rule.id)
    return RuleOut.model_validate(rule)


async def delete_rule(ctx: Ctx, rule_id: uuid.UUID) -> None:
    ctx.require(P.RULE_MANAGE, "rule", str(rule_id))
    rule = await ctx.session.get(AutomationRule, rule_id)
    if rule is None:
        raise NotFound("rule")
    await ctx.session.delete(rule)
    ctx.audit("rule.deleted", "rule", rule_id)
