from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from zentral.api.deps import CtxDep, RuntimeDep
from zentral.schemas import MeOut

router = APIRouter(tags=["meta"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(rt: RuntimeDep, response: Response) -> dict[str, str]:
    try:
        async with rt.sessions() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database unavailable"}
    return {"status": "ready"}


@router.get("/api/v1/config")
async def public_config(request: Request, rt: RuntimeDep) -> dict[str, Any]:
    """Non-secret bootstrap information for the web client."""
    s = rt.settings
    return {
        "auth_mode": s.auth_mode,
        "tenant_id": s.entra_tenant_id,
        "authority": f"{s.entra_authority_host}/{s.entra_tenant_id}",
        "spa_client_id": s.entra_spa_client_id,
        "api_scope": f"api://{s.entra_api_client_id}/{s.entra_required_scope}",
        "ticket_prefix": s.ticket_prefix,
        "categories": s.categories,
        "dev_personas": sorted(request.app.state.dev_personas),
    }


@router.get("/api/v1/me")
async def me(ctx: CtxDep) -> MeOut:
    await ctx.commit()  # persists the user mirror on first sign-in
    p = ctx.principal
    return MeOut(
        subject=p.subject,
        kind=p.kind,
        display_name=p.display_name,
        email=p.email,
        roles=sorted(p.roles),
        permissions=sorted(perm.value for perm in p.permissions),
        team_ids=sorted(ctx.team_ids),
    )
