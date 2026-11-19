"""``zentral idp``: people and AI agents in the bundled Keycloak, and the realm
file that the single-server deployment imports."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from tests.conftest import make_settings
from zentral.cli import app
from zentral.idp import cli as idp_cli
from zentral.idp.keycloak import (
    AGENT_PREFIX,
    API_AUDIENCE,
    AUDIENCE_MAPPER,
    FIRST_SIGN_IN,
    WEB_CLIENT_ID,
    IdpError,
    KeycloakAdmin,
    parse_role,
)
from zentral.security.rbac import Role

ROOT = Path(__file__).parents[1]
REALM = json.loads((ROOT / "deploy/compose/idp/realm.json").read_text(encoding="utf-8"))
BASE = "http://keycloak:8080"
ISSUER = "https://id.desk.example/realms/zentral"


class FakeKeycloak:
    """The slice of Keycloak's admin REST API that ``KeycloakAdmin`` uses."""

    def __init__(self, admin_password: str = "pw") -> None:  # noqa: S107
        self.admin_password = admin_password
        self.users: dict[str, dict[str, Any]] = {}
        self.passwords: dict[str, dict[str, Any]] = {}
        self.credentials: dict[str, list[dict[str, Any]]] = {}
        self.mappings: dict[str, set[str]] = {}
        self.clients: dict[str, dict[str, Any]] = {}
        self.secrets: dict[str, str] = {}
        self.logouts: list[str] = []
        self.refuse_passwords = False
        self.roles = {r["name"]: {"id": uuid.uuid4().hex, **r} for r in REALM["roles"]["realm"]}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/realms/master/protocol/openid-connect/token":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            ok = (
                form.get("client_id") == "admin-cli" and form.get("password") == self.admin_password
            )
            return httpx.Response(200, json={"access_token": "adm"}) if ok else httpx.Response(401)
        if request.headers.get("Authorization") != "Bearer adm":
            return httpx.Response(401)
        prefix = "/admin/realms/zentral"
        assert path.startswith(prefix), path
        parts = [p for p in path[len(prefix) :].split("/") if p]
        body = json.loads(request.content) if request.content else None
        return self.route(request.method, parts, request.url.params, body)

    def route(self, method: str, parts: list[str], q: Any, body: Any) -> httpx.Response:
        match method, parts:
            case "GET", ["users"]:
                users = list(self.users.values())
                if "email" in q:
                    users = [u for u in users if u["email"] == q["email"]]
                return httpx.Response(200, json=users)
            case "POST", ["users"]:
                if '"' in body.get("firstName", "") + body.get("lastName", ""):
                    return httpx.Response(
                        400, json={"errorMessage": "error-person-name-invalid-character"}
                    )
                if any(u["email"] == body["email"] for u in self.users.values()):
                    return httpx.Response(409, json={"errorMessage": "User exists with same email"})
                uid = uuid.uuid4().hex
                self.users[uid] = {"id": uid, **body}
                self.mappings[uid] = set()
                self.credentials[uid] = [
                    {"id": "c-otp", "type": "otp"},
                    {"id": "c-pw", "type": "password"},
                ]
                return httpx.Response(
                    201, headers={"Location": f"{BASE}/admin/realms/zentral/users/{uid}"}
                )
            case "GET", ["users", uid]:
                return httpx.Response(200, json=self.users[uid])
            case "PUT", ["users", uid]:
                self.users[uid] = body
                return httpx.Response(204)
            case "DELETE", ["users", uid]:
                del self.users[uid]
                return httpx.Response(204)
            case "PUT", ["users", uid, "reset-password"]:
                if self.refuse_passwords:
                    return httpx.Response(400, json={"error": "invalidPasswordMinLengthMessage"})
                self.passwords[uid] = body
                return httpx.Response(204)
            case "POST", ["users", uid, "logout"]:
                self.logouts.append(uid)
                return httpx.Response(204)
            case "GET", ["users", uid, "credentials"]:
                return httpx.Response(200, json=self.credentials[uid])
            case "DELETE", ["users", uid, "credentials", cid]:
                self.credentials[uid] = [c for c in self.credentials[uid] if c["id"] != cid]
                return httpx.Response(204)
            case "GET", ["users", uid, "role-mappings", "realm"]:
                extra = [{"name": "default-roles-zentral"}]
                return httpx.Response(200, json=[self.roles[n] for n in self.mappings[uid]] + extra)
            case "POST", ["users", uid, "role-mappings", "realm"]:
                assert all(r == self.roles[r["name"]] for r in body), "full role representations"
                self.mappings[uid] |= {r["name"] for r in body}
                return httpx.Response(204)
            case "DELETE", ["users", uid, "role-mappings", "realm"]:
                assert "default-roles-zentral" not in {r["name"] for r in body}
                self.mappings[uid] -= {r["name"] for r in body}
                return httpx.Response(204)
            case "GET", ["roles", name]:
                return (
                    httpx.Response(200, json=self.roles[name])
                    if name in self.roles
                    else httpx.Response(404)
                )
            case "GET", ["roles", name, "users"]:
                holders = [u for uid, u in self.users.items() if name in self.mappings[uid]]
                return httpx.Response(200, json=holders)
            case "GET", ["clients"]:
                clients = list(self.clients.values())
                if "clientId" in q:
                    clients = [c for c in clients if c["clientId"] == q["clientId"]]
                return httpx.Response(200, json=clients)
            case "POST", ["clients"]:
                cid = uuid.uuid4().hex
                self.clients[cid] = {"id": cid, **body}
                self.secrets[cid] = uuid.uuid4().hex
                sa = uuid.uuid4().hex
                self.users[sa] = {
                    "id": sa,
                    "username": f"service-account-{body['clientId']}",
                    "email": None,
                }
                self.mappings[sa] = set()
                self.clients[cid]["_sa"] = sa
                return httpx.Response(
                    201, headers={"Location": f"{BASE}/admin/realms/zentral/clients/{cid}"}
                )
            case "GET", ["clients", cid, "service-account-user"]:
                return httpx.Response(200, json=self.users[self.clients[cid]["_sa"]])
            case "GET", ["clients", cid, "client-secret"]:
                return httpx.Response(200, json={"type": "secret", "value": self.secrets[cid]})
            case "POST", ["clients", cid, "client-secret"]:
                self.secrets[cid] = uuid.uuid4().hex
                return httpx.Response(200, json={"type": "secret", "value": self.secrets[cid]})
            case "DELETE", ["clients", cid]:
                del self.clients[cid]
                return httpx.Response(204)
        raise AssertionError(f"unexpected {method} {parts}")


