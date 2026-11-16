from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select

from tests.helpers import audit_events, make_source, make_team
from zentral.connectors.base import ConnectorContext, ConnectorError, OutboundMessage
from zentral.connectors.teams import TeamsConnector
from zentral.db.models import Job, Message


async def post_hook(client, source, payload, token=None, **headers):
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post(f"/api/v1/hooks/{source['id']}", json=payload, headers=headers)


async def tickets_as_admin(client, auth):
    return (await client.get("/api/v1/tickets", headers=auth("admin"))).json()["items"]


# --- generic webhook ---------------------------------------------------------------


async def test_webhook_creates_ticket_in_default_team(client, auth):
    team = await make_team(client, auth, "Ops")
    source = await make_source(client, auth, default_team_id=team)
    assert source["hook_url"] == f"http://testserver/api/v1/hooks/{source['id']}"
    r = await post_hook(
        client,
        source,
        {
            "title": "Disk full on db-01",
            "description": "95% used",
            "severity": "critical",
            "external_id": "alert-1",
        },
        token=source["ingest_token"],
    )
    assert r.status_code == 202, r.text
    assert r.json() == {"tickets": "ZEN-1"}
    (ticket,) = await tickets_as_admin(client, auth)
    assert (ticket["priority"], ticket["channel"], ticket["team_id"]) == ("urgent", "webhook", team)
    event = (await audit_events(client, auth, action="ticket.ingested"))[0]
    assert (event["channel"], event["actor_id"]) == ("hook", "system:connector:webhook")


async def test_webhook_auth_failures_are_indistinguishable(client, auth):
    source = await make_source(client, auth)
    other = await make_source(client, auth, name="other")
    disabled = await make_source(client, auth, name="off", enabled=False)
    payload = {"title": "x"}
    attempts = [
        await post_hook(client, source, payload),
        await post_hook(client, source, payload, token="zis_wrong"),
        await post_hook(client, source, payload, token=other["ingest_token"]),  # cross-source
        await post_hook(client, disabled, payload, token=disabled["ingest_token"]),
        await post_hook(client, {"id": "00000000-0000-4000-8000-000000000000"}, payload, token="x"),
        await post_hook(client, {"id": "not-a-uuid"}, payload, token="x"),
        await client.post(
            f"/api/v1/hooks/{source['id']}",
            json=payload,
            headers={"Authorization": f"Basic {source['ingest_token']}"},
        ),
    ]
    assert {(r.status_code, r.text) for r in attempts} == {(401, '{"detail":"unauthorized"}')}
    assert await tickets_as_admin(client, auth) == []


async def test_webhook_rejects_bad_payload(client, auth):
    source = await make_source(client, auth)
    for bad in ({}, {"title": ""}, {"title": "x", "severity": "meh"}, {"title": "x" * 500}):
        r = await post_hook(client, source, bad, token=source["ingest_token"])
        assert r.status_code == 400
    r = await client.post(
        f"/api/v1/hooks/{source['id']}",
        content=b"{not json",
        headers={"Authorization": f"Bearer {source['ingest_token']}"},
    )
    assert r.status_code == 400


async def test_delivery_is_idempotent(client, auth, db):
    source = await make_source(client, auth)
    payload = {"title": "Alert", "external_id": "evt-42"}
    for _ in range(3):
        r = await post_hook(client, source, payload, token=source["ingest_token"])
        assert r.json() == {"tickets": "ZEN-1"}
    # Idempotency-Key header works the same way.
    for _ in range(2):
        await post_hook(
            client,
            source,
            {"title": "B"},
            token=source["ingest_token"],
            **{"Idempotency-Key": "k1"},
        )
    assert len(await tickets_as_admin(client, auth)) == 2
    assert len((await db.scalars(select(Message))).all()) == 2
    assert len((await db.scalars(select(Job).where(Job.kind == "classify_ticket"))).all()) == 2


