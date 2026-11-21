"""Access-token validation for every supported identity provider.

Zentral is a pure OAuth 2.0 *resource server*: it never sees passwords, never
issues long-lived credentials for people, and holds no session state. Every
request must carry an access token issued by the installation's identity
provider (IdP). Which IdP that is — and how its claims map to Zentral's
principals and roles — is described by an ``IdentityProfile`` that is built
from configuration at start-up:

* ``entra`` — Microsoft Entra ID (default),
* ``oidc``  — any standards-compliant OpenID Connect provider (Keycloak, Okta,
  Auth0, AD FS, Authentik, …),
* ``dev``   — the built-in development IdP (never in production).

Validation rules (all mandatory, for every profile):

* the signature verifies against the IdP's published keys, using an asymmetric
  algorithm from an allow-list; the algorithm is never taken on trust from the
  token (no ``none``, no HMAC key-confusion),
* ``iss`` equals the configured issuer exactly, ``aud`` names this API,
* ``exp``/``nbf``/``iat`` are valid (60 s leeway for clock skew),
* a stable subject is present,
* people's tokens carry the required scope (if one is configured); service
  tokens carry at least one role.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx
import jwt
import structlog
from jwt import PyJWK

from zentral.config import Settings
from zentral.logging import error_fields
from zentral.security.principal import Principal
from zentral.security.rbac import Role

log = structlog.get_logger(__name__)

# Asymmetric signature algorithms only.
ALLOWED_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)
LEEWAY_SECONDS = 60


class AuthCheck(StrEnum):
    """Which sign-in check failed: fixed codes, so a log line holds no token or user data."""

    MALFORMED = "malformed_token"
    UNSUPPORTED_ALGORITHM = "unsupported_algorithm"
    MISSING_KEY_ID = "missing_key_id"
    UNKNOWN_SIGNING_KEY = "unknown_signing_key"
    REJECTED = "token_rejected"  # signature, expiry, audience or issuer: see ``cause``
    WRONG_TENANT = "wrong_tenant"
    UNUSABLE_SUBJECT = "unusable_subject"
    APP_HAS_SCOPES = "app_token_has_scopes"
    SERVICE_NO_ROLE = "service_token_no_role"
    SCOPE_MISSING = "scope_missing"
    UNSPECIFIED = "unspecified"  # a custom ``Authenticator`` that names no check


class AuthenticationError(Exception):
    """Token missing or invalid. Log ``check``; the message is not for logs or clients."""

    def __init__(self, message: str = "", check: AuthCheck = AuthCheck.UNSPECIFIED) -> None:
        super().__init__(message)
        # Anything else is refused, so that only a code from the closed set is ever logged.
        self.check = check if isinstance(check, AuthCheck) else AuthCheck.UNSPECIFIED


def sign_in_failure_fields(exc: AuthenticationError) -> dict[str, str]:
    """Log fields for a refused token: its types and fixed check code, never its text."""
    return {**error_fields(exc), "check": exc.check.value}


class Authenticator(Protocol):
    """Anything that can turn a bearer token into a principal.

    ``TokenValidator`` is the built-in implementation; an installation with
    unusual needs can pass its own to ``create_app(validator=...)``.
    """

    async def validate(self, token: str) -> Principal: ...


@dataclass(frozen=True, slots=True)
class IdentityProfile:
    """How to recognise and interpret one identity provider's access tokens."""

    name: str
    issuer: str
    audiences: tuple[str, ...]
    algorithms: tuple[str, ...] = ("RS256",)
    subject_claim: str = "sub"
    scope_claim: str = "scope"
    required_scope: str = ""
    roles_claim: str = "roles"  # dotted path, e.g. "realm_access.roles"
    groups_claim: str = "groups"
    name_claim: str = "name"
    email_claim: str = "email"
    # If set, the token's tenant claim must equal this value (Entra ``tid``).
    tenant_claim: str = ""
    tenant_id: str = ""
    # Entra semantics: a token without delegated scopes is an application token.
    entra_app_tokens: bool = False
    # External role or group value -> Zentral role name.
    role_map: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bad = set(self.algorithms) - ALLOWED_ALGORITHMS
        if bad or not self.algorithms:
            raise ValueError(f"unsupported token signing algorithm(s): {sorted(bad)}")
        if not self.issuer or not self.audiences or not all(self.audiences):
            raise ValueError("identity profile needs an issuer and at least one audience")
        unknown = set(self.role_map.values()) - {r.value for r in Role}
        if unknown:
            raise ValueError(f"role map targets unknown Zentral role(s): {sorted(unknown)}")


