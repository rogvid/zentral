"""Command line interface: ``zentral --help``."""

from __future__ import annotations

import asyncio
import secrets
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from sqlalchemy import text

from zentral.config import get_settings

if TYPE_CHECKING:
    from alembic.config import Config as AlembicConfig

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Zentral service desk")
db_app = typer.Typer(no_args_is_help=True, help="Database schema management")
audit_app = typer.Typer(no_args_is_help=True, help="Audit trail tools")
keys_app = typer.Typer(no_args_is_help=True, help="Key management")
app.add_typer(db_app, name="db")
app.add_typer(audit_app, name="audit")
app.add_typer(keys_app, name="keys")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    proxy_headers: bool = typer.Option(
        False, help="Trust X-Forwarded-* from the addresses in --forwarded-allow-ips."
    ),
    forwarded_allow_ips: str = "127.0.0.1",
) -> None:
    """Run the API, MCP endpoint and web client."""
    import uvicorn

    get_settings().validate_for_runtime()
    uvicorn.run(
        "zentral.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips,
        server_header=False,
        access_log=False,  # Zentral writes its own access log without query strings
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Run the background worker (classification, polling, delivery, retention)."""
    from zentral.logging import configure_logging
    from zentral.runtime import build_runtime
    from zentral.services.jobs import Worker

    async def main() -> None:
        settings = get_settings()
        configure_logging(settings.log_level)
        runtime = build_runtime(settings)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            await Worker(runtime).run_forever(stop)
        finally:
            await runtime.aclose()

    asyncio.run(main())


def _alembic_config() -> AlembicConfig:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return config


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply database migrations."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    typer.echo(f"database is at {revision}")


@db_app.command("current")
def db_current() -> None:
    """Show the current schema revision."""
    from alembic import command

    command.current(_alembic_config())


@keys_app.command("generate")
def keys_generate() -> None:
    """Print a fresh set of keys as environment variables. Store them in a secret manager."""
    from zentral.security.crypto import generate_key

    typer.echo(f'ZENTRAL_ENCRYPTION_KEYS=["{generate_key()}"]')
    typer.echo(f"ZENTRAL_BLIND_INDEX_KEY={secrets.token_urlsafe(48)}")
    typer.echo(f"ZENTRAL_AUDIT_HMAC_KEY={secrets.token_urlsafe(48)}")


@keys_app.command("rotate")
def keys_rotate(batch: int = 500) -> None:
    """Re-encrypt all stored data under the first key in ZENTRAL_ENCRYPTION_KEYS.

    Run after prepending a new key. When it finishes, the old key can be removed.
    """
    from zentral.runtime import build_runtime
    from zentral.security.crypto import get_cipher

    columns = [
        ("tickets", "id", "subject"),
        ("tickets", "id", "ai_summary"),
        ("messages", "id", "body"),
        ("contacts", "id", "email"),
        ("contacts", "id", "display_name"),
        ("ingest_sources", "id", "secrets"),
    ]

    async def main() -> int:
        runtime = build_runtime(get_settings())
        cipher = get_cipher()
        total = 0
        try:
            async with runtime.engine.begin() as conn:
                for table, pk, column in columns:
                    # Identifiers are compile-time constants from the list above.
                    select = text(f"SELECT {pk}, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
                    update = text(f"UPDATE {table} SET {column} = :v WHERE {pk} = :k")  # noqa: S608
                    rows = (await conn.execute(select)).all()
                    for start in range(0, len(rows), batch):
                        params = [
                            {"k": k, "v": cipher.rotate(v)} for k, v in rows[start : start + batch]
                        ]
                        await conn.execute(update, params)
                        total += len(params)
        finally:
            await runtime.aclose()
        return total

    typer.echo(f"re-encrypted {asyncio.run(main())} values")


@audit_app.command("verify")
def audit_verify() -> None:
    """Verify the audit hash chain. Exit code 1 if tampering is detected."""
    from zentral.runtime import build_runtime

    async def main() -> bool:
        runtime = build_runtime(get_settings())
        try:
            async with runtime.sessions() as session:
                result = await runtime.trail.verify(session)
        finally:
            await runtime.aclose()
        if result.ok:
            typer.echo(f"OK: {result.checked} events verified")
        else:
            typer.echo(f"TAMPERING DETECTED at seq {result.first_bad_seq}", err=True)
        return result.ok

    if not asyncio.run(main()):
        raise typer.Exit(1)


@audit_app.command("export")
def audit_export(output: Path, after_seq: int = 0) -> None:
    """Export audit events as JSON Lines (for archival or a SIEM)."""
    import json

    from sqlalchemy import select

    from zentral.db.models import AuditEvent
    from zentral.runtime import build_runtime
    from zentral.schemas import AuditEventOut

    async def main() -> int:
        runtime = build_runtime(get_settings())
        count = 0
        try:
            async with runtime.sessions() as session:
                with output.open("w", encoding="utf-8") as fh:
                    stream = await session.stream_scalars(
                        select(AuditEvent)
                        .where(AuditEvent.seq > after_seq)
                        .order_by(AuditEvent.seq)
                    )
                    async for event in stream:
                        row = AuditEventOut.model_validate(event).model_dump(mode="json")
                        row["prev_hash"] = event.prev_hash
                        fh.write(json.dumps(row, sort_keys=True) + "\n")
                        count += 1
        finally:
            await runtime.aclose()
        return count

    typer.echo(f"exported {asyncio.run(main())} events to {output}")


if __name__ == "__main__":
    app()
