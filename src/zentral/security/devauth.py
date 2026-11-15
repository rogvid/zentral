"""Local token issuer for development and tests.

Issues tokens shaped exactly like Entra ID v2.0 access tokens, signed with an
RSA key generated in memory at start-up, so that development and tests run
through the *same* ``TokenValidator`` as production. It cannot be enabled in
production (``Settings.validate_for_runtime``).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from zentral.security.entra import StaticKeySource, TokenValidator
from zentral.security.rbac import Role

DEV_TENANT = "00000000-0000-4000-8000-00000000d3e0"
DEV_AUDIENCE = "api://zentral-dev"
DEV_SCOPE = "access_as_user"

PERSONAS: dict[str, dict[str, Any]] = {
    "admin": {"name": "Ada Admin", "roles": [Role.ADMIN]},
    "agent": {"name": "Arne Agent", "roles": [Role.AGENT]},
    "viewer": {"name": "Vera Viewer", "roles": [Role.VIEWER]},
    "auditor": {"name": "Aud Auditor", "roles": [Role.AUDITOR]},
    "privacy": {"name": "Pia Privacy", "roles": [Role.PRIVACY_OFFICER]},
    "automation": {"name": "Triage Bot", "roles": [Role.AUTOMATION], "app": True},
}


class LocalIssuer:
    def __init__(self, tenant_id: str = DEV_TENANT, audience: str = DEV_AUDIENCE) -> None:
        self.tenant_id = tenant_id
        self.audience = audience
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.kid = uuid.uuid4().hex
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def validator(self) -> TokenValidator:
        return TokenValidator(
            issuer=self.issuer,
            audiences=[self.audience],
            tenant_id=self.tenant_id,
            required_scope=DEV_SCOPE,
            keys=StaticKeySource(self.jwks()),
        )

    def issue(
        self,
        *,
        subject: str,
        roles: list[str],
        name: str = "",
        email: str = "",
        app: bool = False,
        groups: list[str] | None = None,
        lifetime: int = 3600,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "tid": self.tenant_id,
            "oid": subject,
            "sub": subject,
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
            "roles": [str(r) for r in roles],
            "azp": "zentral-dev-client",
            "ver": "2.0",
        }
        if app:
            claims["idtyp"] = "app"
            claims["app_displayname"] = name
        else:
            claims.update({"scp": DEV_SCOPE, "name": name, "preferred_username": email})
        if groups:
            claims["groups"] = groups
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.kid})

    def issue_persona(self, persona: str) -> str:
        spec = PERSONAS[persona]
        subject = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{persona}.zentral.dev"))
        return self.issue(
            subject=subject,
            roles=spec["roles"],
            name=spec["name"],
            email=f"{persona}@zentral.dev",
            app=bool(spec.get("app")),
        )
