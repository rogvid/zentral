"""Microsoft Entra ID access-token validation.

Zentral is a pure OAuth 2.0 *resource server*: it never sees user passwords,
never issues its own long-lived credentials for users, and holds no session
state. Every request must carry an Entra-issued access token that passes all
of the checks below.

Validation rules (all mandatory):

* signature verifies against the tenant's published signing keys (RS256 only —
  the algorithm is pinned, never taken from the token),
* ``iss`` is exactly this tenant's v2.0 issuer and ``tid`` matches,
* ``aud`` is this API,
* ``exp``/``nbf``/``iat`` are valid (small leeway for clock skew),
* delegated (user) tokens carry the required scope; app-only tokens are
  identified by ``idtyp=app`` (or absence of ``scp``) and need at least one role,
* a stable subject (``oid``) is present.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

import httpx
import jwt
import structlog
from jwt import PyJWK

from zentral.config import Settings
from zentral.security.principal import Principal

log = structlog.get_logger(__name__)

ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 60


class AuthenticationError(Exception):
    """Token missing or invalid. The message is safe to log, not to return to clients."""


class KeySource(Protocol):
    async def get_key(self, kid: str) -> PyJWK | None: ...


class StaticKeySource:
    """Keys supplied directly (development mode and tests)."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self._keys = {k["kid"]: PyJWK.from_dict(k) for k in jwks["keys"]}

    async def get_key(self, kid: str) -> PyJWK | None:
        return self._keys.get(kid)


class JwksKeySource:
    """Fetches and caches the tenant's JWKS.

    An unknown ``kid`` triggers a refresh (key rollover), but at most once per
    ``min_refresh_interval`` so that forged tokens with random ``kid`` values
    cannot be used to make this service hammer the identity provider.
    """

    def __init__(
        self,
        jwks_uri: str,
        http: httpx.AsyncClient,
        *,
        max_age: float = 24 * 3600,
        min_refresh_interval: float = 60,
    ) -> None:
        self._uri = jwks_uri
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

    async def _refresh(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now - self._last_attempt < self._min_refresh:
                return
            self._last_attempt = now
            try:
                resp = await self._http.get(self._uri, timeout=10)
                resp.raise_for_status()
                keys = {
                    k["kid"]: PyJWK.from_dict(k)
                    for k in resp.json()["keys"]
                    if k.get("kty") == "RSA" and k.get("use", "sig") == "sig"
                }
            except (httpx.HTTPError, KeyError, ValueError, jwt.PyJWTError) as exc:
                # Keep serving with the keys we have; never fail open.
                log.error("jwks_refresh_failed", error=type(exc).__name__)
                return
            if keys:
                self._keys = keys
                self._fetched_at = now


class TokenValidator:
    def __init__(
        self,
        *,
        issuer: str,
        audiences: list[str],
        tenant_id: str,
        required_scope: str,
        keys: KeySource,
    ) -> None:
        self._issuer = issuer
        self._audiences = audiences
        self._tenant_id = tenant_id
        self._required_scope = required_scope
        self._keys = keys

    @classmethod
    def for_entra(cls, settings: Settings, http: httpx.AsyncClient) -> TokenValidator:
        return cls(
            issuer=settings.entra_issuer,
            audiences=settings.entra_audiences,
            tenant_id=settings.entra_tenant_id,
            required_scope=settings.entra_required_scope,
            keys=JwksKeySource(settings.entra_jwks_uri, http),
        )

    async def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("malformed token") from exc
        if header.get("alg") not in ALGORITHMS:
            raise AuthenticationError("unsupported signing algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthenticationError("token has no key id")
        key = await self._keys.get_key(kid)
        if key is None:
            raise AuthenticationError("unknown signing key")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=ALGORITHMS,
                audience=self._audiences,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"token rejected: {type(exc).__name__}") from exc
        return self._principal_from(claims)

    def _principal_from(self, claims: dict[str, Any]) -> Principal:
        if claims.get("tid") != self._tenant_id:
            raise AuthenticationError("token is from a different tenant")
        subject = claims.get("oid")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("token has no object id")

        roles = _string_set(claims.get("roles"))
        scopes = set(str(claims.get("scp", "")).split())
        is_app = claims.get("idtyp") == "app" or not scopes
        if is_app:
            if scopes:
                raise AuthenticationError("app token must not carry delegated scopes")
            if not roles:
                raise AuthenticationError("app token carries no application roles")
        elif self._required_scope not in scopes:
            raise AuthenticationError("required scope missing")

        return Principal.build(
            subject=subject,
            tenant_id=self._tenant_id,
            kind="service" if is_app else "user",
            roles=roles,
            groups=_string_set(claims.get("groups")),
            display_name=str(claims.get("name") or claims.get("app_displayname") or ""),
            email=str(claims.get("preferred_username") or ""),
            client_app_id=str(claims.get("azp") or claims.get("appid") or ""),
        )


def _string_set(value: Any) -> frozenset[str]:
    if isinstance(value, list):
        return frozenset(v for v in value if isinstance(v, str))
    return frozenset()