def settings(**overrides: Any):
    base = {
        "keycloak_url": BASE,
        "keycloak_admin_password": "pw",
        "oidc_issuer": ISSUER,
        "public_base_url": "https://desk.example",
    }
    return make_settings(**{**base, **overrides})


@pytest.fixture
def kc():
    fake = FakeKeycloak()
    with httpx.Client(transport=httpx.MockTransport(fake)) as http:
        yield fake, KeycloakAdmin(settings(), http)


# --- the realm file stays in step with the code ---------------------------------------
def test_realm_defines_exactly_zentrals_roles():
    assert {r["name"] for r in REALM["roles"]["realm"]} == {r.value for r in Role}


def test_realm_web_client_matches_what_zentral_expects():
    compose = (ROOT / "deploy/compose/compose.idp.yaml").read_text(encoding="utf-8")
    (client,) = [c for c in REALM["clients"] if c["clientId"] == WEB_CLIENT_ID]
    assert f"ZENTRAL_OIDC_CLIENT_ID: {WEB_CLIENT_ID}" in compose
    assert f"ZENTRAL_OIDC_AUDIENCES: {API_AUDIENCE}" in compose
    assert f"/realms/{REALM['realm']}" in compose
    # A public client that can only use the code flow with PKCE …
    assert client["publicClient"] and client["standardFlowEnabled"]
    assert not (client["implicitFlowEnabled"] or client["directAccessGrantsEnabled"])
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    # … whose tokens carry Zentral's audience, exactly as agents' tokens do.
    assert client["protocolMappers"] == [AUDIENCE_MAPPER]


