from __future__ import annotations

from tests.conftest import SUBJECTS
from tests.helpers import audit_events, make_team, make_ticket

PII_BODY = "Hi, I am locked out. Call me on +45 12 34 56 78 or mail jane.doe@example.com."


async def test_create_validates_input(client, auth):
    h = auth("agent")
    for bad in (
        {"subject": "", "body": "b"},
        {"subject": "s"},
        {"subject": "s", "body": "b", "category": "not-a-category"},
        {"subject": "s", "body": "b", "priority": "apocalyptic"},
        {"subject": "s", "body": "b", "is_admin": True},  # unknown fields are rejected
        {"subject": "s", "body": "b", "team_id": "00000000-0000-4000-8000-000000000000"},
        {"subject": "s" * 201, "body": "b"},
        {"subject": "s", "body": "b", "tags": ["<script>"]},
    ):
        r = await client.post("/api/v1/tickets", headers=h, json=bad)
        assert r.status_code == 422, bad


async def test_validation_errors_do_not_echo_input(client, auth):
    r = await client.post(
        "/api/v1/tickets", headers=auth("agent"), json={"subject": "", "body": "my secret text"}
    )
    assert "my secret text" not in r.text


async def test_update_lifecycle(client, auth):
    ticket = await make_ticket(client, auth, "agent")
    url = f"/api/v1/tickets/{ticket['id']}"
    h = auth("agent")
    r = await client.patch(url, headers=h, json={"assignee_id": SUBJECTS["agent"]})
    assert r.json()["status"] == "open"  # taking a new ticket opens it
    assert r.json()["assignee_name"] == "Agent"
    r = await client.patch(url, headers=h, json={"status": "resolved", "tags": ["b", "a", "a"]})
    assert r.json()["resolved_at"] is not None
    assert r.json()["tags"] == ["a", "b"]
    r = await client.patch(url, headers=h, json={"status": "open"})
    assert r.json()["resolved_at"] is None
    r = await client.patch(url, headers=h, json={"assignee_id": "nobody"})
    assert r.status_code == 422
    r = await client.patch(url, headers=h, json={"category": "hardware", "priority": "high"})
    assert (r.json()["category"], r.json()["priority"]) == ("hardware", "high")

    changes = [
        e["details"]["changes"] for e in await audit_events(client, auth, action="ticket.updated")
    ]
    assert {"category": [None, "hardware"], "priority": ["normal", "high"]} in changes


async def test_messages_and_reply_queue(client, auth, db):
    from sqlalchemy import select

    from zentral.db.models import Job

    ticket = await make_ticket(
        client, auth, "agent", requester_email="jane@example.com", body=PII_BODY
    )
    url = f"/api/v1/tickets/{ticket['id']}/messages"
    h = auth("agent")
    assert (
        await client.post(url, headers=h, json={"kind": "note", "body": "checking"})
    ).status_code == 201
    r = await client.post(url, headers=h, json={"kind": "outbound", "body": "Fixed!"})
    # Manually created ticket has no channel to deliver through.
    assert r.json()["delivery_status"] == "not_applicable"
    messages = (await client.get(url, headers=h)).json()
    assert [m["kind"] for m in messages] == ["inbound", "note", "outbound"]
    assert [m["author_type"] for m in messages] == ["requester", "staff", "staff"]
    assert messages[0]["body"] == PII_BODY
    assert not (await db.scalars(select(Job).where(Job.kind == "send_message"))).all()

    r = await client.post(url, headers=h, json={"kind": "system", "body": "x"})
    assert r.status_code == 422


async def test_reply_requires_requester(client, auth):
    ticket = await make_ticket(client, auth, "agent")
    r = await client.post(
        f"/api/v1/tickets/{ticket['id']}/messages",
        headers=auth("agent"),
        json={"kind": "outbound", "body": "hello?"},
    )
    assert r.status_code == 422


