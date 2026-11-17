"""Requester portal API: ``/api/v1/portal``.

Separate from the staff API on purpose: different identity (a one-time e-mail
link instead of the identity provider), different credential (an HttpOnly
cookie scoped to this path instead of a bearer token) and a much smaller
surface. Every request must carry ``X-Zentral-Portal: 1``: a browser cannot
send a custom header cross-site without a CORS pre-flight, which Zentral never
grants, so this (with SameSite=Strict) stops cross-site request forgery.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from zentral.api.deps import client_ip, get_runtime, request_meta
from zentral.api.ratelimit import RateLimiter
from zentral.api.routes.tickets import file_response, read_upload
from zentral.db.models import Contact, System
from zentral.runtime import Runtime
from zentral.schemas import (
    PortalLinkRequest,
    PortalMessageOut,
    PortalProfile,
    PortalReplyIn,
    PortalSessionRequest,
    PortalTicketDetail,
    PortalTicketIn,
    PortalTicketOut,
    RatingIn,
)
from zentral.security.principal import requester_principal, system_principal
from zentral.services import attachments, portal
from zentral.services.context import Ctx

router = APIRouter(prefix="/portal", tags=["portal"])

CSRF_HEADER = "x-zentral-portal"
COOKIE_PATH = "/api/v1/portal"


def cookie_name(runtime: Runtime) -> str:
    # The __Secure- prefix makes browsers refuse the cookie over plain HTTP.
    secure = runtime.settings.public_base_url.startswith("https://")
    return "__Secure-zentral-portal" if secure else "zentral-portal"


def _guard(request: Request) -> Runtime:
    runtime = get_runtime(request)
    if not runtime.settings.portal_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing portal header")
    return runtime


def _throttle(request: Request, runtime: Runtime, bucket: str, limit: int) -> None:
    limiter: RateLimiter = request.app.state.limiter
    if not limiter.allow(f"portal:{bucket}:{client_ip(request)}", limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests; wait a minute")


GuardDep = Annotated[Runtime, Depends(_guard)]


@dataclass(slots=True)
class PortalSession:
    ctx: Ctx
    contact: Contact
    token: str


async def _anonymous_ctx(request: Request, runtime: GuardDep) -> AsyncIterator[Ctx]:
    async with runtime.sessions() as session:
        yield Ctx(
            session=session,
            principal=system_principal("portal"),
            settings=runtime.settings,
            trail=runtime.trail,
            meta=request_meta(request, channel="portal"),
        )


async def _signed_in(request: Request, runtime: GuardDep) -> AsyncIterator[PortalSession]:
    token = request.cookies.get(cookie_name(runtime), "")
    _throttle(request, runtime, "use", runtime.settings.rate_limit_per_minute)
    if not token or len(token) > 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in again")
    async with runtime.sessions() as session:
        probe = Ctx(
            session=session,
            principal=system_principal("portal"),
            settings=runtime.settings,
            trail=runtime.trail,
            meta=request_meta(request, channel="portal"),
        )
        contact = await portal.session_contact(probe, token)
        if contact is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in again")
        ctx = Ctx(
            session=session,
            principal=requester_principal(contact.id),
            settings=runtime.settings,
            trail=runtime.trail,
            meta=request_meta(request, channel="portal"),
        )
        yield PortalSession(ctx, contact, token)


AnonDep = Annotated[Ctx, Depends(_anonymous_ctx)]
SessionDep = Annotated[PortalSession, Depends(_signed_in)]


@router.post("/link", status_code=status.HTTP_202_ACCEPTED)
async def request_link(
    request: Request, runtime: GuardDep, ctx: AnonDep, data: PortalLinkRequest
) -> dict[str, str]:
    _throttle(request, runtime, "link", 10)
    await portal.request_link(ctx, data.email)
    await ctx.commit()
    # The same answer whether or not the address is known.
    return {
        "detail": "If this address has contacted the service desk, a sign-in link is on its way."
    }


@router.post("/session")
async def start_session(
    request: Request,
    response: Response,
    runtime: GuardDep,
    ctx: AnonDep,
    data: PortalSessionRequest,
) -> PortalProfile:
    _throttle(request, runtime, "session", 20)
    result = await portal.sign_in(ctx, data.token)
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this link is invalid or has expired")
    token, contact = result
    ctx.principal = requester_principal(contact.id)
    await ctx.commit()
    response.set_cookie(
        cookie_name(runtime),
        token,
        max_age=runtime.settings.portal_session_hours * 3600,
        path=COOKIE_PATH,
        secure=runtime.settings.public_base_url.startswith("https://"),
        httponly=True,
        samesite="strict",
    )
    return portal.profile(contact)


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(response: Response, runtime: GuardDep, s: SessionDep) -> None:
    await portal.sign_out(s.ctx, s.token)
    await s.ctx.commit()
    response.delete_cookie(cookie_name(runtime), path=COOKIE_PATH)


@router.get("/me")
async def me(s: SessionDep) -> PortalProfile:
    return portal.profile(s.contact)


@router.get("/systems")
async def systems(s: SessionDep) -> list[dict[str, Any]]:
    """Names of the systems a request can be about — nothing else about them."""
    rows = await s.ctx.session.scalars(
        select(System).where(System.active.is_(True)).order_by(System.name)
    )
    return [{"id": str(r.id), "name": r.name} for r in rows.all()]


@router.get("/tickets")
async def list_tickets(s: SessionDep) -> list[PortalTicketOut]:
    out = await portal.list_tickets(s.ctx, s.contact)
    await s.ctx.commit()
    return out


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket(
    request: Request, runtime: GuardDep, s: SessionDep, data: PortalTicketIn
) -> PortalTicketOut:
    _throttle(request, runtime, "create", 10)
    out = await portal.create_ticket(s.ctx, s.contact, data)
    await s.ctx.commit()
    return out


@router.get("/tickets/{ticket_id}")
async def get_ticket(s: SessionDep, ticket_id: int) -> PortalTicketDetail:
    out = await portal.get_ticket(s.ctx, s.contact, ticket_id)
    await s.ctx.commit()
    return out


@router.post("/tickets/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
async def add_message(
    request: Request, runtime: GuardDep, s: SessionDep, ticket_id: int, data: PortalReplyIn
) -> PortalMessageOut:
    _throttle(request, runtime, "reply", 30)
    out = await portal.add_message(s.ctx, s.contact, ticket_id, data.body)
    await s.ctx.commit()
    return out


@router.post("/tickets/{ticket_id}/rating", status_code=status.HTTP_201_CREATED)
async def rate(s: SessionDep, ticket_id: int, data: RatingIn) -> PortalTicketOut:
    out = await portal.rate(s.ctx, s.contact, ticket_id, data)
    await s.ctx.commit()
    return out


@router.post("/tickets/{ticket_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    request: Request,
    runtime: GuardDep,
    s: SessionDep,
    ticket_id: int,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> PortalMessageOut:
    """Send a file (a screenshot, a log) on one of your requests."""
    _throttle(request, runtime, "upload", 20)
    data = await read_upload(request, s.ctx)
    out = await portal.add_file(s.ctx, s.contact, ticket_id, filename, data)
    await s.ctx.commit()
    return out


@router.get("/tickets/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(s: SessionDep, ticket_id: int, attachment_id: uuid.UUID) -> Response:
    attachment, data = await attachments.requester_download(
        s.ctx, s.contact.id, ticket_id, attachment_id
    )
    await s.ctx.commit()
    return file_response(attachment, data)