def test_realm_security_policy():
    assert REALM["registrationAllowed"] is False
    assert REALM["bruteForceProtected"] is True
    assert "length(12)" in REALM["passwordPolicy"]
    assert REALM["accessTokenLifespan"] <= 300  # disabling someone bites within 5 minutes


# --- people ------------------------------------------------------------------------------
def test_add_user_requires_password_change_and_authenticator(kc):
    fake, admin = kc
    password = admin.add_user("jane@example.com", "Jane  van Doe", [Role.AGENT, Role.VIEWER])
    (user,) = [u for u in fake.users.values() if u["email"] == "jane@example.com"]
    assert user["requiredActions"] == FIRST_SIGN_IN
    assert (user["firstName"], user["lastName"]) == ("Jane", "van Doe")
    assert fake.passwords[user["id"]] == {"type": "password", "value": password, "temporary": True}
    assert re.fullmatch(r"[A-Za-z2-9]{6}-[A-Za-z2-9]{6}-[A-Za-z2-9]{6}", password)
    assert fake.mappings[user["id"]] == {"Zentral.Agent", "Zentral.Viewer"}


def test_user_mistakes_are_refused(kc):
    fake, admin = kc
    admin.add_user("jane@example.com", "Jane", [Role.AGENT])
    with pytest.raises(IdpError, match="exists"):
        admin.add_user("jane@example.com", "Jane Again", [Role.AGENT])
    with pytest.raises(IdpError, match="e-mail"):
        admin.add_user("not-an-address", "X", [Role.AGENT])
    with pytest.raises(IdpError, match="no user"):
        admin.set_enabled("nobody@example.com", False)
    with pytest.raises(IdpError, match="unknown role"):
        parse_role("superuser")
    # Apostrophes and accents are fine; Keycloak's refusal of quotes reads as a sentence.
    admin.add_user("o@example.com", "Sinéad O'Brien", [Role.VIEWER])
    assert any(u.get("lastName") == "O'Brien" for u in fake.users.values())
    with pytest.raises(IdpError, match="does not allow in names"):
        admin.add_user("q@example.com", 'Shay "S" Byrne', [Role.VIEWER])


def test_a_failed_step_leaves_no_half_made_account(kc):
    fake, admin = kc
    fake.refuse_passwords = True
    with pytest.raises(IdpError):
        admin.add_user("jane@example.com", "Jane", [Role.AGENT])
    assert fake.users == {}


def test_roles_are_replaced_not_added(kc):
    fake, admin = kc
    admin.add_user("jane@example.com", "Jane", [Role.ADMIN])
    admin.set_roles("jane@example.com", [Role.AUDITOR])
    uid = admin.user_id("jane@example.com")
    assert fake.mappings[uid] == {"Zentral.Auditor"}
    with pytest.raises(IdpError, match="disable"):
        admin.set_roles("jane@example.com", [])


def test_disable_ends_sessions_and_reset_removes_the_authenticator(kc):
    fake, admin = kc
    first = admin.add_user("jane@example.com", "Jane", [Role.AGENT])
    uid = admin.user_id("jane@example.com")
    fake.users[uid]["requiredActions"] = []  # she has signed in since

    admin.set_enabled("jane@example.com", False)
    assert fake.users[uid]["enabled"] is False and fake.logouts == [uid]

    second = admin.reset("jane@example.com")
    assert second != first and fake.passwords[uid]["temporary"] is True
    assert [c["type"] for c in fake.credentials[uid]] == ["password"]
    assert fake.users[uid]["requiredActions"] == FIRST_SIGN_IN
    assert fake.logouts == [uid, uid]


def test_people_lists_roles_and_leaves_out_agents(kc):
    _, admin = kc
    admin.add_user("b@example.com", "B", [Role.AUDITOR])
    admin.add_user("a@example.com", "A", [Role.ADMIN, Role.AGENT])
    admin.add_agent("triage", Role.AUTOMATION)
    people = admin.people()
    assert [(p.email, p.roles) for p in people] == [
        ("a@example.com", (Role.ADMIN, Role.AGENT)),
        ("b@example.com", (Role.AUDITOR,)),
    ]


