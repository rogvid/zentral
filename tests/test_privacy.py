from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, text

from tests.helpers import audit_events, make_ticket
from zentral.db.models import Contact, Job, Ticket, utcnow

JANE = "jane.doe@example.com"


async def seed(client, auth):
    a = await make_ticket(
        client,
        auth,
        requester_email=JANE,
        requester_name="Jane Doe",
        subject="Payslip for Jane",
        body="My salary is wrong",
    )
    b = await make_ticket(
        client, auth, requester_email=JANE, subject="Second", body="Another thing"
    )
    other = await make_ticket(
        client, auth, requester_email="bob@example.com", subject="Bob's", body="Bob text"
    )
    h = auth("admin")
    await client.post(
        f"/api/v1/tickets/{a['id']}/messages",
        headers=h,
        json={"kind": "note", "body": "internal: Jane is upset"},
    )
    await client.post(
        f"/api/v1/tickets/{a['id']}/messages",
        headers=h,
        json={"kind": "outbound", "body": "Dear Jane, fixed."},
    )
    return a, b, other


async def lookup(client, auth, email=JANE):
    return await client.post(
        "/api/v1/privacy/contacts/lookup", headers=auth("privacy"), json={"email": email}
    )


async def test_subject_access_export(client, auth):
    await seed(client, auth)
    contact = (await lookup(client, auth)).json()
    assert (contact["email"], contact["ticket_count"]) == (JANE, 2)
    r = await client.get(
        f"/api/v1/privacy/contacts/{contact['id']}/export", headers=auth("privacy")
    )
    export = r.json()
    assert export["data_subject"]["display_name"] == "Jane Doe"
    assert [t["subject"] for t in export["tickets"]] == ["Payslip for Jane", "Second"]
    bodies = [m["body"] for t in export["tickets"] for m in t["messages"]]
    assert "My salary is wrong" in bodies and "Dear Jane, fixed." in bodies
    assert not any("internal:" in b for b in bodies)  # staff notes are excluded
    assert "Bob" not in r.text  # nobody else's data
    actions = {e["action"] for e in await audit_events(client, auth)}
    assert {"privacy.contact_lookup", "privacy.exported"} <= actions


async def test_lookup_miss_is_audited_without_the_address(client, auth):
    assert (await lookup(client, auth, "nobody@example.com")).status_code == 404
    events = await audit_events(client, auth, action="privacy.contact_lookup")
    assert events[0]["details"] == {"found": False}
    assert "nobody@example.com" not in str(events)


async def test_erasure_removes_every_trace(client, auth, runtime, db):
    a, b, other = await seed(client, auth)
    contact = (await lookup(client, auth)).json()
    r = await client.post(
        f"/api/v1/privacy/contacts/{contact['id']}/erase", headers=auth("privacy")
    )
    assert r.json() == {"contact_id": contact["id"], "tickets_scrubbed": 2, "messages_scrubbed": 4}

    # Gone for lookups, gone from the tickets, gone from the conversation.
    assert (await lookup(client, auth)).status_code == 404
    for ticket in (a, b):
        t = (await client.get(f"/api/v1/tickets/{ticket['id']}", headers=auth("admin"))).json()
        assert (t["subject"], t["requester"], t["status"]) == ("[erased]", None, "closed")
        msgs = (
            await client.get(f"/api/v1/tickets/{ticket['id']}/messages", headers=auth("admin"))
        ).json()
        assert {m["body"] for m in msgs} == {"[erased]"}
    # Other people's data is untouched.
    t = (await client.get(f"/api/v1/tickets/{other['id']}", headers=auth("admin"))).json()
    assert t["subject"] == "Bob's" and t["requester"]["email"] == "bob@example.com"

    stored = await db.get(Contact, __import__("uuid").UUID(contact["id"]))
    assert (stored.email, stored.display_name, stored.identity_hash) == (None, None, None)
    assert stored.erased_at is not None

    # Nothing decryptable is left behind: decrypt every ciphertext in the database.
    from zentral.security.crypto import get_cipher

    async with runtime.engine.connect() as conn:
        for table, column in (
            ("messages", "body"),
            ("tickets", "subject"),
            ("tickets", "ai_summary"),
            ("contacts", "email"),
            ("contacts", "display_name"),
        ):
            rows = (
                await conn.execute(text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"))
            ).scalars()
            for value in rows.all():
                assert "Jane" not in get_cipher().decrypt(
                    value
                ) and "salary" not in get_cipher().decrypt(value)

    # The erasure is provable, and the scrubbed ticket cannot receive new content.
    event = (await audit_events(client, auth, action="privacy.erased"))[0]
    assert event["details"] == {"tickets": 2, "messages": 4}
    r = await client.post(
        f"/api/v1/tickets/{a['id']}/messages",
        headers=auth("admin"),
        json={"kind": "note", "body": "x"},
    )
    assert r.status_code == 422
    assert (await client.post("/api/v1/audit/verify", headers=auth("auditor"))).json()["ok"] is True


async def test_same_address_after_erasure_is_a_new_person(client, auth):
    await seed(client, auth)
    old = (await lookup(client, auth)).json()
    await client.post(f"/api/v1/privacy/contacts/{old['id']}/erase", headers=auth("privacy"))
    await make_ticket(client, auth, requester_email=JANE)
    new = (await lookup(client, auth)).json()
    assert new["id"] != old["id"] and new["ticket_count"] == 1


async def test_retention_sweep(client, auth, worker, db, runtime):
    a, b, other = await seed(client, auth)
    await worker.run_once()
    h = auth("admin")
    for ticket in (a, b):
        await client.patch(f"/api/v1/tickets/{ticket['id']}", headers=h, json={"status": "closed"})
    await client.patch(f"/api/v1/tickets/{other['id']}", headers=h, json={"status": "closed"})
    old = utcnow() - timedelta(days=runtime.settings.retention_days + 1)
    for ticket in (a, b):
        stored = await db.get(Ticket, ticket["id"])
        stored.resolved_at = old
    await db.commit()

    db.add(Job(kind="retention_sweep", payload={}))
    await db.commit()
    await worker.run_once()

    for ticket in (a, b):
        t = (await client.get(f"/api/v1/tickets/{ticket['id']}", headers=h)).json()
        assert t["subject"] == "[erased]"
    # Recently closed ticket is kept; Jane (no tickets left) is forgotten, Bob is not.
    assert (await client.get(f"/api/v1/tickets/{other['id']}", headers=h)).json()[
        "subject"
    ] == "Bob's"
    assert (await lookup(client, auth)).status_code == 404
    assert (await lookup(client, auth, "bob@example.com")).status_code == 200
    assert len(await audit_events(client, auth, action="retention.ticket_scrubbed")) == 2
    assert len(await audit_events(client, auth, action="retention.contact_erased")) == 1
    contacts = (await db.scalars(select(Contact).execution_options(populate_existing=True))).all()
    assert sum(c.erased_at is not None for c in contacts) == 1
