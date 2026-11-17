"""Pluggable identity: generic OpenID Connect providers, role mapping, the dev IdP."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.conftest import make_settings
from zentral.api.app import create_app
from zentral.config import ConfigurationError
from zentral.security.devauth import LocalIssuer
from zentral.security.rbac import Permission
from zentral.security.tokens import (
    AuthenticationError,
    IdentityProfile,
    JwksKeySource,
    StaticKeySource,
    TokenValidator,
    oidc_profile,
)

ISSUER = "https://idp.corp.example/realms/main"


def keycloak_like(**profile_overrides):
    """An IdP that looks nothing like Entra: sub, scope, nested roles, group names."""
    idp = LocalIssuer(issuer=ISSUER, audience="zentral-api")
    profile = IdentityProfile(
        name="oidc",
        issuer=ISSUER,
        audiences=("zentral-api",),
        roles_claim="realm_access.roles",
        groups_claim="groups",
        required_scope="zentral",
        role_map={"desk-admins": "Zentral.Admin", "/staff/agents": "Zentral.Agent"},
        **profile_overrides,
    )
    return idp, TokenValidator(profile, StaticKeySource(idp.jwks()))


def token(idp: LocalIssuer, **claims):
    base = {
        "oid": None,
        "scp": None,
        "tid": None,
        "roles": None,
        "scope": "openid zentral",
        "sub": "kc-user-1",
    }
    return idp.issue(subject="ignored", roles=[], name="Kim", claims={**base, **claims})


async def test_generic_oidc_user_with_mapped_roles_and_groups():
    idp, validator = keycloak_like()
    p = await validator.validate(
        token(
            idp,
            realm_access={"roles": ["desk-admins", "offline_access"]},
            groups=["/staff/agents", "/other"],
            email="kim@corp.example",
        )
    )
    assert (p.subject, p.kind, p.email) == ("kc-user-1", "user", "kim@corp.example")
    assert p.roles == {"Zentral.Admin", "Zentral.Agent"}
    assert p.tenant_id == ISSUER
    assert p.can(Permission.TEAM_MANAGE)


async def test_unmapped_external_roles_grant_nothing():
    idp, validator = keycloak_like()
    p = await validator.validate(token(idp, realm_access={"roles": ["admin", "superuser", "root"]}))
    assert p.roles == frozenset() and p.permissions == frozenset()


async def test_native_role_names_pass_through():
    idp, validator = keycloak_like()
    p = await validator.validate(token(idp, realm_access={"roles": ["Zentral.Viewer"]}))
    assert p.roles == {"Zentral.Viewer"}


async def test_namespaced_claim_names_are_supported():
    """Auth0-style claims whose *name* contains dots."""
    idp, validator = keycloak_like(), None
    idp = LocalIssuer(issuer=ISSUER, audience="zentral-api")
    profile = IdentityProfile(
        name="oidc",
        issuer=ISSUER,
        audiences=("zentral-api",),
        roles_claim="https://zentral.example/roles",
    )
    validator = TokenValidator(profile, StaticKeySource(idp.jwks()))
    p = await validator.validate(token(idp, **{"https://zentral.example/roles": ["Zentral.Agent"]}))
    assert p.roles == {"Zentral.Agent"}


@pytest.mark.parametrize(
    "claims",
    [
        {"scope": "openid"},  # required scope missing
        {"sub": None},
        {"sub": "x" * 65},
        {"iss": "https://evil.example/realms/main"},
        {"aud": "another-api"},
    ],
    ids=["scope", "no-subject", "oversized-subject", "issuer", "audience"],
)
async def test_generic_oidc_rejects(claims):
    idp, validator = keycloak_like()
    with pytest.raises(AuthenticationError):
        await validator.validate(token(idp, **claims))


@pytest.mark.parametrize(
    "claims",
    [
        {"gty": "client-credentials", "sub": "abc@clients"},
        {"azp": "svc-1", "sub": "svc-1"},
        {"preferred_username": "service-account-triage-bot"},
    ],
    ids=["auth0", "okta", "keycloak"],
)
async def test_service_tokens_are_recognised_and_need_a_role(claims):
    idp, validator = keycloak_like()
    ok = await validator.validate(
        token(idp, scope="", realm_access={"roles": ["Zentral.Automation"]}, **claims)
    )
    assert ok.kind == "service" and ok.roles == {"Zentral.Automation"}
    with pytest.raises(AuthenticationError, match="no Zentral role"):
        await validator.validate(token(idp, scope="", **claims))


def test_profile_refuses_weak_or_invalid_configuration():
    base = {"name": "oidc", "issuer": ISSUER, "audiences": ("a",)}
    for bad in (
        {"algorithms": ("HS256",)},
        {"algorithms": ("none",)},
        {"algorithms": ()},
        {"audiences": ()},
        {"issuer": ""},
        {"role_map": {"admins": "Zentral.God"}},
    ):
        with pytest.raises(ValueError):
            IdentityProfile(**{**base, **bad})


async def test_es256_tokens_are_supported_when_enabled():
    import json
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    key = ec.generate_private_key(ec.SECP256R1())
    jwk = {**json.loads(ECAlgorithm.to_jwk(key.public_key())), "kid": "ec1", "use": "sig"}
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "a",
        "sub": "u",
        "iat": now,
        "exp": now + 60,
        "roles": ["Zentral.Viewer"],
    }
    signed = jwt.encode(claims, key, algorithm="ES256", headers={"kid": "ec1"})

    es = TokenValidator(
        IdentityProfile(name="oidc", issuer=ISSUER, audiences=("a",), algorithms=("ES256",)),
        StaticKeySource({"keys": [jwk]}),
    )
    assert (await es.validate(signed)).subject == "u"
    rs_only = TokenValidator(
        IdentityProfile(name="oidc", issuer=ISSUER, audiences=("a",)),
        StaticKeySource({"keys": [jwk]}),
    )
    with pytest.raises(AuthenticationError, match="algorithm"):
        await rs_only.validate(signed)


async def test_keys_are_discovered_from_the_issuer_and_issuer_must_match():
    idp = LocalIssuer(issuer=ISSUER)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200, json={"issuer": served_issuer, "jwks_uri": f"{ISSUER}/certs"}
            )
        return httpx.Response(200, json=idp.jwks())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        served_issuer = ISSUER
        assert await JwksKeySource(http, issuer=ISSUER).get_key(idp.kid) is not None
        assert calls == ["/realms/main/.well-known/openid-configuration", "/realms/main/certs"]
        served_issuer = "https://evil.example"  # mix-up attack: wrong issuer in metadata
        assert await JwksKeySource(http, issuer=ISSUER).get_key(idp.kid) is None


def test_settings_build_an_oidc_profile(monkeypatch):
    monkeypatch.setenv("ZENTRAL_ROLE_MAP", "desk-admins=Zentral.Admin, agents=Zentral.Agent")
    monkeypatch.setenv("ZENTRAL_OIDC_AUDIENCES", "zentral-api,account")
    from zentral.config import Settings

    s = Settings(
        _env_file=None, auth_mode="oidc", oidc_issuer=ISSUER, oidc_roles_claim="realm_access.roles"
    )
    profile = oidc_profile(s)
    assert profile.audiences == ("zentral-api", "account")
    assert profile.role_map == {"desk-admins": "Zentral.Admin", "agents": "Zentral.Agent"}
    assert s.issuer == ISSUER
    with pytest.raises(ConfigurationError):
        make_settings(auth_mode="oidc").validate_for_runtime()


async def test_entra_groups_can_be_mapped_to_roles(issuer):
    group = "11111111-2222-4333-8444-555555555555"
    validator = TokenValidator(
        issuer.profile(role_map={group: "Zentral.Agent"}), StaticKeySource(issuer.jwks())
    )
    p = await validator.validate(issuer.issue(subject="u1", roles=[], name="U", groups=[group]))
    assert p.roles == {"Zentral.Agent"}


# --- whole application on a generic OIDC provider -----------------------------------------


async def run_app(app):
    ready, stop = asyncio.Event(), asyncio.Event()

    async def lifespan():
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await ready.wait()
    return stop, task


async def test_application_runs_on_generic_oidc(runtime):
    idp, validator = keycloak_like()
    s = runtime.settings
    s.auth_mode, s.oidc_issuer, s.oidc_audiences = "oidc", ISSUER, ["zentral-api"]
    s.oidc_client_id, s.oidc_required_scope = "zentral-web", "zentral"
    app = create_app(s, runtime=runtime, validator=validator)
    stop, task = await run_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        config = (await c.get("/api/v1/config")).json()
        assert config["auth"] == {
            "mode": "oidc",
            "authority": ISSUER,
            "client_id": "zentral-web",
            "scopes": ["openid", "profile", "zentral"],
        }
        h = {"Authorization": "Bearer " + token(idp, realm_access={"roles": ["desk-admins"]})}
        r = await c.get("/api/v1/me", headers=h)
        assert r.status_code == 200 and "Zentral.Admin" in r.json()["roles"]
        # The browser may talk to this IdP and nothing else.
        assert (
            "connect-src 'self' https://idp.corp.example;" in r.headers["content-security-policy"]
        )
        meta = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert meta["authorization_servers"] == [ISSUER]
        assert (await c.get("/dev-idp/authorize")).status_code == 404
    stop.set()
    await task


# --- development identity provider (authorization code + PKCE) ------------------------------


@pytest.fixture
async def dev_app(runtime):
    runtime.settings.auth_mode = "dev"
    app = create_app(runtime.settings, runtime=runtime)
    stop, task = await run_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c
    stop.set()
    await task


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


async def authorize(c, challenge, redirect="http://testserver/", persona="agent"):
    return await c.get(
        "/dev-idp/authorize",
        params={
            "response_type": "code",
            "client_id": "zentral-dev",
            "redirect_uri": redirect,
            "state": "st4te",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **({"persona": persona} if persona else {}),
        },
    )


async def exchange(c, code, verifier, redirect="http://testserver/"):
    return await c.post(
        "/dev-idp/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect,
            "client_id": "zentral-dev",
        },
    )


async def test_dev_idp_full_code_flow(dev_app):
    c = dev_app
    config = (await c.get("/api/v1/config")).json()["auth"]
    assert config["mode"] == "dev" and config["authority"] == "http://testserver/dev-idp"
    discovery = (await c.get("/dev-idp/.well-known/openid-configuration")).json()
    assert discovery["issuer"] == config["authority"]

    verifier, challenge = pkce()
    page = await authorize(c, challenge, persona=None)
    assert page.status_code == 200 and "Arne Agent" in page.text and "<script" not in page.text

    r = await authorize(c, challenge)
    assert r.status_code == 302
    location = urlsplit(r.headers["location"])
    params = parse_qs(location.query)
    assert (location.netloc, params["state"]) == ("testserver", ["st4te"])
    code = params["code"][0]

    tokens = await exchange(c, code, verifier)
    assert tokens.status_code == 200, tokens.text
    me = await c.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {tokens.json()['access_token']}"}
    )
    assert me.json()["roles"] == ["Zentral.Agent"]
    # Codes are single use.
    assert (await exchange(c, code, verifier)).status_code == 400


async def test_dev_idp_enforces_pkce_and_redirect_rules(dev_app):
    c = dev_app
    verifier, challenge = pkce()
    code = parse_qs(urlsplit((await authorize(c, challenge)).headers["location"]).query)["code"][0]
    assert (await exchange(c, code, secrets.token_urlsafe(48))).status_code == 400  # wrong verifier

    code = parse_qs(urlsplit((await authorize(c, challenge)).headers["location"]).query)["code"][0]
    assert (
        await exchange(c, code, verifier, redirect="http://testserver/other")
    ).status_code == 400

    for evil in ("https://evil.example/cb", "http://testserver.evil.example/", "//evil.example"):
        assert (await authorize(c, challenge, redirect=evil)).status_code == 400
    assert (await authorize(c, challenge, persona="root")).status_code == 400
    plain = await c.get(
        "/dev-idp/authorize",
        params={
            "redirect_uri": "http://testserver/",
            "code_challenge": challenge,
            "code_challenge_method": "plain",
        },
    )
    assert plain.status_code == 400