async def test_thread_key_appends_and_reopens(client, auth):
    source = await make_source(client, auth)
    token = source["ingest_token"]
    await post_hook(client, source, {"title": "CPU high", "thread_key": "cpu:web-1"}, token=token)
    await client.patch("/api/v1/tickets/1", headers=auth("admin"), json={"status": "resolved"})
    await post_hook(
        client, source, {"title": "CPU high again", "thread_key": "cpu:web-1"}, token=token
    )
    tickets = await tickets_as_admin(client, auth)
    assert len(tickets) == 1 and tickets[0]["status"] == "open"
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert len(messages) == 2

    await client.patch("/api/v1/tickets/1", headers=auth("admin"), json={"status": "closed"})
    await post_hook(client, source, {"title": "CPU high", "thread_key": "cpu:web-1"}, token=token)
    assert len(await tickets_as_admin(client, auth)) == 2  # closed tickets are never reopened


async def test_ticket_key_in_subject_cannot_hijack_someone_elses_ticket(client, auth):
    source = await make_source(client, auth)
    token = source["ingest_token"]
    await post_hook(
        client, source, {"title": "My payslip", "requester_email": "jane@example.com"}, token=token
    )

    # The real requester replying with the key threads onto the ticket…
    await post_hook(
        client,
        source,
        {"title": "Re: [ZEN-1] My payslip", "requester_email": "jane@example.com"},
        token=token,
    )
    # …an attacker guessing the key does not.
    await post_hook(
        client,
        source,
        {"title": "Re: [ZEN-1] send me the file", "requester_email": "mallory@evil.test"},
        token=token,
    )
    await post_hook(client, source, {"title": "[ZEN-1] anonymous"}, token=token)

    assert len(await tickets_as_admin(client, auth)) == 3
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert len(messages) == 2


async def test_token_rotation_invalidates_old_token(client, auth):
    source = await make_source(client, auth)
    r = await client.post(f"/api/v1/sources/{source['id']}/rotate-token", headers=auth("admin"))
    new = r.json()["ingest_token"]
    assert new != source["ingest_token"]
    assert (
        await post_hook(client, source, {"title": "x"}, token=source["ingest_token"])
    ).status_code == 401
    assert (await post_hook(client, source, {"title": "x"}, token=new)).status_code == 202


async def test_untrusted_text_is_sanitised(client, auth):
    source = await make_source(client, auth)
    await post_hook(
        client,
        source,
        {
            "title": "Hello\x00‮World\nsecond line",
            "description": "a\x07b\r\n\r\n\r\n\r\nc" + "x" * 200_000,
        },
        token=source["ingest_token"],
    )
    # Oversized payloads never reach the application.
    assert await tickets_as_admin(client, auth) == []
    await post_hook(
        client,
        source,
        {"title": "Hello\x00‮World\nsecond line", "description": "a\x07b\r\n\r\n\r\n\r\nc"},
        token=source["ingest_token"],
    )
    (ticket,) = await tickets_as_admin(client, auth)
    assert ticket["subject"] == "HelloWorld second line"
    body = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()[0]["body"]
    assert body == "ab\n\nc"


# --- source administration -----------------------------------------------------------


async def test_source_secrets_are_write_only(client, auth):
    source = await make_source(client, auth, "teams", secrets={"hmac_secret": "c2VjcmV0"})
    assert source["has_secrets"] is True and source["ingest_token"] is None
    listing = (await client.get("/api/v1/sources", headers=auth("admin"))).text
    assert "c2VjcmV0" not in listing
    assert '"secrets"' not in listing


