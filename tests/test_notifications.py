"""Staff notifications and escalation: who hears what, once, without content."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.conftest import SUBJECTS
from tests.helpers import audit_events, make_source, make_team, make_ticket
from tests.test_lifecycle import shift
from zentral.db.models import Job, Notification, utcnow
from zentral.services import notifications


async def inbox(client, auth, who) -> dict:
    r = await client.get("/api/v1/notifications", headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


async def kinds(client, auth, who) -> list[str]:
    return [n["kind"] for n in (await inbox(client, auth, who))["items"]]


async def sweep(runtime) -> int:
    async with runtime.sessions() as session:
        ctx = runtime.system_ctx(session, "worker")
        sent = await notifications.escalate(ctx)
        await ctx.commit()
        return sent


async def sign_in(client, auth, *who: str) -> None:
    for person in who:
        await client.get("/api/v1/me", headers=auth(person))


# --- assignment and replies ---------------------------------------------------------------------


async def test_assignment_notifies_the_assignee_but_not_oneself(client, auth):
    await sign_in(client, auth, "agent", "agent2")
    t = await make_ticket(client, auth)
    url = f"/api/v1/tickets/{t['id']}"
    await client.patch(url, headers=auth("admin"), json={"assignee_id": SUBJECTS["agent"]})
    page = await inbox(client, auth, "agent")
    assert page["unread"] == 1
    (note,) = page["items"]
    assert (note["kind"], note["ticket_id"], note["ticket_key"]) == ("assigned", t["id"], t["key"])
    assert set(note) == {"id", "kind", "ticket_id", "ticket_key", "created_at", "read_at"}

    # Taking a ticket yourself is not news to you.
    await client.patch(url, headers=auth("agent2"), json={"assignee_id": SUBJECTS["agent2"]})
    assert await kinds(client, auth, "agent2") == []


async def test_a_requester_reply_reaches_the_assignee(client, auth):
    await sign_in(client, auth, "agent")
    source = await make_source(client, auth)
    hook = {"Authorization": f"Bearer {source['ingest_token']}"}
    payload = {"title": "VPN", "thread_key": "t-1", "requester_email": "jane@example.com"}
    r = await client.post(f"/api/v1/hooks/{source['id']}", headers=hook, json=payload)
    ticket_id = int(r.json()["tickets"].split("-")[1])
    await client.patch(
        f"/api/v1/tickets/{ticket_id}",
        headers=auth("admin"),
        json={"assignee_id": SUBJECTS["agent"]},
    )
    await client.post(
        f"/api/v1/hooks/{source['id']}", headers=hook, json={**payload, "title": "Any news?"}
    )
    assert await kinds(client, auth, "agent") == ["requester_replied", "assigned"]


async def test_people_only_see_and_mark_their_own(client, auth, db):
    await sign_in(client, auth, "agent", "agent2")
    t = await make_ticket(client, auth)
    await client.patch(
        f"/api/v1/tickets/{t['id']}",
        headers=auth("admin"),
        json={"assignee_id": SUBJECTS["agent"]},
    )
    theirs = (await inbox(client, auth, "agent"))["items"][0]["id"]
    assert await kinds(client, auth, "agent2") == []
    r = await client.post(
        "/api/v1/notifications/read", headers=auth("agent2"), json={"ids": [theirs]}
    )
    assert r.json() == {"marked": 0}
    r = await client.post(
        "/api/v1/notifications/read", headers=auth("agent"), json={"ids": [theirs]}
    )
    assert r.json() == {"marked": 1}
    assert (await inbox(client, auth, "agent"))["unread"] == 0

    # Roles without ticket access have no notifications at all.
    assert (await client.get("/api/v1/notifications", headers=auth("auditor"))).status_code == 403
    assert (await client.get("/api/v1/notifications")).status_code == 401


async def test_mark_read_by_ticket_or_all(client, auth):
    await sign_in(client, auth, "agent")
    a = await make_ticket(client, auth)
    b = await make_ticket(client, auth)
    for t in (a, b):
        await client.patch(
            f"/api/v1/tickets/{t['id']}",
            headers=auth("admin"),
            json={"assignee_id": SUBJECTS["agent"]},
        )
    r = await client.post(
        "/api/v1/notifications/read", headers=auth("agent"), json={"ticket_id": a["id"]}
    )
    assert r.json() == {"marked": 1}
    r = await client.post("/api/v1/notifications/read", headers=auth("agent"), json={})
    assert r.json() == {"marked": 1}
    assert (await inbox(client, auth, "agent"))["unread"] == 0


# --- escalation -------------------------------------------------------------------------------


async def test_targets_at_risk_warn_the_assignee_once(client, auth, runtime, db):
    await sign_in(client, auth, "agent")
    t = await make_ticket(client, auth, requester_email="jane@example.com")  # reply in 8h
    await client.patch(
        f"/api/v1/tickets/{t['id']}",
        headers=auth("admin"),
        json={"assignee_id": SUBJECTS["agent"]},
    )
    assert await sweep(runtime) == 0  # plenty of time left

    # Seven of eight hours gone: the reply target is in its last quarter.
    await shift(db, t["id"], created_at=timedelta(hours=7), response_due_at=timedelta(hours=7))
    assert await sweep(runtime) == 1
    assert await sweep(runtime) == 0  # once per level
    assert (await kinds(client, auth, "agent"))[0] == "response_at_risk"

    event = (await audit_events(client, auth, action="ticket.escalated"))[0]
    assert event["details"] == {"target": "response", "level": "at_risk", "notified": 1}
    assert event["actor_id"] == "system:worker"


async def test_missed_targets_escalate_to_the_team(client, auth, runtime, db):
    await sign_in(client, auth, "agent", "agent2")
    team = await make_team(client, auth, "Ops", members=["agent", "agent2"])
    t = await make_ticket(client, auth, requester_email="jane@example.com", team_id=team)
    await client.patch(
        f"/api/v1/tickets/{t['id']}",
        headers=auth("admin"),
        json={"assignee_id": SUBJECTS["agent"]},
    )
    await shift(
        db,
        t["id"],
        created_at=timedelta(hours=80),
        response_due_at=timedelta(hours=80),
        resolve_due_at=timedelta(hours=80),
    )
    assert await sweep(runtime) == 4  # both targets missed: assignee + colleague, twice
    assert sorted(await kinds(client, auth, "agent2")) == ["resolve_breached", "response_breached"]
    assert "response_breached" in await kinds(client, auth, "agent")
    assert await sweep(runtime) == 0

    # A reply meets the response target; raising the priority re-plans and re-arms.
    await client.post(
        f"/api/v1/tickets/{t['id']}/messages",
        headers=auth("agent"),
        json={"kind": "outbound", "body": "Looking"},
    )
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"priority": "urgent"}
    )
    assert await sweep(runtime) == 2  # resolution missed again; reply already given


async def test_unassigned_tickets_warn_their_team(client, auth, runtime, db):
    await sign_in(client, auth, "agent2")
    team = await make_team(client, auth, "Ops", members=["agent2"])
    t = await make_ticket(client, auth, team_id=team)  # internal: resolution target only
    await shift(db, t["id"], created_at=timedelta(hours=60), resolve_due_at=timedelta(hours=60))
    assert await sweep(runtime) == 1
    assert await kinds(client, auth, "agent2") == ["resolve_at_risk"]


async def test_pending_tickets_are_not_escalated(client, auth, runtime, db):
    team = await make_team(client, auth, "Ops", members=["agent2"])
    t = await make_ticket(client, auth, team_id=team)
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "pending"}
    )
    await shift(db, t["id"], resolve_due_at=timedelta(hours=100))
    assert await sweep(runtime) == 0


async def test_warnings_can_be_turned_off(client, auth, runtime, db):
    runtime.settings.escalation_warn_fraction = 0
    team = await make_team(client, auth, "Ops", members=["agent2"])
    t = await make_ticket(client, auth, team_id=team)
    await shift(db, t["id"], created_at=timedelta(hours=70), resolve_due_at=timedelta(hours=70))
    assert await sweep(runtime) == 0  # no "at risk" warnings; breaches still escalate


async def test_the_worker_schedules_sweeps_and_purges_old_notifications(runtime, worker, db):
    await worker.schedule()
    kinds_ = {j.kind for j in (await db.scalars(select(Job))).all()}
    assert {"escalation_sweep", "retention_sweep"} <= kinds_

    async with runtime.sessions() as session:
        ctx = runtime.system_ctx(session, "worker")
        t = await make_ticket_direct(ctx)
        session.add_all(
            [
                Notification(user_id="u", kind="assigned", ticket_id=t, created_at=utcnow()),
                Notification(
                    user_id="u",
                    kind="assigned",
                    ticket_id=t,
                    created_at=utcnow() - timedelta(days=31),
                    read_at=utcnow(),
                ),
                Notification(
                    user_id="u",
                    kind="assigned",
                    ticket_id=t,
                    created_at=utcnow() - timedelta(days=91),
                ),
            ]
        )
        await ctx.commit()
        await notifications.purge(ctx)
        await ctx.commit()
        remaining = (await session.scalars(select(Notification))).all()
        assert len(remaining) == 1


async def make_ticket_direct(ctx) -> int:
    from zentral.db.models import Ticket

    ticket = Ticket(subject="x", channel="manual", tags=[])
    ctx.session.add(ticket)
    await ctx.session.flush()
    return ticket.id