def entra_profile(settings: Settings) -> IdentityProfile:
    return IdentityProfile(
        name="entra",
        issuer=settings.entra_issuer,
        audiences=tuple(settings.entra_audiences),
        algorithms=("RS256",),
        subject_claim="oid",  # stable and never reassigned, unlike UPN or e-mail
        scope_claim="scp",
        required_scope=settings.entra_required_scope,
        roles_claim="roles",
        groups_claim="groups",
        name_claim="name",
        email_claim="preferred_username",
        tenant_claim="tid",
        tenant_id=settings.entra_tenant_id,
        entra_app_tokens=True,
        role_map=dict(settings.role_map),
    )


def oidc_profile(settings: Settings) -> IdentityProfile:
    return IdentityProfile(
        name="oidc",
        issuer=settings.oidc_issuer,
        audiences=tuple(settings.oidc_audiences),
        algorithms=tuple(settings.oidc_algorithms),
        subject_claim=settings.oidc_subject_claim,
        scope_claim=settings.oidc_scope_claim,
        required_scope=settings.oidc_required_scope,
        roles_claim=settings.oidc_roles_claim,
        groups_claim=settings.oidc_groups_claim,
        name_claim=settings.oidc_name_claim,
        email_claim=settings.oidc_email_claim,
        role_map=dict(settings.role_map),
    )


# --- signing keys ---------------------------------------------------------------------


class KeySource(Protocol):
    async def get_key(self, kid: str) -> PyJWK | None: ...


