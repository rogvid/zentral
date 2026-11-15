"""Shared fixtures.

Tests run the real application stack — real token validation (RS256 against a
local issuer), real encryption, real audit chain — on an in-memory SQLite
database. Only the network is replaced.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from zentral.api.app import create_app
from zentral.config import Settings
from zentral.db.models import Base
from zentral.runtime import Runtime, build_runtime
from zentral.security.devauth import LocalIssuer
from zentral.security.rbac import Role
from zentral.services.jobs import Worker


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "auth_mode": "dev",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "encryption_keys": [Fernet.generate_key().decode()],
        "blind_index_key": "b" * 40,
        "audit_hmac_key": "a" * 40,
        "public_base_url": "http://testserver",
        "allowed_hosts": ["testserver"],
        "rate_limit_per_minute": 10_000,
        "hook_rate_limit_per_minute": 10_000,
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


class OutboundRecorder:
    """Stands in for the internet: records requests, returns canned responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: list[tuple[str, Callable[[httpx.Request], httpx.Response]]] = []

    def on(self, url_part: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.routes.append((url_part, handler))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for part, handler in self.routes:
            if part in str(request.url):
                return handler(request)
        return httpx.Response(599, text="unexpected outbound request")


@pytest.fixture
def outbound() -> OutboundRecorder:
    return OutboundRecorder()


@pytest.fixture
async def runtime(settings: Settings, outbound: OutboundRecorder) -> AsyncIterator[Runtime]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(outbound))
    rt = build_runtime(settings, http=http)
    async with rt.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield rt
    await rt.aclose()


@pytest.fixture
def issuer() -> LocalIssuer:
    return LocalIssuer()


@pytest.fixture
async def client(runtime: Runtime, issuer: LocalIssuer) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(runtime.settings, runtime=runtime, validator=issuer.validator())
    ready, stop = asyncio.Event(), asyncio.Event()

    # The MCP session manager owns an anyio task group, which must be entered
    # and exited in the same task — so the lifespan gets a task of its own.
    async def lifespan() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await ready.wait()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    stop.set()
    await task


@pytest.fixture
def worker(runtime: Runtime) -> Worker:
    return Worker(runtime)


@pytest.fixture
async def db(runtime: Runtime) -> AsyncIterator[AsyncSession]:
    async with runtime.sessions() as session:
        yield session


SUBJECTS = {
    name: str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.test"))
    for name in ("admin", "agent", "agent2", "viewer", "auditor", "privacy", "bot", "ingest")
}
ROLES = {
    "admin": [Role.ADMIN],
    "agent": [Role.AGENT],
    "agent2": [Role.AGENT],
    "viewer": [Role.VIEWER],
    "auditor": [Role.AUDITOR],
    "privacy": [Role.PRIVACY_OFFICER],
    "bot": [Role.AUTOMATION],
    "ingest": [Role.INGEST],
}


@pytest.fixture
def auth(issuer: LocalIssuer) -> Callable[..., dict[str, str]]:
    def _auth(who: str, **claims: Any) -> dict[str, str]:
        token = issuer.issue(
            subject=SUBJECTS[who],
            roles=[str(r) for r in ROLES[who]],
            name=who.title(),
            email=f"{who}@corp.test",
            app=who in ("bot", "ingest"),
            **claims,
        )
        return {"Authorization": f"Bearer {token}"}

    return _auth
