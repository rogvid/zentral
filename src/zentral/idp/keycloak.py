"""People and AI agents in the bundled Keycloak realm, via its admin REST API.

The realm itself (roles, the web client, password and lock-out policy) comes
from ``deploy/compose/idp/realm.json``; ``tests/test_idp.py`` keeps that file
in step with the constants here and with :class:`~zentral.security.rbac.Role`.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from zentral.config import Settings
from zentral.security.rbac import Role

WEB_CLIENT_ID = "zentral-web"
API_AUDIENCE = "zentral-api"
AGENT_PREFIX = "agent-"
# Every person sets their own password and an authenticator app at first sign-in.
FIRST_SIGN_IN = ["CONFIGURE_TOTP", "UPDATE_PASSWORD"]

# Puts Zentral's audience into access tokens (the web client and every agent).
AUDIENCE_MAPPER: dict[str, Any] = {
    "name": "zentral-api audience",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-audience-mapper",
    "config": {
        "included.custom.audience": API_AUDIENCE,
        "access.token.claim": "true",
        "id.token.claim": "false",
        "introspection.token.claim": "true",
    },
}

# Roles an AI agent may hold. Admin, auditor and privacy officer are for people
# (segregation of duties); docs/guides/mcp.md explains the choice.
AGENT_ROLES = frozenset({Role.AUTOMATION, Role.INGEST, Role.VIEWER, Role.AGENT})

_AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IdpError(RuntimeError):
    """A request the realm refused, or a mistake in what was asked for."""


# Keycloak answers some validation failures with a message key, not a sentence.
_KEYCLOAK_MESSAGES = {
    "error-person-name-invalid-character": (
        'the name contains a character Keycloak does not allow in names, such as < > " & $ % ! # ?'
    ),
    "error-invalid-length": "a value is too long",
    "error-invalid-email": "not an e-mail address Keycloak accepts",
}


def role_alias(role: Role) -> str:
    """``Zentral.PrivacyOfficer`` → ``privacy-officer``."""
    return role.name.lower().replace("_", "-")


ROLE_ALIASES: dict[str, Role] = {role_alias(r): r for r in Role}


def parse_role(text: str) -> Role:
    """Accept ``agent``, ``privacy-officer`` or the full ``Zentral.Agent``."""
    key = text.strip()
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    try:
        return Role(key)
    except ValueError:
        names = ", ".join(ROLE_ALIASES)
        raise IdpError(f"unknown role {text!r} (use: {names})") from None


@dataclass(frozen=True)
class Person:
    email: str
    enabled: bool
    roles: tuple[Role, ...]


@dataclass(frozen=True)
class AgentCredentials:
    client_id: str
    secret: str
    token_endpoint: str


def one_time_password() -> str:
    """18 random characters from 57 unambiguous ones (no I, O, l, 0, 1): ≈105 bits."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(6)) for _ in range(3))


