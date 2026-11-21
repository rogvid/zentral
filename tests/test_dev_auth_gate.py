"""Dev sign-in on a reachable server: only behind a password, and only that way in."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import make_settings
from zentral.api.routes.devidp import build_dev_idp
from zentral.api.routes.meta import sign_in_origins
from zentral.config import ConfigurationError, Settings
from zentral.security.devauth import LocalIssuer

PASSWORD = "correct-horse-battery-staple-0123456789"
ORIGIN = "https://zentral.example"
CHALLENGE = "x" * 43
AUTHORIZE = "/dev-idp/authorize"


def production(**overrides: Any) -> Settings:
    return make_settings(
        **{
            "env": "production",
            "auth_mode": "dev",
            "database_url": "postgresql+asyncpg://z:z@db/z",
            "public_base_url": ORIGIN,
            "allowed_hosts": ["zentral.example"],
            "allow_env_keys": True,
            "dev_auth_password": PASSWORD,
            **overrides,
        }
    )


def basic(password: str, user: str = "tester") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def query(**extra: str) -> dict[str, str]:
    return {"redirect_uri": f"{ORIGIN}/", "code_challenge": CHALLENGE, **extra}


async def idp(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(build_dev_idp(LocalIssuer(issuer=settings.issuer), settings))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        yield client


@pytest.fixture
async def gated() -> AsyncIterator[httpx.AsyncClient]:
    async for client in idp(production()):
        yield client


@pytest.mark.parametrize("password", ["", "too-short"])
def test_production_refuses_dev_auth_without_a_strong_password(password):
    with pytest.raises(ConfigurationError, match="ZENTRAL_DEV_AUTH_PASSWORD"):
        production(dev_auth_password=password).validate_for_runtime()


def test_production_accepts_dev_auth_with_a_strong_password():
    production().validate_for_runtime()


async def test_picker_asks_for_the_password(gated):
    response = await gated.get(AUTHORIZE, params=query())
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert "Ada Admin" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        basic("wrong-password"),
        basic(""),
        {"Authorization": "Basic not-base64!"},
        {"Authorization": f"Bearer {PASSWORD}"},
    ],
)
async def test_wrong_credentials_get_no_code(gated, headers):
    response = await gated.get(
        AUTHORIZE, params=query(persona="admin"), headers=headers, follow_redirects=False
    )
    assert response.status_code == 401
    assert "location" not in response.headers


async def test_right_password_signs_in(gated):
    picker = await gated.get(AUTHORIZE, params=query(), headers=basic(PASSWORD))
    assert picker.status_code == 200 and "Ada Admin" in picker.text
    assert "Never expose" not in picker.text
    code = await gated.get(
        AUTHORIZE,
        params=query(persona="admin"),
        headers=basic(PASSWORD),
        follow_redirects=False,
    )
    assert code.status_code == 302 and code.headers["location"].startswith(f"{ORIGIN}/?code=")


async def test_production_has_no_direct_token_route(gated):
    response = await gated.post("/api/v1/dev/token", json={"persona": "admin"})
    assert response.status_code == 404


async def test_production_sends_codes_only_to_its_own_origin(gated):
    response = await gated.get(
        AUTHORIZE,
        params=query(redirect_uri="http://localhost:9999/", persona="admin"),
        headers=basic(PASSWORD),
        follow_redirects=False,
    )
    assert response.status_code == 400


async def test_local_development_stays_open():
    settings = make_settings(public_base_url="http://localhost:8000")
    async for client in idp(settings):
        picker = await client.get(AUTHORIZE, params=query(redirect_uri="http://localhost:5173/"))
        assert picker.status_code == 200
        token = await client.post("/api/v1/dev/token", json={"persona": "admin"})
        assert token.status_code == 200


def test_browser_may_report_errors_under_dev_auth():
    extra = "https://o1.ingest.de.sentry.io"
    assert sign_in_origins(production(csp_connect_extra=[extra])) == [extra]