async def test_pii_is_redacted_without_read_pii(client, auth):
    team = await make_team(client, auth, "Support", ["viewer"])
    ticket = await make_ticket(
        client,
        auth,
        team_id=team,
        requester_email="jane.doe@example.com",
        requester_name="Jane Doe",
        subject="Reset for jane.doe@example.com",
        body=PII_BODY,
    )
    url = f"/api/v1/tickets/{ticket['id']}"

    full = (await client.get(url, headers=auth("admin"))).json()
    assert full["pii_redacted"] is False
    assert full["requester"] == {
        "id": full["requester"]["id"],
        "display_name": "Jane Doe",
        "email": "jane.doe@example.com",
    }

    masked = (await client.get(url, headers=auth("viewer"))).json()
    assert masked["pii_redacted"] is True
    assert masked["subject"] == "Reset for [EMAIL]"
    assert masked["requester"]["email"] is None
    assert "Jane" not in masked["requester"]["display_name"]

    body = (await client.get(f"{url}/messages", headers=auth("viewer"))).json()[0]
    assert "jane.doe" not in body["body"] and "12 34 56 78" not in body["body"]
    assert "[EMAIL]" in body["body"] and "[PHONE]" in body["body"]
    assert body["author_name"] == "Requester"

    listed = (await client.get("/api/v1/tickets", headers=auth("viewer"))).text
    assert "jane.doe" not in listed and "Jane Doe" not in listed


async def test_requester_lookup_needs_pii_permission(client, auth):
    team = await make_team(client, auth, "Support", ["viewer", "agent"])
    await make_ticket(client, auth, team_id=team, requester_email="jane@example.com")
    await make_ticket(client, auth, team_id=team, requester_email="bob@example.com")
    params = {"requester_email": "JANE@example.com"}  # lookup is case-insensitive
    r = await client.get("/api/v1/tickets", headers=auth("agent"), params=params)
    assert r.json()["total"] == 1
    r = await client.get("/api/v1/tickets", headers=auth("viewer"), params=params)
    assert r.status_code == 403


async def test_filters_search_and_pagination(client, auth):
    for i in range(5):
        await make_ticket(client, auth, subject=f"Laptop {i}", priority="high" if i % 2 else "low")
    await make_ticket(client, auth, subject="VPN broken")
    h = auth("admin")

    async def total(**params):
        return (await client.get("/api/v1/tickets", headers=h, params=params)).json()["total"]

    assert await total(q="laptop") == 5
    assert await total(q="ZEN-6") == 1
    assert await total(priority="high") == 2
    assert await total(status="closed") == 0
    assert await total(unassigned="true") == 6
    page = (await client.get("/api/v1/tickets", headers=h, params={"limit": 2, "offset": 4})).json()
    assert (len(page["items"]), page["total"]) == (2, 6)
    assert (
        await client.get("/api/v1/tickets", headers=h, params={"limit": 1000})
    ).status_code == 422


async def test_reads_are_audited_without_content(client, auth):
    ticket = await make_ticket(client, auth, requester_email="jane@example.com", body=PII_BODY)
    await client.get(f"/api/v1/tickets/{ticket['id']}", headers=auth("admin"))
    await client.get(f"/api/v1/tickets/{ticket['id']}/messages", headers=auth("admin"))
    events = await audit_events(client, auth, resource_id=str(ticket["id"]))
    assert {"ticket.read", "ticket.messages.read", "ticket.created"} <= {
        e["action"] for e in events
    }
    assert all(e["actor_id"] == SUBJECTS["admin"] for e in events)
    # The trail holds identifiers and enumerations only — never personal data.
    everything = str(await audit_events(client, auth, limit=200))
    for secret in ("jane@example.com", "12 34 56 78", "locked out", "Printer"):
        assert secret not in everything


async def test_stats_respect_scope(client, auth):
    team = await make_team(client, auth, "Support", ["agent"])
    other = await make_team(client, auth, "HR")
    await make_ticket(client, auth, team_id=team, priority="urgent")
    mine = await make_ticket(client, auth, team_id=team)
    await make_ticket(client, auth, team_id=other, priority="urgent")
    closed = await make_ticket(client, auth, team_id=team)
    await client.patch(
        f"/api/v1/tickets/{closed['id']}", headers=auth("admin"), json={"status": "closed"}
    )
    await client.get("/api/v1/me", headers=auth("agent"))
    await client.patch(
        f"/api/v1/tickets/{mine['id']}",
        headers=auth("agent"),
        json={"assignee_id": SUBJECTS["agent"]},
    )

    agent = (await client.get("/api/v1/tickets/stats", headers=auth("agent"))).json()
    assert agent == {"active": 2, "new": 1, "unassigned": 1, "mine": 1, "urgent": 1, "no_system": 2}
    admin = (await client.get("/api/v1/tickets/stats", headers=auth("admin"))).json()
    assert (admin["active"], admin["urgent"], admin["mine"]) == (3, 2, 0)
    empty = (await client.get("/api/v1/tickets/stats", headers=auth("viewer"))).json()
    assert set(empty.values()) == {0}
    assert (await client.get("/api/v1/tickets/stats", headers=auth("auditor"))).status_code == 403
