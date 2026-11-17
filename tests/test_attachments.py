"""Attachments end to end: checked, cleaned, encrypted, scanned, then — and only then — opened."""

from __future__ import annotations

import asyncio
import base64
import struct

import httpx
import pytest
from sqlalchemy import select, text

from tests.conftest import SUBJECTS
from tests.helpers import audit_events, make_source, make_team, make_ticket
from tests.test_files import jpeg_segment, make_jpeg, make_png
from tests.test_mail import MAILBOX, Graph, channel, mail, poll
from tests.test_portal import H, contact_id
from tests.test_portal import sign_in as portal_sign_in
from zentral.connectors.base import ConnectorError
from zentral.db.models import Attachment
from zentral.security.scanner import ClamdScanner, Scanner, ScanResult

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
SCREENSHOT = make_jpeg(jpeg_segment(0xE1, b"Exif\x00\x00GPS 55.6N 12.5E"))


class FakeScanner(Scanner):
    name = "fake"

    async def scan(self, data: bytes) -> ScanResult:
        if EICAR in data:
            return ScanResult(clean=False, signature="Eicar-Test-Signature")
        return ScanResult(clean=True)


@pytest.fixture(autouse=True)
def enable(runtime):
    runtime.settings.attachments_enabled = True
    runtime.settings.portal_enabled = True
    runtime.settings.portal_mail_channel = "Service desk mailbox"
    runtime.scanner = FakeScanner()


async def upload(client, auth, ticket_id, data, name="screenshot.jpg", who="admin"):
    return await client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        params={"filename": name},
        headers=auth(who),
        content=data,
    )


async def files(client, auth, ticket_id, who="admin") -> list[dict]:
    r = await client.get(f"/api/v1/tickets/{ticket_id}/attachments", headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


async def download(client, auth, ticket_id, attachment_id, who="admin"):
    return await client.get(
        f"/api/v1/tickets/{ticket_id}/attachments/{attachment_id}", headers=auth(who)
    )


# --- the life of a file ----------------------------------------------------------------------


async def test_a_screenshot_is_cleaned_scanned_and_then_opened(client, auth, worker, db):
    t = await make_ticket(client, auth)
    r = await upload(client, auth, t["id"], SCREENSHOT)
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["status"], a["content_type"], a["filename"]) == (
        "scanning",
        "image/jpeg",
        "screenshot.jpg",
    )

    # Not before the scanner has looked at it.
    early = await download(client, auth, t["id"], a["id"])
    assert early.status_code == 422 and "being checked" in early.text

    await worker.run_once()
    (listed,) = await files(client, auth, t["id"])
    assert listed["status"] == "clean"
    r = await download(client, auth, t["id"], a["id"])
    assert r.status_code == 200
    assert r.content == make_jpeg()  # the location in the photo's metadata is gone
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith('attachment; filename="screenshot.jpg"')
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"

    # Stored encrypted: neither the name nor the content is readable in the database.
    raw = (await db.execute(text("SELECT filename, data FROM attachments"))).one()
    assert "screenshot" not in raw.filename and b"JFIF" not in raw.data.encode()

    # It shows up in the conversation as a note with the file.
    messages = (
        await client.get(f"/api/v1/tickets/{t['id']}/messages", headers=auth("admin"))
    ).json()
    assert messages[-1]["kind"] == "note" and messages[-1]["body"] == "Attached screenshot.jpg."
    assert messages[-1]["attachments"][0]["id"] == a["id"]

    actions = [e["action"] for e in await audit_events(client, auth, resource_type="ticket")]
    assert {"attachment.added", "attachment.cleared", "attachment.downloaded"} <= set(actions)
    added = (await audit_events(client, auth, action="attachment.added"))[0]
    assert "screenshot" not in str(added)  # names can identify people: never in the trail
    assert added["details"]["stripped"] > 0


async def test_malware_is_blocked_and_destroyed(client, auth, worker, db):
    t = await make_ticket(client, auth)
    a = (await upload(client, auth, t["id"], b"log line\n" + EICAR, name="debug.log")).json()
    await worker.run_once()
    (listed,) = await files(client, auth, t["id"])
    assert listed["status"] == "blocked"
    r = await download(client, auth, t["id"], a["id"])
    assert r.status_code == 422 and "blocked" in r.text
    stored = await db.scalar(select(text("data")).select_from(Attachment.__table__))
    assert stored is None
    event = (await audit_events(client, auth, action="attachment.blocked"))[0]
    assert event["outcome"] == "failure" and event["details"]["signature"] == "Eicar-Test-Signature"


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b'<svg onload="alert(1)"/>', "pretty.svg"),
        (b"<!doctype html><script>x</script>", "page.html"),
        (b"MZ\x90\x00" + bytes(64), "screenshot.png"),  # an executable with an image's name
    ],
)
async def test_dangerous_files_are_refused(client, auth, data, name):
    t = await make_ticket(client, auth)
    r = await upload(client, auth, t["id"], data, name=name)
    assert r.status_code == 422 and "not accepted" in r.text


