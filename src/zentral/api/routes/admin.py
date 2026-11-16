from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status

from zentral.api.deps import CtxDep, RuntimeDep
from zentral.schemas import (
    RuleIn,
    RuleOut,
    SourceCreated,
    SourceIn,
    SourceOut,
    SourceUpdate,
    SystemIn,
    SystemOut,
    TeamIn,
    TeamMemberIn,
    TeamOut,
    UserOut,
)
from zentral.security.rbac import Permission
from zentral.services import admin

router = APIRouter(tags=["administration"])


@router.get("/teams")
async def list_teams(ctx: CtxDep) -> list[TeamOut]:
    return await admin.list_teams(ctx)


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(ctx: CtxDep, data: TeamIn) -> TeamOut:
    team = await admin.create_team(ctx, data)
    await ctx.commit()
    return team


@router.delete("/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(ctx: CtxDep, team_id: uuid.UUID) -> None:
    await admin.delete_team(ctx, team_id)
    await ctx.commit()


@router.post("/teams/{team_id}/members")
async def add_member(ctx: CtxDep, team_id: uuid.UUID, data: TeamMemberIn) -> TeamOut:
    team = await admin.add_member(ctx, team_id, data.user_id)
    await ctx.commit()
    return team


@router.delete("/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(ctx: CtxDep, team_id: uuid.UUID, user_id: str) -> None:
    await admin.remove_member(ctx, team_id, user_id)
    await ctx.commit()


@router.get("/systems")
async def list_systems(ctx: CtxDep, include_inactive: bool = False) -> list[SystemOut]:
    return await admin.list_systems(ctx, include_inactive=include_inactive)


@router.post("/systems", status_code=status.HTTP_201_CREATED)
async def create_system(ctx: CtxDep, data: SystemIn) -> SystemOut:
    system = await admin.create_system(ctx, data)
    await ctx.commit()
    return system


@router.put("/systems/{system_id}")
async def update_system(ctx: CtxDep, system_id: uuid.UUID, data: SystemIn) -> SystemOut:
    system = await admin.update_system(ctx, system_id, data)
    await ctx.commit()
    return system


@router.delete("/systems/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_system(ctx: CtxDep, system_id: uuid.UUID) -> None:
    await admin.delete_system(ctx, system_id)
    await ctx.commit()


@router.get("/users")
async def list_users(ctx: CtxDep) -> list[UserOut]:
    return await admin.list_users(ctx)


@router.get("/connectors")
async def list_connectors(ctx: CtxDep, rt: RuntimeDep) -> list[dict[str, Any]]:
    ctx.require(Permission.SOURCE_MANAGE, "connector")
    return rt.registry.describe()


@router.get("/sources")
async def list_sources(ctx: CtxDep, rt: RuntimeDep) -> list[SourceOut]:
    return await admin.list_sources(ctx, rt.registry)


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def create_source(ctx: CtxDep, rt: RuntimeDep, data: SourceIn) -> SourceCreated:
    source = await admin.create_source(ctx, rt.registry, data)
    await ctx.commit()
    return source


@router.patch("/sources/{source_id}")
async def update_source(
    ctx: CtxDep, rt: RuntimeDep, source_id: uuid.UUID, data: SourceUpdate
) -> SourceOut:
    source = await admin.update_source(ctx, rt.registry, source_id, data)
    await ctx.commit()
    return source


@router.post("/sources/{source_id}/rotate-token")
async def rotate_token(ctx: CtxDep, rt: RuntimeDep, source_id: uuid.UUID) -> SourceCreated:
    source = await admin.rotate_source_token(ctx, rt.registry, source_id)
    await ctx.commit()
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(ctx: CtxDep, source_id: uuid.UUID) -> None:
    await admin.delete_source(ctx, source_id)
    await ctx.commit()


@router.get("/rules")
async def list_rules(ctx: CtxDep) -> list[RuleOut]:
    return await admin.list_rules(ctx)


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_rule(ctx: CtxDep, data: RuleIn) -> RuleOut:
    rule = await admin.create_rule(ctx, data)
    await ctx.commit()
    return rule


@router.put("/rules/{rule_id}")
async def update_rule(ctx: CtxDep, rule_id: uuid.UUID, data: RuleIn) -> RuleOut:
    rule = await admin.update_rule(ctx, rule_id, data)
    await ctx.commit()
    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(ctx: CtxDep, rule_id: uuid.UUID) -> None:
    await admin.delete_rule(ctx, rule_id)
    await ctx.commit()
