"""Built-in development identity provider.

A deliberately tiny OpenID Connect provider (authorization code + PKCE) so
that local development exercises exactly the same browser sign-in flow and the
same token validation as production — just without a real directory behind it.
"Signing in" means picking a persona.

**Anyone who can reach it can become an administrator.** It is only mounted
when ``ZENTRAL_AUTH_MODE=dev``, which ``Settings.validate_for_runtime`` refuses
in production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from html import escape
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict

from zentral.config import Settings
from zentral.security.devauth import PERSONAS, LocalIssuer

CLIENT_ID = "zentral-dev"
_CODE_TTL = 60
_CSS = (
    "body{font:15px system-ui,sans-serif;max-width:26rem;margin:12vh auto;padding:0 1rem}"
    "a{display:block;padding:.7rem 1rem;margin:.4rem 0;border:1px solid #ccd;border-radius:8px;"
    "text-decoration:none;color:#223}a:hover{background:#eef}"
    ".warn{background:#fff4d6;color:#7a5200;padding:.6rem .8rem;border-radius:8px}"
)


class _PersonaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: str


def build_dev_idp(issuer: LocalIssuer, settings: Settings) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    base = issuer.issuer
    # code -> (persona, code_challenge, redirect_uri, expires_at)
    codes: dict[str, tuple[str, str, str, float]] = {}

    def check_redirect(uri: str) -> str:
        """Only this installation's own origin, or a local dev server, may receive codes."""
        target, own = urlsplit(uri), urlsplit(settings.public_base_url)
        local = target.hostname in ("localhost", "127.0.0.1") and target.scheme == "http"
        same = (target.scheme, target.netloc) == (own.scheme, own.netloc)
        if not (local or same) or target.fragment:
            raise HTTPException(400, "redirect_uri is not allowed")
        return uri

    @router.get("/dev-idp/.well-known/openid-configuration")
    async def discovery() -> dict[str, Any]:
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "jwks_uri": f"{base}/keys",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "subject_types_supported": ["public"],
        }

    @router.get("/dev-idp/keys")
    async def keys() -> dict[str, Any]:
        return issuer.jwks()

    @router.get("/dev-idp/style.css")
    async def style() -> Response:
        return Response(_CSS, media_type="text/css")

    @router.get("/dev-idp/authorize")
    async def authorize(
        redirect_uri: str,
        code_challenge: Annotated[str, Query(min_length=43, max_length=128)],
        state: Annotated[str, Query(max_length=512)] = "",
        response_type: str = "code",
        code_challenge_method: str = "S256",
        client_id: str = CLIENT_ID,
        persona: str | None = None,
    ) -> Response:
        check_redirect(redirect_uri)
        if response_type != "code" or code_challenge_method != "S256" or client_id != CLIENT_ID:
            raise HTTPException(400, "unsupported authorization request")
        if persona is None:
            params = {
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "state": state,
            }
            links = "".join(
                f'<a href="?{escape(urlencode({**params, "persona": name}))}">'
                f"{escape(spec['name'])} <small>({escape(name)})</small></a>"
                for name, spec in PERSONAS.items()
            )
            return HTMLResponse(
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                "<title>Zentral development sign-in</title>"
                '<link rel="stylesheet" href="style.css"><h1>Sign in</h1>'
                '<p class="warn">Development identity provider. Sign-in is simulated — '
                "never expose this instance to a network.</p>" + links + "</html>"
            )
        if persona not in PERSONAS:
            raise HTTPException(400, "unknown persona")
        now = time.monotonic()
        for stale in [c for c, entry in codes.items() if entry[3] < now]:
            del codes[stale]
        code = secrets.token_urlsafe(32)
        codes[code] = (persona, code_challenge, redirect_uri, now + _CODE_TTL)
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            f"{redirect_uri}{separator}{urlencode({'code': code, 'state': state})}", 302
        )

    @router.post("/dev-idp/token")
    async def token(
        grant_type: Annotated[str, Form()],
        code: Annotated[str, Form()],
        code_verifier: Annotated[str, Form(min_length=43, max_length=128)],
        redirect_uri: Annotated[str, Form()],
    ) -> dict[str, Any]:
        entry = codes.pop(code, None)  # single use
        if grant_type != "authorization_code" or entry is None:
            raise HTTPException(400, "invalid_grant")
        persona, challenge, bound_redirect, expires_at = entry
        digest = hashlib.sha256(code_verifier.encode("ascii", "ignore")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if (
            expires_at < time.monotonic()
            or redirect_uri != bound_redirect
            or not hmac.compare_digest(expected, challenge)
        ):
            raise HTTPException(400, "invalid_grant")
        return {
            "access_token": issuer.issue_persona(persona),
            "token_type": "Bearer",
            "expires_in": 8 * 3600,
        }

    # Convenience for scripts and tests: a token without the browser round trip.
    @router.post("/api/v1/dev/token")
    async def direct_token(data: _PersonaIn) -> dict[str, str]:
        if data.persona not in PERSONAS:
            raise HTTPException(404, "unknown persona")
        return {"access_token": issuer.issue_persona(data.persona)}

    return router
