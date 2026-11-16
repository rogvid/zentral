"""Inbound webhooks from external systems.

This is the only write endpoint that does not require an Entra token: the
caller is a third-party system. Authentication is delegated to the source's
connector (HMAC signature or ingest token), and every failure mode — unknown
source, disabled source, bad credentials — produces the same bare 401.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from zentral.api.deps import RuntimeDep, client_ip, request_meta
from zentral.connectors.base import (
    Capability,
    WebhookAuthError,
    WebhookPayloadError,
    WebhookRequest,
)
from zentral.db.models import IngestSource
from zentral.services.ingest import ingest

router = APIRouter(tags=["ingest"])
log = structlog.get_logger(__name__)

_UNAUTHORIZED = HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")


def _substitute(value: Any, keys: str) -> Any:
    if isinstance(value, str):
        return value.replace("{ticket_keys}", keys)
    if isinstance(value, dict):
        return {k: _substitute(v, keys) for k, v in value.items()}
    return value


@router.post("/hooks/{source_id}")
async def receive_hook(source_id: str, request: Request, rt: RuntimeDep) -> JSONResponse:
    ip = client_ip(request)
    if not request.app.state.limiter.allow(
        f"hook:{source_id[:36]}:{ip}", rt.settings.hook_rate_limit_per_minute
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    body = await request.body()
    try:
        parsed_id = uuid.UUID(source_id)
    except ValueError:
        raise _UNAUTHORIZED from None

    async with rt.sessions() as session:
        source = await session.get(IngestSource, parsed_id)
        connector = rt.connector_for(source) if source is not None and source.enabled else None
        if source is None or connector is None or Capability.WEBHOOK not in connector.capabilities:
            raise _UNAUTHORIZED
        try:
            result = await connector.handle_webhook(
                WebhookRequest(
                    headers={k.lower(): v for k, v in request.headers.items()},
                    body=body,
                    query=dict(request.query_params),
                )
            )
        except WebhookAuthError:
            log.warning("hook_auth_failed", source_id=str(parsed_id), ip=ip)
            raise _UNAUTHORIZED from None
        except WebhookPayloadError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid payload") from exc

        ctx = rt.system_ctx(session, f"connector:{source.connector}", channel="hook")
        ctx.meta = request_meta(request, channel="hook")
        keys = [(await ingest(ctx, source, message)).ticket_key for message in result.messages]
        await ctx.commit()

    return JSONResponse(
        _substitute(result.response or {"accepted": len(keys)}, ", ".join(keys)),
        status_code=result.status_code,
    )
