"""Archiving the audit trail: signed archives, checkpoints, a guarded prune."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text

from tests.conftest import IS_POSTGRES
from tests.helpers import audit_events, make_ticket
from zentral.audit.archive import ArchiveError, prune, verify_archive, write_archive
from zentral.audit.trail import AuditTrail, PendingEvent, RequestMeta
from zentral.db.models import AuditCheckpoint, AuditEvent
from zentral.security.principal import system_principal
from zentral.services.jobs import run_audit_housekeeping

ACTOR = system_principal("cli")


async def old_events(runtime, n: int, days_ago: int) -> None:
    """Append ``n`` events dated ``days_ago`` days back (as if written then)."""
    when = datetime.now(UTC) - timedelta(days=days_ago)
    async with runtime.sessions() as session, runtime.trail.serialized(session):
        await runtime.trail.write(
            session,
            [
                PendingEvent("test.old", "test", str(i), occurred_at=when + timedelta(seconds=i))
                for i in range(n)
            ],
            ACTOR,
            RequestMeta(channel="cli"),
        )
        await session.commit()


async def verify(client, auth) -> dict:
    r = await client.post("/api/v1/audit/verify", headers=auth("auditor"))
    return r.json()


def days_ago(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


async def test_archive_then_prune_keeps_the_chain_provable(client, auth, runtime, tmp_path):
    await old_events(runtime, 5, days_ago=400)
    await make_ticket(client, auth)  # recent events stay

    async with runtime.sessions() as session:
        manifest = await write_archive(session, runtime.trail, days_ago(365), tmp_path)
    assert manifest is not None
    assert (manifest.first_seq, manifest.last_seq, manifest.events) == (1, 5, 5)
    assert manifest.data_path.exists() and manifest.path.exists()
    assert verify_archive(manifest.path, runtime.trail).events == 5

    # Archiving removes nothing.
    assert (await verify(client, auth))["archived_through"] == 0

    async with runtime.sessions() as session:
        assert await prune(session, runtime.trail, manifest.path, ACTOR) == 5
    result = await verify(client, auth)
    assert result["ok"] is True and result["archived_through"] == 5

    pruned = (await audit_events(client, auth, action="audit.pruned"))[0]
    assert pruned["details"]["events"] == 5 and pruned["details"]["last_seq"] == 5

    # Nothing older is left; a second archive continues where the first ended.
    async with runtime.sessions() as session:
        assert await write_archive(session, runtime.trail, days_ago(365), tmp_path) is None
        later = await write_archive(session, runtime.trail, days_ago(-1), tmp_path)
    assert later is not None and later.first_seq == 6
    async with runtime.sessions() as session:
        await prune(session, runtime.trail, later.path, ACTOR)
        checkpoints = await runtime.trail.checkpoints(session)
    assert [c.seq for c in checkpoints] == [5, later.last_seq]
    assert checkpoints[1].prev_signature == checkpoints[0].signature
    assert (await verify(client, auth))["ok"] is True


async def test_the_chain_continues_after_everything_was_archived(client, auth, runtime, tmp_path):
    await old_events(runtime, 3, days_ago=10)
    async with runtime.sessions() as session:
        manifest = await write_archive(session, runtime.trail, days_ago(-1), tmp_path)
        await prune(session, runtime.trail, manifest.path, ACTOR)
        # The prune itself was recorded after the checkpoint, continuing the numbering.
        seqs = list((await session.scalars(select(AuditEvent.seq))).all())
    assert seqs == [manifest.last_seq + 1]
    await make_ticket(client, auth)
    assert (await verify(client, auth))["ok"] is True


async def test_a_tampered_archive_is_rejected(runtime, tmp_path):
    await old_events(runtime, 4, days_ago=30)
    async with runtime.sessions() as session:
        manifest = await write_archive(session, runtime.trail, days_ago(1), tmp_path)

    lines = manifest.data_path.read_text().splitlines()
    row = json.loads(lines[1])
    row["actor_id"] = "someone-else"
    lines[1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    manifest.data_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ArchiveError, match="broken at seq 2"):
        verify_archive(manifest.path, runtime.trail)
    async with runtime.sessions() as session:
        with pytest.raises(ArchiveError):
            await prune(session, runtime.trail, manifest.path, ACTOR)
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 4


async def test_a_manifest_needs_our_signature(runtime, tmp_path):
    await old_events(runtime, 2, days_ago=30)
    async with runtime.sessions() as session:
        manifest = await write_archive(session, runtime.trail, days_ago(1), tmp_path)
    with pytest.raises(ArchiveError, match="signed"):
        verify_archive(manifest.path, AuditTrail("a-different-key"))
    payload = json.loads(manifest.path.read_text())
    payload["last_seq"] = 1  # claim less than was archived
    manifest.path.write_text(json.dumps(payload))
    with pytest.raises(ArchiveError, match="signed"):
        verify_archive(manifest.path, runtime.trail)


async def test_archives_are_pruned_in_order_and_never_overwritten(runtime, tmp_path):
    await old_events(runtime, 2, days_ago=30)
    async with runtime.sessions() as session:
        first = await write_archive(session, runtime.trail, days_ago(20), tmp_path)
    await old_events(runtime, 2, days_ago=10)
    async with runtime.sessions() as session:
        second = await write_archive(
            session, runtime.trail, days_ago(1), tmp_path, start_after=first.last_seq
        )
        with pytest.raises(ArchiveError, match="must start at seq 1"):
            await prune(session, runtime.trail, second.path, ACTOR)
    async with runtime.sessions() as session:
        with pytest.raises(ArchiveError, match="refusing to overwrite"):
            await write_archive(session, runtime.trail, days_ago(20), tmp_path)


async def test_archiving_refuses_a_broken_chain(runtime, tmp_path, as_db_superuser):
    await old_events(runtime, 3, days_ago=30)
    await as_db_superuser.execute(delete(AuditEvent).where(AuditEvent.seq == 2))
    await as_db_superuser.commit()
    async with runtime.sessions() as session:
        with pytest.raises(ArchiveError, match="does not verify"):
            await write_archive(session, runtime.trail, days_ago(1), tmp_path)


async def test_removing_old_events_without_a_checkpoint_is_detected(
    client, auth, runtime, as_db_superuser
):
    await old_events(runtime, 3, days_ago=30)
    await as_db_superuser.execute(delete(AuditEvent).where(AuditEvent.seq <= 2))
    await as_db_superuser.commit()
    result = await verify(client, auth)
    assert result["ok"] is False and result["first_bad_seq"] == 1


async def test_a_forged_checkpoint_is_detected(client, auth, runtime, tmp_path, as_db_superuser):
    await old_events(runtime, 3, days_ago=30)
    forger = AuditTrail("attacker-does-not-know-the-real-key")
    event = await as_db_superuser.get(AuditEvent, 2)
    checkpoint = AuditCheckpoint(
        seq=2,
        hash=event.hash,
        first_seq=1,
        events=2,
        archive_sha256="0" * 64,
        created_by="attacker",
        prev_signature="0" * 64,
        signature="",
    )
    checkpoint.signature = forger.sign(forger.checkpoint_payload(checkpoint))
    as_db_superuser.add(checkpoint)
    await as_db_superuser.execute(delete(AuditEvent).where(AuditEvent.seq <= 2))
    await as_db_superuser.commit()
    result = await verify(client, auth)
    assert result["ok"] is False and result["first_bad_seq"] == 1


@pytest.mark.skipif(not IS_POSTGRES, reason="database-level guard exists on PostgreSQL only")
async def test_the_database_allows_only_the_declared_prune(runtime, tmp_path, db):
    from sqlalchemy.exc import DBAPIError

    await old_events(runtime, 4, days_ago=30)
    async with runtime.sessions() as session:
        manifest = await write_archive(session, runtime.trail, days_ago(1), tmp_path)
        await prune(session, runtime.trail, manifest.path, ACTOR)
    await old_events(runtime, 2, days_ago=0)

    attempts = [
        "DELETE FROM audit_events",  # nothing declared
        "SELECT set_config('zentral.audit_prune', '999', true); DELETE FROM audit_events",
        "UPDATE audit_events SET actor_id = 'x'",
        "DELETE FROM audit_checkpoints",
        "UPDATE audit_checkpoints SET hash = 'x'",
        "TRUNCATE audit_checkpoints",
    ]
    for statement in attempts:
        *setup, attempt = statement.split("; ")
        for part in setup:
            await db.execute(text(part))
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text(attempt))
        await db.rollback()


async def test_the_worker_archives_daily_and_prunes_after_the_keep_period(
    client, auth, runtime, tmp_path
):
    await old_events(runtime, 3, days_ago=100)
    await old_events(runtime, 2, days_ago=5)
    async with runtime.sessions() as session:
        archived, pruned = await run_audit_housekeeping(
            session, runtime.trail, tmp_path, 30, system_principal("worker")
        )
    # One archive of everything before today; too young to prune as a whole.
    assert (archived, pruned) == (5, 0)
    async with runtime.sessions() as session:
        archived, pruned = await run_audit_housekeeping(
            session, runtime.trail, tmp_path, 3, system_principal("worker")
        )
    assert (archived, pruned) == (0, 5)
    assert len(list(tmp_path.glob("*.manifest.json"))) == 1
    assert (await verify(client, auth))["archived_through"] == 5


def test_keeping_days_needs_an_archive_directory():
    from tests.conftest import make_settings
    from zentral.config import ConfigurationError

    with pytest.raises(ConfigurationError, match="AUDIT_KEEP_DAYS"):
        make_settings(audit_keep_days=30).validate_for_runtime()