async def test_size_count_and_switch_are_enforced(client, auth, runtime):
    t = await make_ticket(client, auth)
    runtime.settings.attachment_max_bytes = 2048
    assert (await upload(client, auth, t["id"], b"x" * 3000, name="big.txt")).status_code == 413
    runtime.settings.attachment_max_bytes = 1024 * 1024
    runtime.settings.attachments_per_ticket = 2
    for _ in range(2):
        assert (await upload(client, auth, t["id"], b"ok", name="a.txt")).status_code == 201
    r = await upload(client, auth, t["id"], b"ok", name="a.txt")
    assert r.status_code == 422 and "number of attachments" in r.text
    runtime.settings.attachments_enabled = False
    other = await make_ticket(client, auth)
    r = await upload(client, auth, other["id"], b"ok", name="a.txt")
    assert r.status_code == 422 and "not enabled" in r.text


# --- who may open what -----------------------------------------------------------------------


async def test_files_open_only_for_people_who_may_see_personal_data(client, auth, worker):
    await client.get("/api/v1/me", headers=auth("viewer"))
    team = await make_team(client, auth, "Finance", members=["bot"])  # the agent can see it
    t = await make_ticket(client, auth, team_id=team)
    a = (await upload(client, auth, t["id"], SCREENSHOT, name="jane_doe_passport.jpg")).json()
    await worker.run_once()
    await client.patch(
        f"/api/v1/tickets/{t['id']}",
        headers=auth("admin"),
        json={"assignee_id": SUBJECTS["viewer"]},
    )
    # The viewer sees that there is a file, not its name, and cannot open it.
    (seen,) = await files(client, auth, t["id"], who="viewer")
    assert seen["filename"] == "attachment.jpg"
    assert (await download(client, auth, t["id"], a["id"], who="viewer")).status_code == 403
    # AI agents never receive attachments.
    assert (await download(client, auth, t["id"], a["id"], who="bot")).status_code == 403
    # Nor does anyone outside the ticket's scope.
    assert (await download(client, auth, t["id"], a["id"], who="agent")).status_code == 404
    assert (await upload(client, auth, t["id"], b"x", who="auditor")).status_code == 403


# --- arriving from outside ---------------------------------------------------------------------


async def test_a_webhook_report_brings_its_screenshot_and_context(client, auth, worker):
    source = await make_source(client, auth)
    r = await client.post(
        f"/api/v1/hooks/{source['id']}",
        headers={"Authorization": f"Bearer {source['ingest_token']}"},
        json={
            "title": "Save button does nothing",
            "description": "I click save and nothing happens.",
            "requester_email": "jane@example.com",
            "context": {
                "page": "/orders/42/edit",
                "app_version": "3.14.2",
                "browser": "Firefox 146",
            },
            "attachments": [
                {"filename": "screen.png", "content_base64": base64.b64encode(make_png()).decode()},
                {
                    "filename": "virus.exe",
                    "content_base64": base64.b64encode(b"MZ" + bytes(9)).decode(),
                },
            ],
        },
    )
    assert r.status_code == 202, r.text
    ticket_id = int(r.json()["tickets"].split("-")[1])
    (first,) = (
        await client.get(f"/api/v1/tickets/{ticket_id}/messages", headers=auth("admin"))
    ).json()
    assert "— Context —\npage: /orders/42/edit\napp_version: 3.14.2" in first["body"]
    (png,) = first["attachments"]
    assert png["from_requester"] and png["content_type"] == "image/png"
    # The refused file was dropped, the report was not.
    dropped = (await audit_events(client, auth, action="attachment.dropped"))[0]
    assert dropped["outcome"] == "failure"
    await worker.run_once()
    assert (await download(client, auth, ticket_id, png["id"])).status_code == 200


async def test_a_webhook_with_broken_base64_is_refused(client, auth):
    source = await make_source(client, auth)
    r = await client.post(
        f"/api/v1/hooks/{source['id']}",
        headers={"Authorization": f"Bearer {source['ingest_token']}"},
        json={
            "title": "x",
            "attachments": [{"filename": "a.png", "content_base64": "not base64!"}],
        },
    )
    assert r.status_code in (400, 422)


