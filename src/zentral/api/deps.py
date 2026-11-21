"""FastAPI dependencies: authentication and the request-scoped unit of work."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status

from zentral.api.ratelimit import RateLimiter
from zentral.audit.trail import RequestMeta
from zentral.runtime import Runtime
from zentral.security.principal import Principal
from zentral.security.tokens import (
    AuthenticationError,
    Authenticator,
    sign_in_failure_fields,
)
from zentral.services.context import Ctx, resolve_team_ids, touch_user

log = structlog.get_logger(__name__)


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def request_meta(request: Request, channel: str = "api") -> RequestMeta:
    return RequestMeta(
        channel=channel,
        request_id=getattr(request.state, "request_id", ""),
        ip=client_ip(request),
    )


def _unauthenticated() -> HTTPException:
    # One generic answer for every failure mode: no oracle for attackers.
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_principal(request: Request) -> Principal:
    validator: Authenticator = request.app.state.validator
    limiter: RateLimiter = request.app.state.limiter
    runtime = get_runtime(request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthenticated()
    ip = client_ip(request)
    # An address that keeps presenting bad tokens is slowed down before any
    # cryptography is done for it (token guessing, stolen-token replay, scanners).
    if limiter.blocked(f"authfail:{ip}", runtime.settings.auth_failure_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts")
    try:
        principal = await validator.validate(token.strip())
    except AuthenticationError as exc:
        limiter.allow(f"authfail:{ip}", runtime.settings.auth_failure_limit_per_minute)
        log.warning("authentication_failed", **sign_in_failure_fields(exc), ip=ip)
        raise _unauthenticated() from exc
    if not limiter.allow(f"p:{principal.subject}", runtime.settings.rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    # Kept for the error handlers, which audit denied attempts.
    request.state.principal = principal
    structlog.contextvars.bind_contextvars(actor_id=principal.subject)
    return principal


async def get_ctx(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> AsyncIterator[Ctx]:
    runtime = get_runtime(request)
    async with runtime.sessions() as session:
        await touch_user(session, principal)
        yield Ctx(
            session=session,
            principal=principal,
            settings=runtime.settings,
            trail=runtime.trail,
            meta=request_meta(request),
            team_ids=await resolve_team_ids(session, principal),
        )
        # No implicit commit: handlers call ``ctx.commit()`` so a failed commit
        # is reported to the client instead of being lost after the response.


CtxDep = Annotated[Ctx, Depends(get_ctx)]
RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