# --- agents ------------------------------------------------------------------------------
def test_agent_gets_a_confidential_client_with_audience_and_one_role(kc):
    fake, admin = kc
    creds = admin.add_agent("triage-bot", Role.AUTOMATION)
    (client,) = fake.clients.values()
    assert client["clientId"] == creds.client_id == f"{AGENT_PREFIX}triage-bot"
    assert client["serviceAccountsEnabled"] and not client["publicClient"]
    assert not (client["standardFlowEnabled"] or client["directAccessGrantsEnabled"])
    assert client["protocolMappers"] == [AUDIENCE_MAPPER]
    assert fake.mappings[client["_sa"]] == {"Zentral.Automation"}
    assert creds.secret == fake.secrets[client["id"]]
    assert creds.token_endpoint == f"{ISSUER}/protocol/openid-connect/token"

    rotated = admin.rotate_agent("triage-bot")
    assert rotated.secret != creds.secret
    assert admin.agents() == [("triage-bot", True)]
    admin.remove_agent("triage-bot")
    assert admin.agents() == []


@pytest.mark.parametrize("role", [Role.ADMIN, Role.AUDITOR, Role.PRIVACY_OFFICER])
def test_agents_never_hold_peoples_roles(kc, role):
    fake, admin = kc
    with pytest.raises(IdpError, match="may not hold"):
        admin.add_agent("bot", role)
    assert fake.clients == {}


def test_agent_names_are_checked(kc):
    _, admin = kc
    with pytest.raises(IdpError, match="lowercase"):
        admin.add_agent("Bad Name", Role.AUTOMATION)


# --- login and configuration -----------------------------------------------------------------
def test_needs_configuration_and_a_working_login():
    with httpx.Client(transport=httpx.MockTransport(FakeKeycloak())) as http:
        with pytest.raises(IdpError, match="ZENTRAL_KEYCLOAK_URL"):
            KeycloakAdmin(settings(keycloak_url=""), http)
        with pytest.raises(IdpError, match="refused the administrator login"):
            KeycloakAdmin(settings(keycloak_admin_password="wrong"), http)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(down)) as http,
        pytest.raises(IdpError, match="cannot reach Keycloak"),
    ):
        KeycloakAdmin(settings(), http)


def test_login_waits_for_keycloak_to_start(monkeypatch):
    fake, attempts = FakeKeycloak(), []

    def starting(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("starting", request=request)
        return fake(request)

    monkeypatch.setattr("zentral.idp.keycloak.time.sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(starting)) as http:
        KeycloakAdmin(settings(), http, wait_seconds=30)
    assert len(attempts) == 3


def test_the_admin_password_never_appears_in_output(monkeypatch):
    fake = FakeKeycloak(admin_password="s3cret-admin")
    real_client = httpx.Client
    monkeypatch.setattr(
        idp_cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(fake))
    )
    monkeypatch.setattr(
        idp_cli, "get_settings", lambda: settings(keycloak_admin_password="s3cret-admin")
    )
    runner = CliRunner()
    out = runner.invoke(app, ["idp", "user", "add", "jane@example.com", "Jane Doe", "agent"])
    assert out.exit_code == 0, out.output
    assert "One-time password:" in out.output and "s3cret-admin" not in out.output
    out = runner.invoke(app, ["idp", "user", "add", "jane@example.com", "Jane Doe", "agent"])
    assert out.exit_code == 1 and "exists" in out.output
    out = runner.invoke(app, ["idp", "user", "add", "x@example.com", "X", "root"])
    assert out.exit_code == 2 and "unknown role" in out.output
    out = runner.invoke(app, ["idp", "agent", "add", "bot", "admin"])
    assert out.exit_code == 1 and "may not hold" in out.output
    out = runner.invoke(app, ["idp", "user", "list"])
    assert "jane@example.com" in out.output and "agent" in out.output
