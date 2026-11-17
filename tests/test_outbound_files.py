"""Sending files to requesters with a reply; and what happens to files posted in Teams."""

from __future__ import annotations

import base64
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.helpers import make_source, make_ticket
from tests.test_attachments import EICAR, FakeScanner, files, upload
from tests.test_files import make_png
from tests.test_ingest import ACTIVITY, TEAMS_SECRET, teams_request
from tests.test_mail import Graph, channel, mail, poll
from tests.test_portal import H
from tests.test_portal import sign_in as portal_sign_in
from zentral.db.models import Attachment, utcnow
from zentral.services import attachments as attachments_service


@pytest.fixture(autouse=True)
def enable(runtime):
    runtime.settings.attachments_enabled = True
    runtime.settings.portal_enabled = True
    runtime.settings.portal_mail_channel = "Service desk mailbox"
    runtime.scanner = FakeScanner()


async def for_reply(client, auth, ticket_id, data, name="steps.png"):
    """Upload a file to be sent with the next reply."""
    r = await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        params={"filename": name, "purpose": "reply"},
        headers=auth("admin"),
        content=data,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def reply(client, auth, ticket_id, ids, body="Here is how"):
    return await client.post(
        f"/api/v1/tickets/{ticket_id}/messages",
        headers=auth("admin"),
        json={"kind": "outbound", "body": body, "attachment_ids": ids},
    )


async def mail_ticket(client, auth, worker, outbound):
    graph = Graph(outbound)
    graph.inbox = [mail(1)]
    await channel(client, auth)
    await poll(worker)
    return graph


async def test_a_reply_carries_a_screenshot_by_mail(client, auth, worker, outbound):
    graph = await mail_ticket(client, auth, worker, outbound)
    held = await for_reply(client, auth, 1, make_png())
    # Held files are not part of the conversation until they are sent.
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert held["id"] not in str([m["attachments"] for m in messages if m["kind"] != "note"])

    early = await reply(client, auth, 1, [held["id"]])
    assert early.status_code == 422 and "being checked" in early.text  # not before the scan
    await worker.run_once()
    r = await reply(client, auth, 1, [held["id"]])
    assert r.status_code == 201, r.text
    assert [a["id"] for a in r.json()["attachments"]] == [held["id"]]
    await worker.run_once()
    (sent,) = graph.sent
    (attached,) = sent["message"]["attachments"]
    assert attached["name"] == "steps.png" and attached["contentType"] == "image/png"
    assert base64.b64decode(attached["contentBytes"]) == make_png()

    # A file can be sent once; afterwards it belongs to that reply.
    again = await reply(client, auth, 1, [held["id"]])
    assert again.status_code == 422


async def test_requesters_receive_desk_files_in_the_portal(client, auth, db, runtime, worker):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    internal = (await upload(client, auth, t["id"], b"internal", name="notes.txt")).json()
    held = await for_reply(client, auth, t["id"], make_png(), name="how-to.png")
    await worker.run_once()
    assert (await reply(client, auth, t["id"], [held["id"]])).status_code == 201

    await portal_sign_in(client, runtime, db)
    detail = (await client.get(f"/api/v1/portal/tickets/{t['id']}", headers=H)).json()
    desk = [m for m in detail["messages"] if not m["from_you"]]
    assert [a["filename"] for a in desk[-1]["attachments"]] == ["how-to.png"]
    url = f"/api/v1/portal/tickets/{t['id']}/attachments"
    assert (await client.get(f"{url}/{held['id']}", headers=H)).content == make_png()
    assert (await client.get(f"{url}/{internal['id']}", headers=H)).status_code == 404


async def test_what_cannot_be_sent(client, auth, worker):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    other = await make_ticket(client, auth, requester_email="jane@example.com")
    virus = await for_reply(client, auth, t["id"], b"log\n" + EICAR, name="virus.txt")
    elsewhere = await for_reply(client, auth, other["id"], make_png())
    note_file = (await upload(client, auth, t["id"], make_png())).json()
    await worker.run_once()
    for ids, reason in (
        ([virus["id"]], "blocked"),
        ([elsewhere["id"]], "belongs elsewhere"),
        ([note_file["id"]], "already sent"),
        (["00000000-0000-4000-8000-000000000000"], "unknown"),
    ):
        r = await reply(client, auth, t["id"], ids)
        assert r.status_code == 422 and reason in r.text, r.text
    r = await client.post(
        f"/api/v1/tickets/{t['id']}/messages",
        headers=auth("admin"),
        json={"kind": "note", "body": "x", "attachment_ids": [elsewhere["id"]]},
    )
    assert r.status_code == 422


async def test_mail_size_is_checked_before_sending(client, auth, worker, outbound, monkeypatch):
    await mail_ticket(client, auth, worker, outbound)
    monkeypatch.setattr(attachments_service, "OUTBOUND_MAX_BYTES", 50)
    held = await for_reply(client, auth, 1, make_png())
    await worker.run_once()
    r = await reply(client, auth, 1, [held["id"]])
    assert r.status_code == 422 and "too large for e-mail" in r.text


async def test_channels_without_file_delivery_need_the_portal(client, auth, runtime, worker):
    source = await make_source(client, auth)  # a webhook: replies are not delivered anywhere
    r = await client.post(
        f"/api/v1/hooks/{source['id']}",
        headers={"Authorization": f"Bearer {source['ingest_token']}"},
        json={"title": "Report", "requester_email": "jane@example.com"},
    )
    ticket_id = int(r.json()["tickets"].split("-")[1])
    held = await for_reply(client, auth, ticket_id, make_png())
    await worker.run_once()
    runtime.settings.portal_enabled = False
    r = await reply(client, auth, ticket_id, [held["id"]])
    assert r.status_code == 422 and "portal" in r.text
    runtime.settings.portal_enabled = True
    assert (await reply(client, auth, ticket_id, [held["id"]])).status_code == 201


async def test_unsent_files_are_removed_after_a_day(client, auth, runtime, db):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    held = await for_reply(client, auth, t["id"], make_png())
    kept = await for_reply(client, auth, t["id"], make_png(), name="fresh.png")
    stale = await db.get(Attachment, uuid.UUID(held["id"]))
    stale.created_at = utcnow() - timedelta(days=2)
    await db.commit()
    async with runtime.sessions() as session:
        ctx = runtime.system_ctx(session, "worker")
        assert await attachments_service.purge_unsent(ctx) == 1
        await ctx.commit()
    remaining = {str(a.id) for a in (await db.scalars(select(Attachment))).all()}
    assert held["id"] not in remaining and kept["id"] in remaining
    assert len(await files(client, auth, t["id"])) == len(remaining)


async def test_files_posted_in_teams_are_not_lost_silently(client, auth):
    source = await make_source(client, auth, "teams", secrets={"hmac_secret": TEAMS_SECRET})
    activity = {
        **ACTIVITY,
        "attachments": [
            {"contentType": "text/html", "content": "<div>My laptop</div>"},
            {"contentType": "image/*", "contentUrl": "https://smba.trafficmanager.net/x"},
        ],
    }
    body, headers = teams_request(activity)
    r = await client.post(f"/api/v1/hooks/{source['id']}", content=body, headers=headers)
    assert "Files can't be received through Teams" in r.json()["text"]
    assert "mention ZEN-1" in r.json()["text"]
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert "1 file(s) were posted in Teams" in messages[0]["body"]
