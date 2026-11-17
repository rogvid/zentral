"""Pre-flight checks: ``zentral doctor``.

Run it after configuring an installation and before letting people in. Each
check answers one question an operator would otherwise find out the hard way.
It never prints secret values.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from alembic.script import ScriptDirectory
from sqlalchemy import text

from zentral.ai.base import ClassificationInput
from zentral.config import ConfigurationError, Settings
from zentral.runtime import Runtime, build_runtime
from zentral.security.crypto import get_cipher


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: str  # ok | warn | fail
    detail: str


def expected_revision() -> str:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head() or ""


async def _database(rt: Runtime) -> CheckResult:
    async with rt.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        try:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        except Exception:
            return CheckResult(
                "database", "fail", "reachable, but no schema: run `zentral db upgrade`"
            )
    head = expected_revision()
    if current != head:
        return CheckResult(
            "database", "fail", f"schema is at {current}, expected {head}: run `zentral db upgrade`"
        )
    return CheckResult("database", "ok", f"{rt.engine.dialect.name}, schema {current}")


async def _encryption(rt: Runtime) -> CheckResult:
    cipher = get_cipher()
    if cipher.decrypt(cipher.encrypt("zentral")) != "zentral":
        return CheckResult("encryption", "fail", "round trip failed")
    # Can the configured keys read what is already stored?
    async with rt.engine.connect() as conn:
        try:
            sample = (await conn.execute(text("SELECT body FROM messages LIMIT 1"))).scalar()
        except Exception:
            sample = None
    if sample is not None:
        try:
            cipher.decrypt(sample)
        except Exception:
            return CheckResult(
                "encryption", "fail", "configured keys cannot decrypt existing data (wrong key?)"
            )
    return CheckResult("encryption", "ok", f"{len(rt.settings.encryption_keys)} key(s) configured")


async def _audit(rt: Runtime) -> CheckResult:
    async with rt.sessions() as session:
        try:
            result = await rt.trail.verify(session)
        except Exception as exc:
            return CheckResult("audit trail", "fail", f"could not verify: {type(exc).__name__}")
    if not result.ok:
        return CheckResult(
            "audit trail",
            "fail",
            f"chain broken at event {result.first_bad_seq} (or wrong HMAC key)",
        )
    return CheckResult("audit trail", "ok", f"{result.checked} events verified")


async def _identity(rt: Runtime) -> CheckResult:
    s = rt.settings
    if s.auth_mode == "dev":
        return CheckResult("identity provider", "warn", "development sign-in: anyone can be admin")
    url = s.issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = await rt.http.get(url, timeout=10)
        resp.raise_for_status()
        document = resp.json()
        if s.auth_mode == "oidc" and document.get("issuer") != s.issuer:
            return CheckResult(
                "identity provider", "fail", "discovery document names a different issuer"
            )
        keys = (await rt.http.get(document["jwks_uri"], timeout=10)).json()["keys"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return CheckResult("identity provider", "fail", f"{url}: {type(exc).__name__}")
    return CheckResult("identity provider", "ok", f"{s.auth_mode}: {len(keys)} signing key(s)")


async def _ai(rt: Runtime) -> CheckResult:
    s = rt.settings
    inp = ClassificationInput(
        "Printer does not print", "The printer on floor 2 is jammed.", tuple(s.categories)
    )
    try:
        result = await rt.ai.classify(inp)
    except Exception as exc:
        return CheckResult(
            "AI provider", "warn", f"{rt.ai.name} failed ({exc}); tickets fall back to rules"
        )
    where = "on-prem" if rt.ai.on_prem else "CLOUD — redacted ticket text leaves your network"
    status = "ok" if rt.ai.on_prem or not s.is_production else "warn"
    return CheckResult("AI provider", status, f"{rt.ai.model_label} ({where}) → {result.category}")


async def _connectors(rt: Runtime) -> CheckResult:
    names = rt.registry.names()
    missing = sorted(set(rt.settings.enabled_connectors) - set(names))
    if missing:
        return CheckResult("connectors", "fail", f"enabled but not installed: {', '.join(missing)}")
    return CheckResult("connectors", "ok", ", ".join(names) or "none")


def _web(rt: Runtime) -> CheckResult:
    static = rt.settings.static_dir
    if not static:
        return CheckResult("web client", "warn", "ZENTRAL_STATIC_DIR not set: API and MCP only")
    if not (Path(static) / "index.html").is_file():
        return CheckResult("web client", "fail", f"no index.html in {static}")
    return CheckResult("web client", "ok", static)


async def run_checks(settings: Settings) -> list[CheckResult]:
    try:
        rt = build_runtime(settings)
    except ConfigurationError as exc:
        return [CheckResult("configuration", "fail", str(exc).replace("\n", " "))]
    except Exception as exc:
        return [CheckResult("configuration", "fail", f"{type(exc).__name__}: {exc}")]
    results = [CheckResult("configuration", "ok", f"env={settings.env}, auth={settings.auth_mode}")]
    checks: list[tuple[str, Callable[[Runtime], Awaitable[CheckResult]]]] = [
        ("database", _database),
        ("encryption", _encryption),
        ("audit trail", _audit),
        ("identity provider", _identity),
        ("AI provider", _ai),
        ("connectors", _connectors),
    ]
    try:
        for name, check in checks:
            try:
                results.append(await check(rt))
            except Exception as exc:
                results.append(CheckResult(name, "fail", f"{type(exc).__name__}"))
        results.append(_web(rt))
    finally:
        await rt.aclose()
    return results
