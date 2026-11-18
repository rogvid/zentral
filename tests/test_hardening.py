from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from tests.conftest import make_settings
from zentral.api.ratelimit import RateLimiter
from zentral.config import ConfigurationError
from zentral.security.redaction import redact
from zentral.text import clean_text, html_to_text, single_line


async def test_security_headers(client, auth):
    r = await client.get("/api/v1/me", headers=auth("agent"))
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"
    assert "default-src 'self'" in h["content-security-policy"]
    assert "'unsafe-inline'" not in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert len(h["x-request-id"]) == 32
    assert "server" not in {k.lower() for k in h} or "uvicorn" not in h.get("server", "")


async def test_not_ready_until_schema_matches(client, db):
    from sqlalchemy import text

    assert (await client.get("/readyz")).status_code == 200
    await db.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    await db.commit()
    r = await client.get("/readyz")
    assert r.status_code == 503 and "schema" in r.json()["status"]
    assert (await client.get("/healthz")).status_code == 200  # alive, just not ready


async def test_client_cannot_choose_request_id(client, auth):
    r = await client.get("/healthz", headers={"X-Request-ID": "forged\nlog-line"})
    assert r.headers["x-request-id"] != "forged\nlog-line"


async def test_unknown_host_is_rejected(client):
    assert (await client.get("/healthz", headers={"Host": "evil.example"})).status_code == 400


async def test_request_body_limit(client, auth, settings):
    big = "x" * (settings.max_body_bytes + 1)
    r = await client.post(
        "/api/v1/tickets", headers=auth("agent"), json={"subject": "s", "body": big}
    )
    assert r.status_code == 413
    r = await client.post(
        "/api/v1/hooks/00000000-0000-4000-8000-000000000000", content=big.encode()
    )
    assert r.status_code == 413


async def test_no_cors_headers_by_default(client):
    r = await client.options(
        "/api/v1/tickets",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers


async def test_public_config_exposes_no_secrets(client, settings):
    body = (await client.get("/api/v1/config")).text
    for secret in (
        settings.audit_hmac_key.get_secret_value(),
        settings.blind_index_key.get_secret_value(),
        settings.encryption_keys[0].get_secret_value(),
    ):
        assert secret not in body


async def test_dev_token_endpoint_absent_unless_dev_issuer_is_active(client):
    # The test app is built with an explicit validator, like production.
    assert (await client.post("/api/v1/dev/token", json={"persona": "admin"})).status_code in (
        404,
        405,
    )
    assert (await client.get("/dev-idp/authorize")).status_code == 404
    assert (await client.get("/dev-idp/.well-known/openid-configuration")).status_code == 404


def test_rate_limiter():
    limiter = RateLimiter(max_keys=3)
    assert [limiter.allow("a", 2) for _ in range(3)] == [True, True, False]
    assert limiter.allow("b", 2)
    for key in "cdefg":  # memory stays bounded
        limiter.allow(key, 2)
    assert len(limiter._windows) <= 3


async def test_rate_limit_applies_per_principal(runtime, issuer, auth):
    import asyncio

    import httpx

    from zentral.api.app import create_app

    runtime.settings.rate_limit_per_minute = 3
    app = create_app(runtime.settings, runtime=runtime, validator=issuer.validator())
    ready, stop = asyncio.Event(), asyncio.Event()

    async def lifespan():
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await ready.wait()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        codes = [(await c.get("/api/v1/me", headers=auth("agent"))).status_code for _ in range(5)]
        other = (await c.get("/api/v1/me", headers=auth("admin"))).status_code
    stop.set()
    await task
    assert codes == [200, 200, 200, 429, 429] and other == 200


# --- configuration fails closed ---------------------------------------------------------


def prod(**overrides):
    base = {
        "env": "production",
        "auth_mode": "entra",
        "entra_tenant_id": "t",
        "entra_api_client_id": "c",
        "database_url": "postgresql+asyncpg://u@db/zentral",
        "public_base_url": "https://desk.corp.example",
        # Plain-text keys keep these tests about the other settings; key custody
        # has its own tests in test_key_custody.py.
        "allow_env_keys": True,
    }
    return make_settings(**{**base, **overrides})


def test_valid_production_config_passes():
    prod().validate_for_runtime()
    prod(
        auth_mode="oidc",
        oidc_issuer="https://idp.corp.example/realms/x",
        oidc_audiences=["zentral"],
    ).validate_for_runtime()
    assert prod().entra_audiences == ["c", "api://c"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_mode": "dev"},
        {"auth_mode": "oidc"},
        {
            "auth_mode": "oidc",
            "oidc_issuer": "http://idp.corp.example",
            "oidc_audiences": ["zentral"],
        },
        {"database_url": "sqlite+aiosqlite:///x.db"},
        {"cors_origins": ["*"]},
        {"public_base_url": "http://desk.corp.example"},
        {"encryption_keys": []},
        {"blind_index_key": "short"},
        {"audit_hmac_key": ""},
        {"entra_tenant_id": ""},
        {"ai_provider": "claude_cli"},
        {"ai_provider": "anthropic", "ai_redact_pii": False},
        {"allow_env_keys": False},
    ],
)
def test_unsafe_production_config_is_refused(overrides):
    with pytest.raises(ConfigurationError):
        prod(**overrides).validate_for_runtime()


