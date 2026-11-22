"""Built-in development identity provider.

A deliberately tiny OpenID Connect provider (authorization code + PKCE) so
that local development exercises exactly the same browser sign-in flow and the
same token validation as production — just without a real directory behind it.
"Signing in" means picking a persona.

**Anyone who can reach it can become an administrator.** It is only mounted
when ``ZENTRAL_AUTH_MODE=dev``. ``Settings.validate_for_runtime`` refuses that in
production unless ``ZENTRAL_DEV_AUTH_PASSWORD`` is set: the picker then asks for
that password (HTTP Basic, any user name), the direct token route is not mounted
and codes go only to this installation's own origin. It is a stopgap for a test
server, not a sign-in system: everyone with the password can be anyone.
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

from fastapi import APIRouter, Form, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict

from zentral.config import Settings
from zentral.security.devauth import PERSONAS, LocalIssuer

CLIENT_ID = "zentral-dev"
_CODE_TTL = 60
_CSS = """
:root{--bg:#f6f2ea;--card:#fffdf8;--ink:#221e19;--muted:#6f675b;--line:#e5dccc;--pine:#1f6b5a;--saffron:#d98f22}
@media (prefers-color-scheme:dark){:root{--bg:#15130f;--card:#1e1b16;--ink:#f0e9dc;--muted:#a99f8f;--line:#36312a;--pine:#7cc8ab}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:15px/1.5 "Inter Variable",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-feature-settings:"cv11","ss03";padding:24px}
main{width:min(30rem,100%);background:var(--card);border:1px solid var(--line);border-radius:20px;padding:30px;
box-shadow:0 18px 48px rgb(40 30 15 / 12%)}
.brand{display:flex;align-items:center;gap:10px;font:600 22px/1 "Fraunces Variable","Iowan Old Style",Georgia,serif;font-variation-settings:"SOFT" 100;margin-bottom:22px}
h1{font:500 26px/1.2 "Fraunces Variable","Iowan Old Style",Georgia,serif;font-variation-settings:"SOFT" 100;margin:0 0 6px}
p{margin:0 0 18px;color:var(--muted)}
.warn{background:rgb(217 143 34 / 14%);color:#93600c;padding:10px 12px;border-radius:10px;font-size:13px}
@media (prefers-color-scheme:dark){.warn{color:#f0c060}}
ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
a{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 14px;border:1px solid var(--line);
border-radius:12px;text-decoration:none;color:var(--ink);transition:border-color .15s,transform .06s}
a:hover{border-color:var(--pine)}a:active{transform:translateY(1px)}
a:focus-visible{outline:2px solid var(--saffron);outline-offset:2px}
a strong{font-weight:600}a small{color:var(--muted)}a span{color:var(--muted);font-size:13px;text-align:right}
"""

_MARK = (
    '<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">'
    '<rect width="32" height="32" rx="9" fill="#1f6b5a"/>'
    '<path d="M10 10.5h12L10 21.5h12" fill="none" stroke="#f6efe2" stroke-width="2.6" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="16" cy="16" r="2.7" fill="#e9a83a"/></svg>'
)

_ROLE_LINES = {
    "Zentral.Admin": "Sets up teams, systems and channels",
    "Zentral.Agent": "Works the queue",
    "Zentral.Viewer": "Reads tickets, personal data masked",
    "Zentral.Auditor": "Reads the audit trail only",
    "Zentral.PrivacyOfficer": "Handles access and erasure requests",
    "Zentral.Automation": "An AI agent's view: masked, cannot reply",
}


class _PersonaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: str


def _password_matches(authorization: str | None, password: str) -> bool:
    """Check an HTTP Basic header; the user name is not part of the secret."""
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(value.strip(), validate=True)
    except ValueError:
        return False
    return hmac.compare_digest(decoded.partition(b":")[2], password.encode())


def build_dev_idp(issuer: LocalIssuer, settings: Settings) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    base = issuer.issuer
    password = settings.dev_auth_password.get_secret_value()
    # Production without a password stays shut, even if validation was skipped.
    gated = settings.is_production or bool(password)
    warning = (
        "Test sign-in: everyone with the password can be anyone. Do not enter real personal data."
        if gated
        else "Development sign-in: anyone who can reach this page can "
        "be anyone. Never expose this instance to a network."
    )
    # code -> (persona, code_challenge, redirect_uri, expires_at)
    codes: dict[str, tuple[str, str, str, float]] = {}

    def check_redirect(uri: str) -> str:
        """Only this installation's own origin, or a local dev server, may receive codes."""
        target, own = urlsplit(uri), urlsplit(settings.public_base_url)
        local = (
            not settings.is_production
            and target.hostname in ("localhost", "127.0.0.1")
            and target.scheme == "http"
        )
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
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        if gated and not (password and _password_matches(authorization, password)):
            raise HTTPException(
                401,
                "password required",
                headers={"WWW-Authenticate": 'Basic realm="Zentral test sign-in", charset="UTF-8"'},
            )
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
                f'<li><a href="?{escape(urlencode({**params, "persona": name}))}">'
                f"<strong>{escape(spec['name'])} <small>({escape(name)})</small></strong>"
                f"<span>{escape(_ROLE_LINES.get(str(spec['roles'][0]), ''))}</span></a></li>"
                for name, spec in PERSONAS.items()
            )
            return HTMLResponse(
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>Sign in · Zentral</title>"
                '<link rel="stylesheet" href="../signin-fonts.css">'
                '<link rel="stylesheet" href="style.css"><main>'
                f'<div class="brand">{_MARK} Zentral</div>'
                "<h1>Who are you today?</h1>"
                "<p>Pick a person to try Zentral as. Each sees exactly what their role allows.</p>"
                f'<p class="warn">{escape(warning)}</p>'
                f"<ul>{links}</ul></main></html>"
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
            "token_type": "Bearer",  # nosec B105
            "expires_in": 8 * 3600,
        }

    if settings.is_production:
        return router

    # Convenience for scripts and tests: a token without the browser round trip.
    @router.post("/api/v1/dev/token")
    async def direct_token(data: _PersonaIn) -> dict[str, str]:
        if data.persona not in PERSONAS:
            raise HTTPException(404, "unknown persona")
        return {"access_token": issuer.issue_persona(data.persona)}

    return router
