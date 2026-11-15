"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from zentral import __version__
from zentral.api.deps import request_meta
from zentral.api.middleware import HardeningMiddleware
from zentral.api.ratelimit import RateLimiter
from zentral.api.routes import admin, compliance, hooks, meta, tickets
from zentral.config import Settings, get_settings
from zentral.logging import configure_logging
from zentral.mcp_server import build_mcp, mcp_asgi_app
from zentral.runtime import Runtime, build_runtime
from zentral.security.devauth import PERSONAS, LocalIssuer
from zentral.security.entra import TokenValidator
from zentral.services.context import record_denied
from zentral.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

log = structlog.get_logger(__name__)


class _PersonaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: str


def create_app(
    settings: Settings | None = None,
    *,
    runtime: Runtime | None = None,
    validator: TokenValidator | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    runtime = runtime or build_runtime(settings)
    limiter = RateLimiter()

    dev_issuer: LocalIssuer | None = None
    if validator is None:
        if settings.auth_mode == "dev":
            dev_issuer = LocalIssuer()
            validator = dev_issuer.validator()
            log.warning("dev_auth_enabled", note="anyone can obtain a token; never expose this")
        else:
            validator = TokenValidator.for_entra(settings, runtime.http)

    mcp = build_mcp(runtime, validator, limiter)
    mcp_app = mcp_asgi_app(mcp, runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield
        await runtime.aclose()

    app = FastAPI(
        title="Zentral",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.runtime = runtime
    app.state.validator = validator
    app.state.limiter = limiter
    app.state.dev_personas = list(PERSONAS) if dev_issuer else []

    # --- middleware (outermost last) ----------------------------------------------
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            max_age=600,
        )
    hosts = sorted({urlsplit(settings.public_base_url).hostname or "", *settings.allowed_hosts} - {""})
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(
        HardeningMiddleware, max_body_bytes=settings.max_body_bytes, hsts=settings.is_production
    )

    # --- routes -----------------------------------------------------------------------
    app.include_router(meta.router)
    for router in (tickets.router, admin.router, compliance.router, hooks.router):
        app.include_router(router, prefix="/api/v1")

    if dev_issuer is not None:
        issuer = dev_issuer

        @app.post("/api/v1/dev/token", include_in_schema=False)
        async def dev_token(data: _PersonaIn) -> dict[str, str]:
            if data.persona not in PERSONAS:
                raise HTTPException(404, "unknown persona")
            return {"access_token": issuer.issue_persona(data.persona)}

    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    async def protected_resource_metadata() -> dict[str, Any]:
        """RFC 9728 metadata so MCP clients can discover the authorization server."""
        base = settings.public_base_url.rstrip("/")
        return {
            "resource": f"{base}/mcp",
            "authorization_servers": [settings.entra_issuer],
            "scopes_supported": [f"api://{settings.entra_api_client_id}/{settings.entra_required_scope}"],
            "bearer_methods_supported": ["header"],
        }

    app.mount("/mcp", mcp_app)

    # --- error handling ------------------------------------------------------------------
    @app.exception_handler(PermissionDenied)
    async def _denied(request: Request, exc: PermissionDenied) -> JSONResponse:
        principal = getattr(request.state, "principal", None)
        if principal is not None:
            await record_denied(
                runtime.sessions, runtime.trail, settings, principal, request_meta(request), exc
            )
        if exc.permission == "ticket:scope":
            # Outside the caller's teams: indistinguishable from "does not exist".
            return JSONResponse({"detail": "ticket not found"}, status_code=404)
        return JSONResponse({"detail": "forbidden", "missing_permission": exc.permission}, 403)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValidationFailed)
    async def _invalid(_: Request, exc: ValidationFailed) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo submitted values back: they may be personal data.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error=type(exc).__name__, exc_info=exc)
        return JSONResponse(
            {"detail": "internal error", "request_id": getattr(request.state, "request_id", "")},
            status_code=500,
        )

    # --- web client -------------------------------------------------------------------------
    static_dir = Path(settings.static_dir) if settings.static_dir else None
    if static_dir and (static_dir / "index.html").is_file():
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith(("api/", "mcp", ".well-known/")):
                raise HTTPException(404)
            candidate = (root / path).resolve()
            # Path traversal guard: only files inside the static root are served.
            if path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