class StaticKeySource:
    """Keys supplied directly (development IdP and tests)."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self._keys = {k["kid"]: PyJWK.from_dict(k) for k in jwks["keys"]}

    async def get_key(self, kid: str) -> PyJWK | None:
        return self._keys.get(kid)


class JwksKeySource:
    """Fetches and caches the IdP's signing keys.

    The JWKS location is either given, or discovered from the issuer's
    ``/.well-known/openid-configuration`` (whose ``issuer`` must match exactly).

    An unknown ``kid`` triggers a refresh (key rollover), but at most once per
    ``min_refresh_interval`` so that forged tokens with random ``kid`` values
    cannot be used to make this service hammer the identity provider.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        jwks_uri: str = "",
        issuer: str = "",
        max_age: float = 24 * 3600,
        min_refresh_interval: float = 60,
    ) -> None:
        if not jwks_uri and not issuer:
            raise ValueError("either jwks_uri or issuer is required")
        self._uri = jwks_uri
        self._issuer = issuer
        self._http = http
        self._max_age = max_age
        self._min_refresh = min_refresh_interval
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at = 0.0
        self._last_attempt = float("-inf")
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> PyJWK | None:
        now = time.monotonic()
        stale = now - self._fetched_at > self._max_age
        if (kid not in self._keys or stale) and now - self._last_attempt >= self._min_refresh:
            await self._refresh()
        return self._keys.get(kid)

    async def _discover(self) -> str:
        url = self._issuer.rstrip("/") + "/.well-known/openid-configuration"
        resp = await self._http.get(url, timeout=10)
        resp.raise_for_status()
        document = resp.json()
        if document.get("issuer") != self._issuer:
            raise ValueError("discovery document is for a different issuer")
        uri = str(document["jwks_uri"])
        if self._issuer.startswith("https://") and not uri.startswith("https://"):
            raise ValueError("jwks_uri must use https")
        return uri

    async def _refresh(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now - self._last_attempt < self._min_refresh:
                return
            self._last_attempt = now
            try:
                if not self._uri:
                    self._uri = await self._discover()
                resp = await self._http.get(self._uri, timeout=10)
                resp.raise_for_status()
                keys = {
                    k["kid"]: PyJWK.from_dict(k)
                    for k in resp.json()["keys"]
                    if k.get("kty") in ("RSA", "EC") and k.get("use", "sig") == "sig" and "kid" in k
                }
            except (httpx.HTTPError, KeyError, ValueError, TypeError, jwt.PyJWTError) as exc:
                # Keep serving with the keys we have; never fail open.
                log.error("jwks_refresh_failed", error=type(exc).__name__)
                return
            if keys:
                self._keys = keys
                self._fetched_at = now


# --- validation -------------------------------------------------------------------------


def _claim(claims: dict[str, Any], path: str) -> Any:
    """Read a claim by dotted path; a literal key containing dots wins (Auth0 namespaces)."""
    if not path:
        return None
    if path in claims:
        return claims[path]
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _strings(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(value.replace(",", " ").split())
    if isinstance(value, list):
        return frozenset(v for v in value if isinstance(v, str))
    return frozenset()


class TokenValidator:
    def __init__(self, profile: IdentityProfile, keys: KeySource) -> None:
        self.profile = profile
        self._keys = keys

    @classmethod
    def for_settings(cls, settings: Settings, http: httpx.AsyncClient) -> TokenValidator:
        if settings.auth_mode == "entra":
            return cls(
                entra_profile(settings), JwksKeySource(http, jwks_uri=settings.entra_jwks_uri)
            )
        if settings.auth_mode == "oidc":
            return cls(
                oidc_profile(settings),
                JwksKeySource(http, jwks_uri=settings.oidc_jwks_uri, issuer=settings.oidc_issuer),
            )
        raise ValueError(f"no token validator for auth mode {settings.auth_mode!r}")

    async def validate(self, token: str) -> Principal:
        profile = self.profile
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("malformed token", AuthCheck.MALFORMED) from exc
        if header.get("alg") not in profile.algorithms:
            raise AuthenticationError(
                "unsupported signing algorithm", AuthCheck.UNSUPPORTED_ALGORITHM
            )
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthenticationError("token has no key id", AuthCheck.MISSING_KEY_ID)
        key = await self._keys.get_key(kid)
        if key is None:
            raise AuthenticationError("unknown signing key", AuthCheck.UNKNOWN_SIGNING_KEY)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=list(profile.algorithms),
                audience=list(profile.audiences),
                issuer=profile.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(
                f"token rejected: {type(exc).__name__}", AuthCheck.REJECTED
            ) from exc
        return self._principal_from(claims)

    def _principal_from(self, claims: dict[str, Any]) -> Principal:
        profile = self.profile
        if profile.tenant_claim and claims.get(profile.tenant_claim) != profile.tenant_id:
            raise AuthenticationError("token is from a different tenant", AuthCheck.WRONG_TENANT)
        subject = claims.get(profile.subject_claim)
        if not isinstance(subject, str) or not subject or len(subject) > 64:
            raise AuthenticationError("token has no usable subject", AuthCheck.UNUSABLE_SUBJECT)

        groups = _strings(_claim(claims, profile.groups_claim))
        external = _strings(_claim(claims, profile.roles_claim))
        # Native role names pass through; everything else must be mapped explicitly.
        roles = frozenset(
            {r for r in external if r.startswith("Zentral.")}
            | {profile.role_map[v] for v in external | groups if v in profile.role_map}
        )
        scopes = _strings(claims.get(profile.scope_claim))
        is_service = self._is_service(claims, subject, scopes)

        if is_service:
            if profile.entra_app_tokens and scopes:
                raise AuthenticationError(
                    "app token must not carry delegated scopes", AuthCheck.APP_HAS_SCOPES
                )
            if not roles:
                raise AuthenticationError(
                    "service token carries no Zentral role", AuthCheck.SERVICE_NO_ROLE
                )
        elif profile.required_scope and profile.required_scope not in scopes:
            raise AuthenticationError("required scope missing", AuthCheck.SCOPE_MISSING)

        name = claims.get(profile.name_claim) or claims.get("app_displayname") or ""
        return Principal.build(
            subject=subject,
            tenant_id=profile.tenant_id or profile.issuer,
            kind="service" if is_service else "user",
            roles=roles,
            groups=groups,
            display_name=str(name),
            email=str(claims.get(profile.email_claim) or ""),
            client_app_id=str(
                claims.get("azp") or claims.get("appid") or claims.get("client_id") or ""
            ),
        )

    def _is_service(self, claims: dict[str, Any], subject: str, scopes: frozenset[str]) -> bool:
        """Was this token issued to an application rather than a person?

        The answer affects labelling (audit ``actor_kind``, who can be an
        assignee) — never the permissions, which come from roles alone.
        """
        if self.profile.entra_app_tokens:
            return claims.get("idtyp") == "app" or not scopes
        if self.profile.required_scope and self.profile.required_scope in scopes:
            return False
        client = claims.get("azp") or claims.get("client_id") or claims.get("cid")
        return (
            claims.get("gty") == "client-credentials"  # Auth0
            or (isinstance(client, str) and client == subject)  # Okta, generic
            or str(claims.get("preferred_username", "")).startswith("service-account-")  # Keycloak
        )