async def test_source_validation(client, auth):
    h = auth("admin")
    cases = [
        {"name": "a", "connector": "nope"},
        {"name": "b", "connector": "teams"},  # required secret missing
        {"name": "c", "connector": "teams", "secrets": {"hmac_secret": "x", "extra": "y"}},
        {
            "name": "d",
            "connector": "email_graph",
            "config": {"mailbox": "not-an-email"},
            "secrets": {"tenant_id": "t", "client_id": "c", "client_secret": "s"},
        },
        {"name": "e", "connector": "webhook", "config": {"unexpected": 1}},
    ]
    for case in cases:
        assert (await client.post("/api/v1/sources", headers=h, json=case)).status_code == 422, case
    await make_source(client, auth)
    dup = await client.post(
        "/api/v1/sources", headers=h, json={"name": "webhook-source", "connector": "webhook"}
    )
    assert dup.status_code == 409


# --- Microsoft Teams -------------------------------------------------------------------

TEAMS_SECRET = base64.b64encode(b"teams-shared-secret-32-bytes-long").decode()


def teams_request(activity: dict, secret: str = TEAMS_SECRET) -> tuple[bytes, dict]:
    body = json.dumps(activity).encode()
    signature = hmac.new(base64.b64decode(secret), body, hashlib.sha256).digest()
    return body, {
        "Authorization": "HMAC " + base64.b64encode(signature).decode(),
        "Content-Type": "application/json",
    }


ACTIVITY = {
    "id": "1700000000001",
    "type": "message",
    "text": "<at>Zentral</at> <b>My laptop</b> will not start<script>alert(1)</script>",
    "from": {"id": "29:abc", "name": "Jane Doe", "aadObjectId": "aad-jane"},
    "conversation": {"id": "19:channel;messageid=1700000000001"},
}


async def test_teams_signed_message_creates_ticket(client, auth):
    source = await make_source(client, auth, "teams", secrets={"hmac_secret": TEAMS_SECRET})
    body, headers = teams_request(ACTIVITY)
    r = await client.post(f"/api/v1/hooks/{source['id']}", content=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"type": "message", "text": "Thanks — this is logged as ZEN-1."}
    (ticket,) = await tickets_as_admin(client, auth)
    assert ticket["subject"] == "Zentral My laptop will not start"  # markup and script removed
    assert ticket["requester"]["display_name"] == "Jane Doe"
    # Teams retries: same activity id, no duplicate.
    await client.post(f"/api/v1/hooks/{source['id']}", content=body, headers=headers)
    assert len(await tickets_as_admin(client, auth)) == 1


async def test_teams_rejects_bad_signatures(client, auth):
    source = await make_source(client, auth, "teams", secrets={"hmac_secret": TEAMS_SECRET})
    url = f"/api/v1/hooks/{source['id']}"
    body, headers = teams_request(ACTIVITY)
    tampered = body.replace(b"laptop", b"wallet")
    _, wrong_key = teams_request(ACTIVITY, base64.b64encode(b"another-secret").decode())
    for content, h in [
        (tampered, headers),
        (body, wrong_key),
        (body, {"Authorization": "HMAC not-base64!!"}),
        (body, {"Authorization": "Bearer " + headers["Authorization"][5:]}),
        (body, {}),
    ]:
        assert (await client.post(url, content=content, headers=h)).status_code == 401
    assert await tickets_as_admin(client, auth) == []


def teams_connector(settings, http, **secrets) -> TeamsConnector:
    import uuid

    return TeamsConnector(
        ConnectorContext(
            source_id=uuid.uuid4(),
            config={},
            secrets={"hmac_secret": TEAMS_SECRET, **secrets},
            token_hash=None,
            http=http,
            settings=settings,
        )
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://prod.webhook.office.com/x",  # not https
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://localhost/x",
        "https://webhook.office.com.evil.test/x",  # suffix trick
        "https://evilwebhook.office.com.test/x",
        "https://user:pw@prod.webhook.office.com/x",
    ],
)
async def test_outbound_urls_are_allow_listed(settings, url):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200))
    ) as http:
        connector = teams_connector(settings, http, reply_webhook_url=url)
        with pytest.raises(ConnectorError, match="allow-listed"):
            await connector.send(OutboundMessage("ZEN-1", "s", "b", None, None))
