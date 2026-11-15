"""Migrations, key rotation and CLI tooling."""

from __future__ import annotations

import json

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from zentral.cli import app
from zentral.config import get_settings
from zentral.db.models import Base


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    db = tmp_path / "ops.db"
    key = Fernet.generate_key().decode()
    env = {
        "ZENTRAL_ENV": "test",
        "ZENTRAL_AUTH_MODE": "dev",
        "ZENTRAL_DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        "ZENTRAL_ENCRYPTION_KEYS": json.dumps([key]),
        "ZENTRAL_BLIND_INDEX_KEY": "b" * 40,
        "ZENTRAL_AUDIT_HMAC_KEY": "a" * 40,
        "ZENTRAL_LOG_LEVEL": "ERROR",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield {"db": db, "key": key, "set": monkeypatch.setenv}
    get_settings.cache_clear()


def test_migrations_match_models(cli_env):
    """`zentral db upgrade` must produce exactly the schema the models describe."""
    runner = CliRunner()
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0, result.output
    engine = create_engine(f"sqlite:///{cli_env['db']}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_key_rotation_and_audit_cli(cli_env, tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0

    import asyncio

    from zentral.db.models import Message, Ticket
    from zentral.runtime import build_runtime

    async def seed():
        rt = build_runtime(get_settings())
        async with rt.sessions() as session:
            ctx = rt.system_ctx(session, "test", "cli")
            ticket = Ticket(subject="Top secret subject", channel="manual", tags=[])
            session.add(ticket)
            await session.flush()
            session.add(Message(ticket_id=ticket.id, kind="note", body="Top secret body"))
            ctx.audit("ticket.created", "ticket", ticket.id)
            await ctx.commit()
        await rt.aclose()

    asyncio.run(seed())

    # Rotate: prepend a new key, re-encrypt, then drop the old key entirely.
    new_key = Fernet.generate_key().decode()
    cli_env["set"]("ZENTRAL_ENCRYPTION_KEYS", f"{new_key}, {cli_env['key']}")
    get_settings.cache_clear()
    result = runner.invoke(app, ["keys", "rotate"])
    assert result.exit_code == 0 and "re-encrypted 2 values" in result.output

    cli_env["set"]("ZENTRAL_ENCRYPTION_KEYS", new_key)
    get_settings.cache_clear()

    async def read():
        rt = build_runtime(get_settings())
        async with rt.sessions() as session:
            ticket = await session.get(Ticket, 1)
            subject = ticket.subject
        await rt.aclose()
        return subject

    assert asyncio.run(read()) == "Top secret subject"

    # Audit tooling.
    result = runner.invoke(app, ["audit", "verify"])
    assert result.exit_code == 0 and "OK: 1 events verified" in result.output
    out = tmp_path / "audit.jsonl"
    assert runner.invoke(app, ["audit", "export", str(out)]).exit_code == 0
    (line,) = out.read_text().splitlines()
    assert json.loads(line)["action"] == "ticket.created"

    engine = create_engine(f"sqlite:///{cli_env['db']}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_events SET action = 'nothing.happened'"))
    result = runner.invoke(app, ["audit", "verify"])
    assert result.exit_code == 1 and "TAMPERING DETECTED at seq 1" in result.output


def test_generated_keys_are_usable(cli_env):
    out = CliRunner().invoke(app, ["keys", "generate"]).output
    values = dict(line.split("=", 1) for line in out.strip().splitlines())
    Fernet(values["ZENTRAL_ENCRYPTION_KEYS"].encode())
    assert (
        len(values["ZENTRAL_BLIND_INDEX_KEY"]) >= 32 and len(values["ZENTRAL_AUDIT_HMAC_KEY"]) >= 32
    )