def test_list_settings_accept_csv_and_json(monkeypatch):
    from zentral.config import Settings

    monkeypatch.setenv("ZENTRAL_CORS_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("ZENTRAL_CATEGORIES", '["x", "y"]')
    monkeypatch.setenv("ZENTRAL_ENCRYPTION_KEYS", "k1,k2")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["https://a.example", "https://b.example"]
    assert s.categories == ["x", "y"]
    assert [k.get_secret_value() for k in s.encryption_keys] == ["k1", "k2"]


def test_secrets_do_not_leak_through_repr():
    key = Fernet.generate_key().decode()
    settings = make_settings(encryption_keys=[key], ai_api_key="sk-very-secret")
    assert key not in repr(settings) and "sk-very-secret" not in repr(settings)


# --- text handling ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail jane.doe@example.com now", "mail [EMAIL] now"),
        ("call +45 12 34 56 78 today", "call [PHONE] today"),
        ("cpr 010190-1234", "cpr [NATIONAL_ID]"),
        ("card 4111 1111 1111 1111", "card [CARD]"),
        ("iban DK5000400440116243", "iban [IBAN]"),
        ("host 192.168.1.10 down", "host [IP] down"),
        ("password: Hunter2!", "password: [SECRET]"),
        ("my api_key=abc123", "my api_key=[SECRET]"),
        ("see https://intra.corp/u/jane?id=4", "see [URL]"),
        ("ticket ZEN-42 is fine, version 1.2.3", "ticket ZEN-42 is fine, version 1.2.3"),
    ],
)
def test_redaction(text, expected):
    assert redact(text) == expected


def test_html_is_reduced_to_text():
    html = '<p>Hello <b>World</b></p><script>steal()</script><style>p{}</style><img src=x onerror=alert(1)><a href="javascript:x">link</a>'
    assert html_to_text(html) == "Hello World\nlink"


def test_text_cleaning():
    assert clean_text("a\x00b‮c​") == "abc"
    assert clean_text("x" * 50, max_chars=10).startswith("x" * 10 + "\n[")
    assert single_line("  a\n\n b\t c ", 4) == "a b…"


async def test_repeated_bad_tokens_are_throttled_per_address(client, auth, runtime):
    runtime.settings.auth_failure_limit_per_minute = 3
    bad = {"Authorization": "Bearer not-a-token"}
    codes = [(await client.get("/api/v1/me", headers=bad)).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    # The block is on the address: even a valid token waits it out…
    assert (await client.get("/api/v1/me", headers=auth("agent"))).status_code == 429
    # …and successful requests never count towards it.
    runtime.settings.auth_failure_limit_per_minute = 1000
    for _ in range(5):
        assert (await client.get("/api/v1/me", headers=auth("agent"))).status_code == 200


def test_redaction_is_linear_on_hostile_input():
    """No catastrophic backtracking: 200 kB of pattern-bait must finish quickly."""
    import time

    bait = (
        ("1 " * 50_000)
        + ("a@" * 20_000)
        + ("DE00 " * 10_000)
        + ("+1-" * 20_000)
        + "password: " * 5_000
    )
    started = time.perf_counter()
    redact(bait)
    assert time.perf_counter() - started < 2.0
