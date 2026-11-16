"""Token validation: every way a token can be wrong must be rejected."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest

from zentral.security.devauth import LocalIssuer
from zentral.security.rbac import Role
from zentral.security.tokens import AuthenticationError, JwksKeySource


@pytest.fixture
def validator(issuer):
    return issuer.validator()


def user_token(issuer: LocalIssuer, **overrides):
    return issuer.issue(subject="user-1", roles=[Role.AGENT], name="U", **overrides)


async def test_valid_user_token(issuer, validator):
    p = await validator.validate(user_token(issuer))
    assert (p.subject, p.kind) == ("user-1", "user")
    assert p.roles == {Role.AGENT}


async def test_valid_app_token(issuer, validator):
    p = await validator.validate(issuer.issue(subject="svc", roles=[Role.INGEST], app=True))
    assert p.kind == "service"


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifetime": -3600},  # expired
        {"aud": "api://some-other-api"},
        {"iss": "https://login.microsoftonline.com/other-tenant/v2.0"},
        {"tid": "other-tenant"},
        {"oid": None},
        {"scp": "something.else"},
        {"nbf": int(time.time()) + 3600},
        {"exp": None},
    ],
    ids=["expired", "audience", "issuer", "tenant", "no-oid", "scope", "not-yet-valid", "no-exp"],
)
async def test_rejects_bad_claims(issuer, validator, overrides):
    with pytest.raises(AuthenticationError):
        await validator.validate(user_token(issuer, **overrides))


async def test_rejects_app_token_without_roles(issuer, validator):
    with pytest.raises(AuthenticationError):
        await validator.validate(issuer.issue(subject="svc", roles=[], app=True))


async def test_rejects_token_signed_by_another_key(issuer, validator):
    attacker = LocalIssuer()
    attacker.kid = issuer.kid  # same key id, different key
    with pytest.raises(AuthenticationError):
        await validator.validate(user_token(attacker))


async def test_rejects_unknown_kid(validator):
    with pytest.raises(AuthenticationError):
        await validator.validate(user_token(LocalIssuer()))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def test_rejects_alg_none(issuer, validator):
    claims = jwt.decode(user_token(issuer), options={"verify_signature": False})
    header = {"alg": "none", "kid": issuer.kid, "typ": "JWT"}
    forged = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}."
    with pytest.raises(AuthenticationError):
        await validator.validate(forged)


async def test_rejects_hs256_key_confusion(issuer, validator):
    """Classic attack: sign with HS256 using the public key as the HMAC secret."""
    claims = jwt.decode(user_token(issuer), options={"verify_signature": False})
    header = {"alg": "HS256", "kid": issuer.kid, "typ": "JWT"}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    secret = json.dumps(issuer.jwks()["keys"][0]).encode()
    signature = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    with pytest.raises(AuthenticationError):
        await validator.validate(f"{signing_input}.{_b64(signature)}")


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer x"])
async def test_rejects_malformed(validator, token):
    with pytest.raises(AuthenticationError):
        await validator.validate(token)


async def test_api_requires_bearer_token(client, issuer):
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer nope"}):
        r = await client.get("/api/v1/tickets", headers=headers)
        assert r.status_code == 401
        assert r.json() == {"detail": "authentication required"}
        assert r.headers["www-authenticate"] == "Bearer"


async def test_every_api_route_requires_authentication(client):
    """Guard against someone adding an unauthenticated route by accident."""
    public = {"/api/v1/config", "/api/v1/hooks/{source_id}", "/api/openapi.json"}
    spec = (await client.get("/api/openapi.json")).json()
    checked = 0
    for path, methods in spec["paths"].items():
        if path in public or not path.startswith("/api/"):
            continue
        for method in methods:
            url = path.replace("{ticket_id}", "1")
            for name in ("team_id", "source_id", "rule_id", "contact_id"):
                url = url.replace(f"{{{name}}}", "00000000-0000-4000-8000-000000000000")
            url = url.replace("{user_id}", "x")
            r = await client.request(method.upper(), url, json={})
            assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a token"
            checked += 1
    assert checked > 20


async def test_jwks_refresh_is_throttled():
    """Random ``kid`` values must not let an attacker make us hammer the IdP."""
    calls = 0
    issuer = LocalIssuer()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=issuer.jwks())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = JwksKeySource(http, jwks_uri="https://idp.test/keys")
        assert await source.get_key(issuer.kid) is not None
        for i in range(20):
            assert await source.get_key(f"forged-{i}") is None
    assert calls == 1


async def test_jwks_failure_fails_closed():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await JwksKeySource(http, jwks_uri="https://idp.test/keys").get_key("any") is None
