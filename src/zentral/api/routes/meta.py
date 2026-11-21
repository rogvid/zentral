from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from zentral.api.deps import CtxDep, RuntimeDep
from zentral.api.routes.devidp import CLIENT_ID as DEV_CLIENT_ID
from zentral.config import Settings
from zentral.doctor import expected_revision
from zentral.schemas import MeOut

router = APIRouter(tags=["meta"])


@lru_cache
def _expected_revision() -> str:
    return expected_revision()


def sign_in_scopes(s: Settings) -> list[str]:
    """Scopes the web client requests when signing in."""
    if s.auth_mode == "entra":
        api_scope = f"api://{s.entra_api_client_id}/{s.entra_required_scope}"
        return ["openid", "profile", "offline_access", api_scope]
    if s.auth_mode == "oidc":
        return list(dict.fromkeys([*s.oidc_scopes, *filter(None, [s.oidc_required_scope])]))
    return ["openid"]


def sign_in_origins(s: Settings) -> list[str]:
    """Origins the browser must reach to sign in (for the Content-Security-Policy)."""
    if s.auth_mode == "dev":  # same origin; extras are e.g. an error-reporting endpoint
        return list(s.csp_connect_extra)
    parts = urlsplit(s.issuer)
    return list(dict.fromkeys([f"{parts.scheme}://{parts.netloc}", *s.csp_connect_extra]))


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(rt: RuntimeDep, response: Response) -> dict[str, str]:
    """Ready means: the database answers *and* its schema is the one this version expects.
    During a rollout, new pods therefore wait for the migration to finish."""
    try:
        async with rt.sessions() as session:
            current = await session.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database unavailable or not migrated"}
    if current != _expected_revision():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database schema does not match this version"}
    return {"status": "ready"}


@router.get("/api/v1/config")
async def public_config(rt: RuntimeDep) -> dict[str, Any]:
    """Non-secret bootstrap information for the web client."""
    s = rt.settings
    client_id = {
        "entra": s.entra_spa_client_id,
        "oidc": s.oidc_client_id,
        "dev": DEV_CLIENT_ID,
    }[s.auth_mode]
    return {
        # Everything the browser needs for OpenID Connect sign-in (code + PKCE).
        "auth": {
            "mode": s.auth_mode,
            "authority": s.issuer,
            "client_id": client_id,
            "scopes": sign_in_scopes(s),
        },
        "ticket_prefix": s.ticket_prefix,
        "categories": s.categories,
        "require_system_to_resolve": s.require_system_to_resolve,
        # Shown next to targets so people know which hours count.
        "business_hours": s.business_hours,
        "timezone": s.timezone,
        "attachments_enabled": s.attachments_enabled,
        "attachment_max_bytes": s.attachment_max_bytes,
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