class KeycloakAdmin:
    """A session against one realm. ``http`` is injectable for tests."""

    def __init__(self, settings: Settings, http: httpx.Client, *, wait_seconds: float = 0) -> None:
        if not settings.keycloak_url:
            raise IdpError("ZENTRAL_KEYCLOAK_URL is not set: this installation has no bundled IdP")
        if not settings.keycloak_admin_password.get_secret_value():
            raise IdpError("ZENTRAL_KEYCLOAK_ADMIN_PASSWORD is not set")
        self.settings = settings
        self.http = http
        self._base = settings.keycloak_url.rstrip("/")
        self._realm = f"{self._base}/admin/realms/{quote(settings.keycloak_realm)}"
        self._token = self._login(wait_seconds)

    # ------------------------------------------------------------------ plumbing
    def _login(self, wait_seconds: float) -> str:
        """Admin token. Keycloak answers health checks a moment before logins
        work, so ``wait_seconds`` allows retrying for a while after a start."""
        deadline = time.monotonic() + wait_seconds
        s = self.settings
        while True:
            try:
                resp = self.http.post(
                    f"{self._base}/realms/master/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "admin-cli",
                        "username": s.keycloak_admin_user,
                        "password": s.keycloak_admin_password.get_secret_value(),
                    },
                )
                if resp.status_code == 200:
                    return str(resp.json()["access_token"])
                problem = f"Keycloak refused the administrator login (HTTP {resp.status_code})"
            except httpx.HTTPError as exc:
                problem = f"cannot reach Keycloak at {self._base} ({type(exc).__name__})"
            if time.monotonic() >= deadline:
                raise IdpError(problem)
            time.sleep(2)

    def _call(
        self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        resp = self.http.request(
            method,
            f"{self._realm}{path}",
            json=json,
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("errorMessage") or body.get("error") or resp.text
            except ValueError:
                detail = resp.text
            detail = _KEYCLOAK_MESSAGES.get(str(detail), detail)
            raise IdpError(f"Keycloak: {detail or resp.reason_phrase} (HTTP {resp.status_code})")
        return resp

    def _get(self, path: str, **params: Any) -> Any:
        return self._call("GET", path, params=params or None).json()

    @staticmethod
    def _created_id(resp: httpx.Response) -> str:
        return resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def _role(self, role: Role) -> dict[str, Any]:
        rep: dict[str, Any] = self._get(f"/roles/{quote(role.value)}")
        return rep

    # -------------------------------------------------------------------- people
    def user_id(self, email: str) -> str:
        found = self._get("/users", email=email, exact="true")
        if not found:
            raise IdpError(f"no user with e-mail {email}")
        return str(found[0]["id"])

    def _user(self, email: str) -> tuple[str, dict[str, Any]]:
        uid = self.user_id(email)
        return uid, self._get(f"/users/{uid}")

    def _set_password(self, uid: str) -> str:
        password = one_time_password()
        self._call(
            "PUT",
            f"/users/{uid}/reset-password",
            json={"type": "password", "value": password, "temporary": True},
        )
        return password

    def _realm_roles(self, uid: str) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = self._get(f"/users/{uid}/role-mappings/realm")
        return [r for r in mapped if r.get("name") in {role.value for role in Role}]

    def _grant(self, uid: str, roles: Iterable[Role]) -> None:
        reps = [self._role(r) for r in roles]
        if reps:
            self._call("POST", f"/users/{uid}/role-mappings/realm", json=reps)

    def add_user(self, email: str, name: str, roles: list[Role]) -> str:
        """Create a person; returns their one-time password."""
        if not _EMAIL.match(email):
            raise IdpError(f"not an e-mail address: {email}")
        name = " ".join(name.split())
        if not 0 < len(name) <= 100:
            raise IdpError("a name of 1 to 100 characters is needed")
        if not roles:
            raise IdpError("give at least one role")
        first, _, last = name.partition(" ")
        rep: dict[str, Any] = {
            "username": email,
            "email": email,
            "emailVerified": True,
            "enabled": True,
            "firstName": first,
            "requiredActions": FIRST_SIGN_IN,
        }
        if last:
            rep["lastName"] = last
        uid = self._created_id(self._call("POST", "/users", json=rep))
        try:
            self._grant(uid, roles)
            return self._set_password(uid)
        except IdpError:
            # Never leave a half-made account (no roles, or no password) behind.
            self._call("DELETE", f"/users/{uid}")
            raise

    def people(self) -> list[Person]:
        holders: dict[str, list[Role]] = {}
        for role in Role:
            for user in self._get(f"/roles/{quote(role.value)}/users", max=10_000):
                holders.setdefault(user["username"], []).append(role)
        users = self._get("/users", max=10_000, briefRepresentation="true")
        return [
            Person(
                u.get("email") or u["username"],
                bool(u.get("enabled")),
                tuple(holders.get(u["username"], ())),
            )
            for u in sorted(users, key=lambda u: u["username"])
            if not u["username"].startswith("service-account-")
        ]

    def set_roles(self, email: str, roles: list[Role]) -> None:
        if not roles:
            raise IdpError("give at least one role (to remove access, disable the person)")
        uid = self.user_id(email)
        current = self._realm_roles(uid)
        if current:
            self._call("DELETE", f"/users/{uid}/role-mappings/realm", json=current)
        self._grant(uid, roles)

    def set_enabled(self, email: str, enabled: bool) -> None:
        uid, rep = self._user(email)
        rep["enabled"] = enabled
        self._call("PUT", f"/users/{uid}", json=rep)
        if not enabled:
            self._call("POST", f"/users/{uid}/logout")

    def reset(self, email: str) -> str:
        """End sessions, remove the authenticator app, issue a one-time password."""
        uid, rep = self._user(email)
        for credential in self._get(f"/users/{uid}/credentials"):
            if credential.get("type") == "otp":
                self._call("DELETE", f"/users/{uid}/credentials/{credential['id']}")
        rep["requiredActions"] = FIRST_SIGN_IN
        self._call("PUT", f"/users/{uid}", json=rep)
        self._call("POST", f"/users/{uid}/logout")
        return self._set_password(uid)

    # -------------------------------------------------------------------- agents
    def _agent_client(self, name: str) -> str:
        found = self._get("/clients", clientId=AGENT_PREFIX + name)
        if not found:
            raise IdpError(f"no agent named {name}")
        return str(found[0]["id"])

    def _credentials(self, name: str, cid: str, secret: dict[str, Any]) -> AgentCredentials:
        issuer = self.settings.oidc_issuer.rstrip("/")
        return AgentCredentials(
            AGENT_PREFIX + name, str(secret["value"]), f"{issuer}/protocol/openid-connect/token"
        )

    def add_agent(self, name: str, role: Role) -> AgentCredentials:
        if not _AGENT_NAME.match(name):
            raise IdpError("agent names are 2-31 lowercase letters, digits and dashes")
        if role not in AGENT_ROLES:
            allowed = ", ".join(sorted(role_alias(r) for r in AGENT_ROLES))
            raise IdpError(f"an AI agent may not hold {role.value} (allowed: {allowed})")
        cid = self._created_id(
            self._call(
                "POST",
                "/clients",
                json={
                    "clientId": AGENT_PREFIX + name,
                    "name": f"AI agent {name}",
                    "enabled": True,
                    "protocol": "openid-connect",
                    "publicClient": False,
                    "serviceAccountsEnabled": True,
                    "standardFlowEnabled": False,
                    "implicitFlowEnabled": False,
                    "directAccessGrantsEnabled": False,
                    "protocolMappers": [AUDIENCE_MAPPER],
                },
            )
        )
        account = self._get(f"/clients/{cid}/service-account-user")
        self._grant(str(account["id"]), [role])
        return self._credentials(name, cid, self._get(f"/clients/{cid}/client-secret"))

    def agents(self) -> list[tuple[str, bool]]:
        clients = self._get("/clients", max=10_000)
        return sorted(
            (c["clientId"][len(AGENT_PREFIX) :], bool(c.get("enabled")))
            for c in clients
            if c["clientId"].startswith(AGENT_PREFIX)
        )

    def rotate_agent(self, name: str) -> AgentCredentials:
        cid = self._agent_client(name)
        secret = self._call("POST", f"/clients/{cid}/client-secret").json()
        return self._credentials(name, cid, secret)

    def remove_agent(self, name: str) -> None:
        self._call("DELETE", f"/clients/{self._agent_client(name)}")
