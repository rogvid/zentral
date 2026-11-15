from __future__ import annotations

import pytest
from sqlalchemy import delete, select, update

from tests.conftest import IS_POSTGRES, make_settings
from tests.helpers import audit_events, make_ticket
from zentral.audit.trail import AuditTrail
from zentral.db.models import AuditEvent


async def verify(client, auth) -> dict:
    r = await client.post("/api/v1/audit/verify", headers=auth("auditor"))
    assert r.status_code == 200
    return r.json()


async def seed(client, auth, n=4):
    for _ in range(n):
        await make_ticket(client, auth)


async def test_chain_verifies_and_is_contiguous(client, auth, db):
    await seed(client, auth)
    result = await verify(client, auth)
    assert result["ok"] is True and result["checked"] >= 4
    seqs = list((await db.scalars(select(AuditEvent.seq).order_by(AuditEvent.seq))).all())
    assert seqs == list(range(1, len(seqs) + 1))


async def test_modified_event_is_detected(client, auth, as_db_superuser):
    db = as_db_superuser
    await seed(client, auth)
    await db.execute(update(AuditEvent).where(AuditEvent.seq == 2).values(actor_id="someone-else"))
    await db.commit()
    assert await verify(client, auth) == {"ok": False, "checked": 1, "first_bad_seq": 2}


async def test_modified_details_are_detected(client, auth, as_db_superuser):
    db = as_db_superuser
    await seed(client, auth)
    await db.execute(update(AuditEvent).where(AuditEvent.seq == 3).values(details={"forged": True}))
    await db.commit()
    assert (await verify(client, auth))["first_bad_seq"] == 3


async def test_deleted_event_is_detected(client, auth, as_db_superuser):
    db = as_db_superuser
    await seed(client, auth)
    await db.execute(delete(AuditEvent).where(AuditEvent.seq == 2))
    await db.commit()
    assert (await verify(client, auth))["first_bad_seq"] == 2


async def test_truncated_tail_then_rewrite_needs_the_key(client, auth, as_db_superuser, runtime):
    db = as_db_superuser
    """An attacker with DB access but without the HMAC key cannot re-chain events."""
    await seed(client, auth)
    forger = AuditTrail("attacker-does-not-know-the-real-key")
    event = await db.get(AuditEvent, 2)
    event.action = "nothing.happened"
    event.hash = forger._digest(event.prev_hash, event)
    await db.commit()
    assert (await verify(client, auth))["first_bad_seq"] == 2
    assert (
        make_settings().audit_hmac_key.get_secret_value() != "attacker-does-not-know-the-real-key"
    )


async def test_audit_api_filters_and_paginates(client, auth):
    await seed(client, auth, 6)
    first = await client.get("/api/v1/audit/events", headers=auth("auditor"), params={"limit": 3})
    body = first.json()
    assert len(body["items"]) == 3 and body["next_before_seq"]
    second = await client.get(
        "/api/v1/audit/events",
        headers=auth("auditor"),
        params={"limit": 3, "before_seq": body["next_before_seq"]},
    )
    assert second.json()["items"][0]["seq"] < body["items"][-1]["seq"]
    created = await audit_events(client, auth, action="ticket.created")
    assert len(created) == 6 and all(e["channel"] == "api" for e in created)
    assert all(len(e["request_id"]) == 32 for e in created)


async def test_reading_the_audit_trail_is_audited(client, auth):
    await audit_events(client, auth)
    assert (await audit_events(client, auth, action="audit.read"))[0]["actor_kind"] == "user"


async def test_action_and_audit_commit_atomically(client, auth, db, runtime, monkeypatch):
    """If the audit write fails, the business change must not be committed."""

    async def boom(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(runtime.trail, "write", boom)
    client._transport.raise_app_exceptions = False
    r = await client.post(
        "/api/v1/tickets", headers=auth("admin"), json={"subject": "s", "body": "b"}
    )
    assert r.status_code == 500
    assert r.json()["detail"] == "internal error" and "audit store" not in r.text
    from zentral.db.models import Ticket

    assert (await db.scalars(select(Ticket))).all() == []


@pytest.mark.skipif(not IS_POSTGRES, reason="database-level guard exists on PostgreSQL only")
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET actor_id = 'x' WHERE seq = 1",
        "DELETE FROM audit_events WHERE seq = 1",
        "TRUNCATE audit_events",
    ],
)
async def test_database_refuses_to_rewrite_history(client, auth, db, statement):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    await seed(client, auth, 1)
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text(statement))
    await db.rollback()
    assert (await verify(client, auth))["ok"] is True
