"""The requester portal: one-time links, cookie sessions, strict isolation, ratings."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.conftest import SUBJECTS
from tests.helpers import audit_events, make_ticket
from tests.test_lifecycle import shift
from zentral.db.models import Contact, Job, PortalToken, utcnow
from zentral.security.crypto import get_cipher
from zentral.services import portal

H = {"X-Zentral-Portal": "1"}


@pytest.fixture(autouse=True)
def enable_portal(runtime):
    runtime.settings.portal_enabled = True
    runtime.settings.portal_mail_channel = "Service desk mailbox"


async def contact_id(db, email: str):
    identity = get_cipher().blind_index("email", email)
    return await db.scalar(select(Contact.id).where(Contact.identity_hash == identity))


async def link_for(runtime, db, email: str) -> str:
    """What the worker does: create the link and 'send' it; return the token."""
    sent = []

    async def fake_send(message):
        sent.append(message)

    async with runtime.sessions() as session:
        ctx = runtime.system_ctx(session, "worker")
        await portal.send_link(ctx, await contact_id(db, email), fake_send)
        await ctx.commit()
    (message,) = sent
    assert message.recipient_email == email and message.ticket_key == ""
    return re.search(r"#token=(\S+)", message.body).group(1)


async def sign_in(client, runtime, db, email="jane@example.com"):
    token = await link_for(runtime, db, email)
    r = await client.post("/api/v1/portal/session", headers=H, json={"token": token})
    assert r.status_code == 200, r.text
    return r


async def sign_out(client):
    await client.post("/api/v1/portal/sign-out", headers=H)
    client.cookies.clear()


# --- signing in -----------------------------------------------------------------------------


async def test_asking_for_a_link_reveals_nothing(client, auth, db):
    await make_ticket(client, auth, requester_email="jane@example.com")
    known = await client.post("/api/v1/portal/link", headers=H, json={"email": "jane@example.com"})
    unknown = await client.post(
        "/api/v1/portal/link", headers=H, json={"email": "nobody@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    jobs = (await db.scalars(select(Job).where(Job.kind == "send_portal_link"))).all()
    assert [j.payload["contact_id"] for j in jobs] == [
        str(await contact_id(db, "jane@example.com"))
    ]
    events = await audit_events(client, auth, action="portal.link_requested")
    assert sorted(e["details"]["found"] for e in events) == [False, True]
    assert "example.com" not in str(events)  # the address itself is never logged


async def test_links_are_throttled_per_address(client, auth, db, runtime):
    await make_ticket(client, auth, requester_email="jane@example.com")
    for _ in range(runtime.settings.portal_links_per_hour + 2):
        await client.post("/api/v1/portal/link", headers=H, json={"email": "jane@example.com"})
    jobs = (await db.scalars(select(Job).where(Job.kind == "send_portal_link"))).all()
    assert len(jobs) == runtime.settings.portal_links_per_hour


async def test_a_link_signs_in_once_with_a_locked_down_cookie(client, auth, db, runtime):
    await make_ticket(client, auth, requester_email="jane@example.com", requester_name="Jane")
    token = await link_for(runtime, db, "jane@example.com")
    r = await client.post("/api/v1/portal/session", headers=H, json={"token": token})
    assert r.status_code == 200 and r.json() == {
        "display_name": "Jane",
        "email": "jane@example.com",
    }
    cookie = r.headers["set-cookie"].lower()
    for attribute in ("httponly", "samesite=strict", "path=/api/v1/portal", "max-age=28800"):
        assert attribute in cookie
    assert token not in str((await db.scalars(select(PortalToken.token_hash))).all())

    assert (await client.get("/api/v1/portal/me", headers=H)).status_code == 200
    client.cookies.clear()
    again = await client.post("/api/v1/portal/session", headers=H, json={"token": token})
    assert again.status_code == 401  # single use


async def test_expired_and_forged_links_do_not_work(client, auth, db, runtime):
    await make_ticket(client, auth, requester_email="jane@example.com")
    token = await link_for(runtime, db, "jane@example.com")
    link = await db.scalar(select(PortalToken).where(PortalToken.kind == "link"))
    link.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    for attempt in (token, "x" * 43):
        r = await client.post("/api/v1/portal/session", headers=H, json={"token": attempt})
        assert r.status_code == 401


async def test_the_portal_needs_its_header_and_can_be_off(client, auth, db, runtime):
    await make_ticket(client, auth, requester_email="jane@example.com")
    await sign_in(client, runtime, db)
    assert (await client.get("/api/v1/portal/me")).status_code == 403  # cross-site forgery guard
    runtime.settings.portal_enabled = False
    assert (await client.get("/api/v1/portal/me", headers=H)).status_code == 404


async def test_every_portal_route_requires_a_session(client):
    spec = (await client.get("/api/openapi.json")).json()
    checked = 0
    for path, methods in spec["paths"].items():
        if not path.startswith("/api/v1/portal/") or path in (
            "/api/v1/portal/link",
            "/api/v1/portal/session",
        ):
            continue
        for method in methods:
            r = await client.request(
                method.upper(), path.replace("{ticket_id}", "1"), headers=H, json={}
            )
            assert r.status_code == 401, f"{method} {path} answered {r.status_code}"
            checked += 1
    assert checked >= 7


async def test_staff_tokens_do_not_open_the_portal(client, auth):
    r = await client.get("/api/v1/portal/me", headers={**H, **auth("admin")})
    assert r.status_code == 401


# --- what a requester sees ------------------------------------------------------------------


async def test_requesters_see_only_their_own_public_conversation(client, auth, db, runtime):
    mine = await make_ticket(
        client, auth, requester_email="jane@example.com", body="My laptop is broken"
    )
    theirs = await make_ticket(client, auth, requester_email="bob@example.com")
    url = f"/api/v1/tickets/{mine['id']}/messages"
    await client.post(url, headers=auth("admin"), json={"kind": "note", "body": "INTERNAL ONLY"})
    await client.post(url, headers=auth("admin"), json={"kind": "outbound", "body": "On it"})
    await sign_in(client, runtime, db)

    listed = (await client.get("/api/v1/portal/tickets", headers=H)).json()
    assert [t["key"] for t in listed] == [mine["key"]]
    detail = (await client.get(f"/api/v1/portal/tickets/{mine['id']}", headers=H)).json()
    assert [(m["author"], m["body"]) for m in detail["messages"]] == [
        ("You", "My laptop is broken"),
        ("Service desk", "On it"),
    ]
    assert "INTERNAL" not in str(detail) and "Admin" not in str(detail)
    assert set(detail["ticket"]) == {
        "id",
        "key",
        "subject",
        "status",
        "created_at",
        "updated_at",
        "resolved_at",
        "merged_into_key",
        "rating",
        "can_rate",
        "can_reply",
    }

    for path in (f"/api/v1/portal/tickets/{theirs['id']}", "/api/v1/portal/tickets/9999"):
        assert (await client.get(path, headers=H)).status_code == 404
    r = await client.post(
        f"/api/v1/portal/tickets/{theirs['id']}/messages", headers=H, json={"body": "hi"}
    )
    assert r.status_code == 404


async def test_a_reply_reopens_and_reaches_the_assignee(client, auth, db, runtime):
    await client.get("/api/v1/me", headers=auth("agent"))
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    await client.patch(
        f"/api/v1/tickets/{t['id']}",
        headers=auth("admin"),
        json={"assignee_id": SUBJECTS["agent"], "status": "resolved"},
    )
    await sign_in(client, runtime, db)
    r = await client.post(
        f"/api/v1/portal/tickets/{t['id']}/messages",
        headers=H,
        json={"body": "Still broken, sorry"},
    )
    assert r.status_code == 201
    staff_view = (await client.get(f"/api/v1/tickets/{t['id']}", headers=auth("admin"))).json()
    assert staff_view["status"] == "open"
    notes = (await client.get("/api/v1/notifications", headers=auth("agent"))).json()["items"]
    assert notes[0]["kind"] == "requester_replied"
    event = (await audit_events(client, auth, action="ticket.message_received"))[0]
    assert event["actor_id"].startswith("contact:") and event["channel"] == "portal"


async def test_closed_requests_cannot_be_reopened(client, auth, db, runtime):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "closed"}
    )
    await sign_in(client, runtime, db)
    r = await client.post(
        f"/api/v1/portal/tickets/{t['id']}/messages", headers=H, json={"body": "hello?"}
    )
    assert r.status_code == 422 and "start a new one" in r.text


async def test_requesters_can_open_a_new_request(client, auth, db, runtime):
    await make_ticket(client, auth, requester_email="jane@example.com")
    await sign_in(client, runtime, db)
    r = await client.post(
        "/api/v1/portal/tickets",
        headers=H,
        json={"subject": "Printer jammed", "body": "Tray 2 again"},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    staff = (await client.get(f"/api/v1/tickets/{created['id']}", headers=auth("admin"))).json()
    assert staff["channel"] == "portal" and staff["requester"]["email"] == "jane@example.com"
    r = await client.post(
        "/api/v1/portal/tickets",
        headers=H,
        json={"subject": "x", "body": "y", "priority": "urgent"},  # not theirs to set
    )
    assert r.status_code == 422


# --- ratings -------------------------------------------------------------------------------


async def test_rating_a_resolved_request_once(client, auth, db, runtime):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    await sign_in(client, runtime, db)
    url = f"/api/v1/portal/tickets/{t['id']}/rating"
    assert (await client.post(url, headers=H, json={"score": 5})).status_code == 422  # not yet
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "resolved"}
    )
    for bad in ({"score": 0}, {"score": 6}, {"score": 3, "comment": "x" * 2001}):
        assert (await client.post(url, headers=H, json=bad)).status_code == 422
    r = await client.post(url, headers=H, json={"score": 4, "comment": "Quick and kind"})
    assert r.status_code == 201 and r.json()["rating"] == 4 and r.json()["can_rate"] is False
    assert (await client.post(url, headers=H, json={"score": 1})).status_code == 422  # once

    staff = (await client.get(f"/api/v1/tickets/{t['id']}", headers=auth("admin"))).json()
    assert (staff["rating_score"], staff["rating_comment"]) == (4, "Quick and kind")
    stats = (await client.get("/api/v1/tickets/insights", headers=auth("admin"))).json()
    assert (stats["ratings"], stats["rating_average"]) == (1, 4.0)
    event = (await audit_events(client, auth, action="ticket.rated"))[0]
    assert event["details"] == {"score": 4, "commented": True}


async def test_ratings_close_after_thirty_days(client, auth, db, runtime):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "resolved"}
    )
    await shift(db, t["id"], resolved_at=timedelta(days=31))
    await sign_in(client, runtime, db)
    r = await client.post(f"/api/v1/portal/tickets/{t['id']}/rating", headers=H, json={"score": 5})
    assert r.status_code == 422


# --- ending a session ------------------------------------------------------------------------


async def test_sign_out_ends_the_session(client, auth, db, runtime):
    await make_ticket(client, auth, requester_email="jane@example.com")
    await sign_in(client, runtime, db)
    cookie = dict(client.cookies)
    assert (await client.post("/api/v1/portal/sign-out", headers=H)).status_code == 204
    client.cookies.update(cookie)  # even a copied cookie is now worthless
    assert (await client.get("/api/v1/portal/me", headers=H)).status_code == 401


async def test_erasure_ends_sessions_and_removes_the_rating_comment(client, auth, db, runtime):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "resolved"}
    )
    await sign_in(client, runtime, db)
    await client.post(
        f"/api/v1/portal/tickets/{t['id']}/rating",
        headers=H,
        json={"score": 2, "comment": "Jane was not happy"},
    )
    cid = await contact_id(db, "jane@example.com")
    r = await client.post(f"/api/v1/privacy/contacts/{cid}/erase", headers=auth("privacy"))
    assert r.status_code == 200
    assert (await client.get("/api/v1/portal/me", headers=H)).status_code == 401
    assert (await db.scalars(select(PortalToken))).all() == []
    staff = (await client.get(f"/api/v1/tickets/{t['id']}", headers=auth("admin"))).json()
    assert staff["rating_score"] == 2 and staff["rating_comment"] is None


async def test_the_worker_refuses_without_a_mail_channel(client, auth, db, worker):
    await make_ticket(client, auth, requester_email="jane@example.com")
    await client.post("/api/v1/portal/link", headers=H, json={"email": "jane@example.com"})
    await worker.run_once()
    job = await db.scalar(select(Job).where(Job.kind == "send_portal_link"))
    await db.refresh(job)
    assert job.status == "failed" and "mail channel" in job.last_error