async def test_mail_attachments_and_pasted_screenshots_are_kept(client, auth, worker, outbound):
    graph = Graph(outbound)
    files_ = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": "image001.png",
            "isInline": True,
            "size": 100,
            "contentBytes": base64.b64encode(make_png()).decode(),
        },
        {"@odata.type": "#microsoft.graph.itemAttachment", "name": "Forwarded mail"},
        {"@odata.type": "#microsoft.graph.referenceAttachment", "name": "OneDrive link"},
    ]
    outbound.routes.insert(
        0, ("/attachments", lambda r: httpx.Response(200, json={"value": files_}))
    )
    graph.inbox = [mail(1, hasAttachments=True)]
    await channel(client, auth)
    await poll(worker)
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    (only,) = messages[0]["attachments"]
    assert only["filename"] == "image001.png"
    assert any(
        f"/users/{MAILBOX}/messages/AAMk1/attachments" in str(r.url) for r in outbound.requests
    )


# --- the portal -------------------------------------------------------------------------------


async def test_requesters_send_files_and_see_only_their_own(client, auth, db, runtime, worker):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    staff_file = (await upload(client, auth, t["id"], b"internal notes", name="notes.txt")).json()
    await portal_sign_in(client, runtime, db)
    r = await client.post(
        f"/api/v1/portal/tickets/{t['id']}/attachments",
        params={"filename": "screenshot.png"},
        headers=H,
        content=make_png(),
    )
    assert r.status_code == 201, r.text
    mine = r.json()["attachments"][0]
    await worker.run_once()
    detail = (await client.get(f"/api/v1/portal/tickets/{t['id']}", headers=H)).json()
    shared = [a["id"] for m in detail["messages"] for a in m["attachments"]]
    assert shared == [mine["id"]]
    ok = await client.get(f"/api/v1/portal/tickets/{t['id']}/attachments/{mine['id']}", headers=H)
    assert ok.status_code == 200 and ok.content == make_png()
    hidden = await client.get(
        f"/api/v1/portal/tickets/{t['id']}/attachments/{staff_file['id']}", headers=H
    )
    assert hidden.status_code == 404


# --- privacy -----------------------------------------------------------------------------------


async def test_erasure_removes_files_and_export_includes_them(client, auth, db, runtime, worker):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    await portal_sign_in(client, runtime, db)
    await client.post(
        f"/api/v1/portal/tickets/{t['id']}/attachments",
        params={"filename": "error.png"},
        headers=H,
        content=make_png(),
    )
    await worker.run_once()
    cid = await contact_id(db, "jane@example.com")
    export = (
        await client.get(f"/api/v1/privacy/contacts/{cid}/export", headers=auth("privacy"))
    ).json()
    (exported,) = export["tickets"][0]["attachments"]
    assert base64.b64decode(exported["content_base64"]) == make_png()
    await client.post(f"/api/v1/privacy/contacts/{cid}/erase", headers=auth("privacy"))
    assert (await db.scalars(select(Attachment))).all() == []


async def test_merging_brings_the_files_along(client, auth):
    a = await make_ticket(client, auth, requester_email="jane@example.com")
    b = await make_ticket(client, auth, requester_email="jane@example.com")
    await upload(client, auth, b["id"], b"trace", name="trace.txt")
    await client.post(
        f"/api/v1/tickets/{b['id']}/merge", headers=auth("admin"), json={"into_ticket_id": a["id"]}
    )
    assert [f["filename"] for f in await files(client, auth, a["id"])] == ["trace.txt"]


# --- the real scanner protocol -----------------------------------------------------------------


async def fake_clamd(reply_for):
    """A tiny clamd: reads one INSTREAM, answers like ClamAV."""

    async def handle(reader, writer):
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        data = b""
        while True:
            (size,) = struct.unpack(">I", await reader.readexactly(4))
            if size == 0:
                break
            data += await reader.readexactly(size)
        writer.write(reply_for(data))
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def test_clamd_protocol():
    server = await fake_clamd(
        lambda data: b"stream: Eicar-Test-Signature FOUND\0" if EICAR in data else b"stream: OK\0"
    )
    port = server.sockets[0].getsockname()[1]
    scanner = ClamdScanner("127.0.0.1", port)
    assert (await scanner.scan(b"x" * 200_000)).clean  # several chunks
    found = await scanner.scan(EICAR)
    assert (found.clean, found.signature) == (False, "Eicar-Test-Signature")
    server.close()
    await server.wait_closed()
    with pytest.raises(ConnectorError, match="unreachable"):
        await scanner.scan(b"x")


async def test_an_unreachable_scanner_keeps_files_closed(client, auth, runtime, worker, db):
    runtime.scanner = ClamdScanner("127.0.0.1", 9)  # nothing listens there
    t = await make_ticket(client, auth)
    await upload(client, auth, t["id"], b"log", name="a.txt")
    await worker.run_once()
    (listed,) = await files(client, auth, t["id"])
    assert listed["status"] == "scanning"  # retried later; never opened unscanned
