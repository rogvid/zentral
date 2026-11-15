"""Process-wide wiring shared by the API server, the worker and the CLI."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from zentral.ai.base import AIProvider
from zentral.ai.service import load_provider
from zentral.audit.trail import AuditTrail, RequestMeta
from zentral.config import Settings
from zentral.connectors.registry import ConnectorRegistry
from zentral.db.session import SessionFactory, create_engine, create_session_factory
from zentral.security import crypto
from zentral.security.principal import Principal, system_principal
from zentral.services.context import Ctx


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: AsyncEngine
    sessions: SessionFactory
    trail: AuditTrail
    registry: ConnectorRegistry
    http: httpx.AsyncClient
    ai: AIProvider

    def system_ctx(self, session, component: str, channel: str = "worker") -> Ctx:  # type: ignore[no-untyped-def]
        principal: Principal = system_principal(component)
        return Ctx(
            session=session,
            principal=principal,
            settings=self.settings,
            trail=self.trail,
            meta=RequestMeta(channel=channel),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


def build_runtime(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    ai: AIProvider | None = None,
    engine: AsyncEngine | None = None,
) -> Runtime:
    settings.validate_for_runtime()
    crypto.configure(settings)
    engine = engine or create_engine(settings)
    # One shared outbound client: explicit timeouts, no redirects, TLS verification on.
    http = http or httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=False,
        headers={"User-Agent": "zentral"},
    )
    return Runtime(
        settings=settings,
        engine=engine,
        sessions=create_session_factory(engine),
        trail=AuditTrail(settings.audit_hmac_key.get_secret_value()),
        registry=ConnectorRegistry.load(settings),
        http=http,
        ai=ai or load_provider(settings, http),
    )
