"""Request-scoped unit of work.

A ``Ctx`` carries *who* is acting (``principal``), *what they may see*
(``team_ids``), and the open database transaction. Every service function takes
a ``Ctx`` and performs its own authorisation through it, so the REST API, the
MCP server and background workers all pass through exactly the same checks.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zentral.audit.trail import AuditTrail, PendingEvent, RequestMeta
from zentral.config import Settings
from zentral.db.models import Team, TeamMember, User, utcnow
from zentral.db.session import SessionFactory
from zentral.security.principal import Principal
from zentral.security.rbac import Permission, Role
from zentral.services.errors import PermissionDenied

_USER_TOUCH_SECONDS = 3600


@dataclass(slots=True)
class Ctx:
    session: AsyncSession
    principal: Principal
    settings: Settings
    trail: AuditTrail
    meta: RequestMeta
    team_ids: frozenset[uuid.UUID] = frozenset()
    _events: list[PendingEvent] = field(default_factory=list)

    @property
    def is_ai_agent(self) -> bool:
        """An AI agent is acting: anything over MCP, or an identity with the automation role.

        People who object to automated processing are hidden from these callers.
        """
        return self.meta.channel == "mcp" or Role.AUTOMATION.value in self.principal.roles

    def can(self, permission: Permission) -> bool:
        return self.principal.can(permission)

    def require(
        self, permission: Permission, resource_type: str = "", resource_id: str = ""
    ) -> None:
        if not self.principal.can(permission):
            raise PermissionDenied(permission.value, resource_type, resource_id)

    def audit(
        self,
        action: str,
        resource_type: str,
        resource_id: object = "",
        *,
        outcome: str = "success",
        **details: Any,
    ) -> None:
        self._events.append(PendingEvent(action, resource_type, str(resource_id), outcome, details))

    async def commit(self) -> None:
        """Commit the transaction together with its audit events."""
        if not self._events:
            # Nothing to chain: do not hold the chain lock (on SQLite an in-process
            # lock) while this transaction's own writes block other writers.
            await self.session.commit()
            return
        async with self.trail.serialized(self.session):
            await self.trail.write(self.session, self._events, self.principal, self.meta)
            self._events.clear()
            await self.session.commit()


async def resolve_team_ids(session: AsyncSession, principal: Principal) -> frozenset[uuid.UUID]:
    """Teams the principal belongs to: explicit membership plus mapped Entra groups."""
    ids = set(
        (
            await session.scalars(
                select(TeamMember.team_id).where(TeamMember.user_id == principal.subject)
            )
        ).all()
    )
    if principal.groups:
        ids |= set(
            (
                await session.scalars(
                    select(Team.id).where(Team.entra_group_id.in_(principal.groups))
                )
            ).all()
        )
    return frozenset(ids)


async def touch_user(session: AsyncSession, principal: Principal) -> None:
    """Mirror the caller's directory attributes so they can be shown as assignee/author."""
    if principal.kind in ("system", "requester"):
        return
    user = await session.get(User, principal.subject)
    now = utcnow()
    if user is None:
        session.add(
            User(
                id=principal.subject,
                kind=principal.kind,
                display_name=principal.display_name[:256],
                email=principal.email[:320],
                last_seen_at=now,
            )
        )
    elif (
        user.display_name != principal.display_name[:256]
        or user.email != principal.email[:320]
        or (now - user.last_seen_at).total_seconds() > _USER_TOUCH_SECONDS
    ):
        user.display_name = principal.display_name[:256]
        user.email = principal.email[:320]
        user.last_seen_at = now


async def record_denied(
    factory: SessionFactory,
    trail: AuditTrail,
    settings: Settings,
    principal: Principal,
    meta: RequestMeta,
    exc: PermissionDenied,
) -> None:
    """Record a denied attempt in its own transaction (the original one is rolled back)."""
    async with factory() as session:
        ctx = Ctx(session=session, principal=principal, settings=settings, trail=trail, meta=meta)
        ctx.audit(
            "access.denied",
            exc.resource_type or "permission",
            exc.resource_id,
            outcome="denied",
            permission=exc.permission,
        )
        await ctx.commit()
