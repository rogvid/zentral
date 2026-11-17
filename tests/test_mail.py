"""Microsoft 365 mail channel: scoped by construction, configurable, observable."""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from tests.helpers import audit_events, make_source
from zentral.connectors import email_graph
from zentral.db.models import IngestSource, Job, utcnow

MAILBOX = "servicedesk@corp.example"
PROBE = "ceo@corp.example"
SECRETS = {"tenant_id": "tenant-1", "client_id": "client-1", "client_secret": "graph-secret"}


def graph_token(roles: list[str] | None) -> str:
    """A token shaped like Microsoft's; ``None`` produces an opaque (unreadable) one."""
    if roles is None:
        return "opaque-token"
    body = base64.urlsafe_b64encode(json.dumps({"roles": roles, "aud": "graph"}).encode()).rstrip(
        b"="
    )
    return f"eyJhbGciOiJSUzI1NiJ9.{body.decode()}.sig"


def mail(n: int, sender: str = "Jane.Doe@example.com", **extra) -> dict:
    return {
        "id": f"AAMk{n}",
        "internetMessageId": f"<msg-{n}@mail.example.com>",
        "conversationId": f"conv-{n}",
        "subject": f"Problem {n}",
        "from": {"emailAddress": {"name": "Jane Doe", "address": sender}},
        "receivedDateTime": f"2026-11-01T08:{n:02d}:00Z",
        "body": {"contentType": "text", "content": f"Body {n}"},
        "internetMessageHeaders": [],
        **extra,
    }


class Graph:
    """A stand-in for login.microsoftonline.com and graph.microsoft.com."""

    def __init__(self, outbound, roles=(), probe_status=403):
        self.roles: list[str] | None = list(roles) if roles is not None else None
        self.probe_status = probe_status
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.marked_read: list[str] = []
        self.send_status = 202
        self.outbound = outbound
        outbound.on(
            "oauth2/v2.0/token",
            lambda r: httpx.Response(
                200, json={"access_token": graph_token(self.roles), "expires_in": 3600}
            ),
        )
        outbound.on("/sendMail", self._send)
        outbound.on(
            f"/users/{PROBE}/",
            lambda r: httpx.Response(self.probe_status, json={"value": [{"id": "secret"}]}),
        )
        outbound.on(f"/users/{MAILBOX}/messages/", self._patch)
        outbound.on(f"/users/{MAILBOX}/mailFolders", self._list)

    def _send(self, request):
        if self.send_status < 300:
            self.sent.append(json.loads(request.content))
        return httpx.Response(self.send_status)

    def _patch(self, request):
        self.marked_read.append(str(request.url).rsplit("/", 1)[-1])
        return httpx.Response(200, json={})

    def _list(self, request):
        since = request.url.params.get("$filter", "").removeprefix("receivedDateTime gt ")
        return httpx.Response(
            200, json={"value": [m for m in self.inbox if m["receivedDateTime"] > since]}
        )

    def mailbox_reads(self) -> int:
        return sum(
            1 for r in self.outbound.requests if f"/users/{MAILBOX}/mailFolders" in str(r.url)
        )


@pytest.fixture(autouse=True)
def _clear_connector_caches():
    email_graph._token_cache.clear()
    email_graph._scope_verified.clear()
    yield
    email_graph._token_cache.clear()
    email_graph._scope_verified.clear()


@pytest.fixture
def graph(outbound) -> Graph:
    return Graph(outbound)


async def channel(client, auth, **config) -> dict:
    return await make_source(
        client,
        auth,
        "email_graph",
        # Acknowledgements have their own tests; elsewhere they would only add noise.
        config={
            "mailbox": MAILBOX,
            "initial_sync": "all",
            "acknowledge_new_tickets": False,
            **config,
        },
        secrets=SECRETS,
    )


async def poll(worker) -> None:
    await worker.schedule()
    await worker.run_once()


async def tickets(client, auth) -> list[dict]:
    return (await client.get("/api/v1/tickets", headers=auth("admin"))).json()["items"]


async def source_state(client, auth) -> dict:
    return (await client.get("/api/v1/sources", headers=auth("admin"))).json()[0]


# --- scope: proven, or the channel does not run ---------------------------------------------


async def test_rbac_scoped_app_runs_full_cycle(client, auth, worker, graph, db):
    graph.inbox = [mail(1)]
    source = await channel(client, auth)
    await poll(worker)

    (ticket,) = await tickets(client, auth)
    assert (ticket["subject"], ticket["channel"]) == ("Problem 1", "email_graph")
    assert ticket["requester"]["email"] == "Jane.Doe@example.com"
    state = await source_state(client, auth)
    assert state["last_ok_at"] and state["last_error"] is None
    stored = await db.get(IngestSource, uuid.UUID(source["id"]))
    assert stored.cursor == {"received_after": "2026-11-01T08:01:00Z"}

    await poll(worker)  # nothing new, no duplicates
    assert len(await tickets(client, auth)) == 1

    r = await client.post(
        "/api/v1/tickets/1/messages",
        headers=auth("admin"),
        json={"kind": "outbound", "body": "Try safe mode."},
    )
    assert r.json()["delivery_status"] == "pending"
    await worker.run_once()
    (sent,) = graph.sent
    assert sent["message"]["subject"] == "[ZEN-1] Problem 1"
    assert sent["message"]["toRecipients"] == [
        {"emailAddress": {"address": "Jane.Doe@example.com"}}
    ]
    assert sent["message"]["body"] == {"contentType": "Text", "content": "Try safe mode."}
    assert sent["saveToSentItems"] is True
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert messages[-1]["delivery_status"] == "sent"


@pytest.mark.parametrize(
    "roles",
    [
        ["Mail.Read"],
        ["Mail.ReadWrite", "Mail.Send"],
        ["full_access_as_app"],
        ["User.Read.All", "Mail.Send"],
    ],
)
async def test_tenant_wide_permissions_are_refused(client, auth, worker, graph, roles):
    """The decisive property: with tenant-wide access and no proof of restriction,
    the connector reads no mail at all."""
    graph.roles = roles
    graph.inbox = [mail(1)]
    await channel(client, auth)
    await poll(worker)
    await poll(worker)

    assert await tickets(client, auth) == []
    assert graph.mailbox_reads() == 0
    state = await source_state(client, auth)
    assert "tenant-wide permissions" in state["last_error"] and state["last_ok_at"] is None
    failing = await audit_events(client, auth, action="source.failing")
    assert len(failing) == 1  # recorded once, not on every poll
    assert failing[0]["details"] == {"connector": "email_graph", "error": "ScopeError"}


async def test_access_policy_is_proven_with_a_probe_mailbox(client, auth, worker, graph):
    graph.roles = ["Mail.Read", "Mail.Send"]  # tenant-wide on paper…
    graph.probe_status = 403  # …but Exchange denies other mailboxes
    graph.inbox = [mail(1)]
    await channel(client, auth, scope_probe_mailbox=PROBE)
    await poll(worker)
    assert len(await tickets(client, auth)) == 1


@pytest.mark.parametrize(
    ("probe_status", "expected"),
    [(200, "can read the probe mailbox"), (404, "inconclusive"), (500, "inconclusive")],
)
async def test_readable_or_inconclusive_probe_stops_the_channel(
    client, auth, worker, graph, probe_status, expected
):
    graph.roles = ["Mail.Read"]
    graph.probe_status = probe_status
    graph.inbox = [mail(1)]
    await channel(client, auth, scope_probe_mailbox=PROBE)
    await poll(worker)
    assert await tickets(client, auth) == []
    assert graph.mailbox_reads() == 0
    assert expected in (await source_state(client, auth))["last_error"]


async def test_probe_wins_even_when_token_looks_scoped(client, auth, worker, graph):
    graph.roles = []
    graph.probe_status = 200
    graph.inbox = [mail(1)]
    await channel(client, auth, scope_probe_mailbox=PROBE)
    await poll(worker)
    assert await tickets(client, auth) == []


async def test_unreadable_token_needs_a_probe(client, auth, worker, graph):
    graph.roles = None
    graph.inbox = [mail(1)]
    source = await channel(client, auth)
    await poll(worker)
    assert await tickets(client, auth) == []
    assert "scope probe mailbox" in (await source_state(client, auth))["last_error"]

    r = await client.patch(
        f"/api/v1/sources/{source['id']}",
        headers=auth("admin"),
        json={"config": {"mailbox": MAILBOX, "initial_sync": "all", "scope_probe_mailbox": PROBE}},
    )
    assert (
        r.status_code == 200 and r.json()["last_error"] is None
    )  # config change clears the old verdict
    await poll(worker)
    assert len(await tickets(client, auth)) == 1


async def test_probe_must_be_another_mailbox(client, auth, worker, graph):
    graph.inbox = [mail(1)]
    await channel(client, auth, scope_probe_mailbox=MAILBOX.upper())
    await poll(worker)
    assert await tickets(client, auth) == []


async def test_scope_is_rechecked_and_a_later_widening_stops_the_channel(
    client, auth, worker, graph, monkeypatch
):
    graph.inbox = [mail(1)]
    await channel(client, auth)
    await poll(worker)
    assert len(await tickets(client, auth)) == 1

    # Someone grants the app tenant-wide Mail.Read afterwards.
    graph.roles = ["Mail.Read"]
    graph.inbox.append(mail(2))
    email_graph._token_cache.clear()
    await poll(worker)
    assert len(await tickets(client, auth)) == 2  # still inside the verified hour

    monkeypatch.setattr(email_graph, "SCOPE_RECHECK_SECONDS", 0)
    graph.inbox.append(mail(3))
    await poll(worker)
    assert len(await tickets(client, auth)) == 2
    assert "tenant-wide" in (await source_state(client, auth))["last_error"]


async def test_sending_is_scope_checked_too(client, auth, worker, graph):
    graph.inbox = [mail(1)]
    await channel(client, auth)
    await poll(worker)
    graph.roles = ["Mail.Send"]
    email_graph._token_cache.clear()
    email_graph._scope_verified.clear()
    await client.post(
        "/api/v1/tickets/1/messages", headers=auth("admin"), json={"kind": "outbound", "body": "Hi"}
    )
    await worker.run_once()
    assert graph.sent == []


# --- configurable behaviour -----------------------------------------------------------------


async def test_first_sync_imports_only_new_mail_by_default(client, auth, worker, graph, db):
    graph.inbox = [mail(1), mail(2)]  # years of history already in the mailbox
    source = await make_source(
        client, auth, "email_graph", config={"mailbox": MAILBOX}, secrets=SECRETS
    )
    assert source["config"]["initial_sync"] == "new_only"
    await poll(worker)
    assert await tickets(client, auth) == []
    assert graph.mailbox_reads() == 0  # the backlog is not even fetched

    stored = await db.get(IngestSource, uuid.UUID(source["id"]))
    stamp = stored.cursor["received_after"]
    assert stamp[:4] == str(utcnow().year)
    graph.inbox.append(mail(3, receivedDateTime="2099-01-01T00:00:00Z"))
    await poll(worker)
    assert [t["subject"] for t in await tickets(client, auth)] == ["Problem 3"]


async def test_sender_allow_and_block_lists(client, auth, worker, graph, db):
    graph.inbox = [
        mail(1, "jane@example.com"),
        mail(2, "bob@partner.example.org"),
        mail(3, "eve@evil.test"),
        mail(4, "noreply@example.com"),
        mail(5, "kim@mail.example.com"),  # subdomain of an allowed domain
        mail(6, "x@notexample.com"),  # look-alike must not match
    ]
    source = await channel(
        client,
        auth,
        allowed_sender_domains="Example.com, @partner.example.org",  # tolerant input
        blocked_senders=["noreply@example.com"],
    )
    assert source["config"]["allowed_sender_domains"] == ["example.com", "partner.example.org"]
    await poll(worker)
    assert sorted(t["subject"] for t in await tickets(client, auth)) == [
        "Problem 1",
        "Problem 2",
        "Problem 5",
    ]
    # Skipped mail is not retried forever: the cursor moves past it.
    stored = await db.get(IngestSource, uuid.UUID(source["id"]))
    assert stored.cursor == {"received_after": "2026-11-01T08:06:00Z"}


async def test_automatic_replies_and_own_mail_are_ignored(client, auth, worker, graph):
    def header(name, value):
        return {"internetMessageHeaders": [{"name": name, "value": value}]}

    graph.inbox = [
        mail(1, **header("Auto-Submitted", "auto-replied")),
        mail(2, **header("Precedence", "bulk")),
        mail(3, **header("X-Autoreply", "yes")),
        mail(4, **header("List-Unsubscribe", "<mailto:u@x>")),
        mail(5, **header("Return-Path", "<>")),
        mail(6, MAILBOX),  # our own reply bouncing back
        mail(7, **header("Auto-Submitted", "no")),
        mail(8),
    ]
    await channel(client, auth)
    await poll(worker)
    assert sorted(t["subject"] for t in await tickets(client, auth)) == ["Problem 7", "Problem 8"]


async def test_automatic_replies_can_be_accepted(client, auth, worker, graph):
    graph.inbox = [
        mail(1, internetMessageHeaders=[{"name": "Auto-Submitted", "value": "auto-generated"}])
    ]
    await channel(client, auth, ignore_automatic_replies=False)
    await poll(worker)
    assert len(await tickets(client, auth)) == 1


async def test_mark_read_happens_only_after_mail_is_stored(
    client, auth, worker, graph, monkeypatch
):
    graph.inbox = [mail(1), mail(2, "eve@blocked.test")]
    await channel(client, auth, after_ingest="mark_read", blocked_senders=["blocked.test"])
    await poll(worker)
    assert len(await tickets(client, auth)) == 1
    assert graph.marked_read == ["AAMk1"]  # skipped mail stays unread for a human to see

    # If marking fails, the ticket is still there (ingestion is never undone).
    graph.inbox.append(mail(3))
    graph.outbound.routes.insert(0, (f"/users/{MAILBOX}/messages/", lambda r: httpx.Response(403)))
    await poll(worker)
    assert len(await tickets(client, auth)) == 2
    assert (await source_state(client, auth))["last_error"] is None


async def test_default_never_modifies_the_mailbox(client, auth, worker, graph):
    graph.inbox = [mail(1)]
    await channel(client, auth)
    await poll(worker)
    assert graph.marked_read == []
    assert {r.method for r in graph.outbound.requests if "graph.microsoft.com" in str(r.url)} == {
        "GET"
    }


async def test_read_only_channel_and_recipient_allow_list(client, auth, worker, graph, db):
    graph.inbox = [mail(1, "jane@example.com"), mail(2, "bob@other.example.org")]
    source = await channel(
        client, auth, allowed_recipient_domains=["example.com"], save_to_sent_items=False
    )
    await poll(worker)
    h = auth("admin")
    for ticket_id in (1, 2):
        await client.post(
            f"/api/v1/tickets/{ticket_id}/messages",
            headers=h,
            json={"kind": "outbound", "body": "Hello"},
        )
    await worker.run_once()
    (sent,) = graph.sent
    assert sent["message"]["toRecipients"][0]["emailAddress"]["address"] == "jane@example.com"
    assert sent["saveToSentItems"] is False
    job = await db.scalar(
        select(Job).where(Job.kind == "send_message", Job.last_error.is_not(None))
    )
    assert (
        job.last_error == "ConnectorRejected: recipient domain is not on this channel's allow-list"
    )
    # A rejection cannot succeed on retry: failed at once, not retried five times.
    assert (job.status, job.attempts) == ("failed", 1)
    messages = (await client.get("/api/v1/tickets/2/messages", headers=h)).json()
    assert messages[-1]["delivery_status"] == "failed"

    # Read-only and acknowledgements contradict each other: refused, not silently ignored.
    contradictory = await client.patch(
        f"/api/v1/sources/{source['id']}",
        headers=h,
        json={
            "config": {"mailbox": MAILBOX, "send_replies": False, "acknowledge_new_tickets": True}
        },
    )
    assert contradictory.status_code == 422
    r = await client.patch(
        f"/api/v1/sources/{source['id']}",
        headers=h,
        json={
            "config": {"mailbox": MAILBOX, "send_replies": False, "acknowledge_new_tickets": False}
        },
    )
    assert r.status_code == 200
    await client.post(
        "/api/v1/tickets/1/messages", headers=h, json={"kind": "outbound", "body": "Again"}
    )
    await worker.run_once()
    assert len(graph.sent) == 1


@pytest.mark.parametrize(
    "config",
    [
        {"mailbox": "not-an-address"},
        {"mailbox": MAILBOX, "folder": "../../drafts"},
        {"mailbox": "a/../../admin@corp.example"},
        {"mailbox": "x?$expand=y@corp.example"},
        {"mailbox": MAILBOX, "scope_probe_mailbox": "ceo%2f..@corp.example"},
        {"mailbox": MAILBOX, "allowed_sender_domains": ["not a domain"]},
        {"mailbox": MAILBOX, "allowed_sender_domains": ["example.com' or 1 eq 1"]},
        {"mailbox": MAILBOX, "blocked_senders": ["a@b@c.com"]},
        {"mailbox": MAILBOX, "initial_sync": "everything"},
        {"mailbox": MAILBOX, "after_ingest": "delete"},
        {"mailbox": MAILBOX, "page_size": 5000},
        {"mailbox": MAILBOX, "require_scoped_access": False},  # there is no off switch
        {"mailbox": MAILBOX, "scope_probe_mailbox": "nope"},
    ],
)
async def test_invalid_mail_configuration_is_rejected(client, auth, config):
    r = await client.post(
        "/api/v1/sources",
        headers=auth("admin"),
        json={"name": "m", "connector": "email_graph", "config": config, "secrets": SECRETS},
    )
    assert r.status_code == 422, config


# --- self-test and administration -------------------------------------------------------------


async def run_test(client, auth, source, who="admin"):
    return await client.post(f"/api/v1/sources/{source['id']}/test", headers=auth(who))


async def test_channel_self_test_reports_each_check(client, auth, graph):
    source = await channel(client, auth)
    results = {d["name"]: d for d in (await run_test(client, auth, source)).json()}
    assert results["Sign in to Microsoft"]["status"] == "ok"
    assert (
        results["Mailbox scope"]["status"] == "ok" and "RBAC" in results["Mailbox scope"]["detail"]
    )
    assert results["Read mailbox"]["status"] == "ok"
    assert results["Send replies"]["status"] == "warn"  # honest: cannot be proven without sending

    graph.roles = ["Mail.Read"]
    email_graph._token_cache.clear()
    results = {d["name"]: d for d in (await run_test(client, auth, source)).json()}
    assert results["Mailbox scope"]["status"] == "fail"  # the test always re-verifies

    events = await audit_events(client, auth, action="source.tested")
    assert [e["outcome"] for e in events] == ["failure", "success"]
    assert events[0]["details"]["checks"]["Mailbox scope"] == "fail"


async def test_self_test_with_bad_credentials_and_permissions(client, auth, graph, outbound):
    source = await channel(client, auth)
    outbound.routes.insert(
        0,
        (
            "oauth2/v2.0/token",
            lambda r: httpx.Response(
                401, json={"error_description": "secret expired AADSTS7000222"}
            ),
        ),
    )
    r = await run_test(client, auth, source)
    assert r.json() == [
        {"name": "Sign in to Microsoft", "status": "fail", "detail": "token endpoint returned 401"}
    ]
    assert "graph-secret" not in r.text and "AADSTS" not in r.text  # no upstream text, no secrets

    assert (await run_test(client, auth, source, who="agent")).status_code == 403


async def test_self_test_for_connectors_without_live_checks(client, auth):
    source = await make_source(client, auth)
    assert (await run_test(client, auth, source)).json()[0]["status"] == "ok"


async def test_updating_a_channel_keeps_secrets_left_blank(client, auth, worker, graph):
    graph.inbox = [mail(1)]
    source = await channel(client, auth)
    h = auth("admin")
    # Rotate only the client secret; the other two are left blank in the form.
    r = await client.patch(
        f"/api/v1/sources/{source['id']}",
        headers=h,
        json={"secrets": {"tenant_id": "", "client_id": "", "client_secret": "new-secret"}},
    )
    assert r.status_code == 200 and r.json()["has_secrets"] is True
    await poll(worker)
    token_request = next(r for r in graph.outbound.requests if "oauth2" in str(r.url))
    assert (
        b"client_secret=new-secret" in token_request.content
        and b"client_id=client-1" in token_request.content
    )
    bad = await client.patch(
        f"/api/v1/sources/{source['id']}", headers=h, json={"secrets": {"password": "x"}}
    )
    assert bad.status_code == 422


async def test_finished_jobs_are_cleaned_up(client, auth, worker, db, graph):
    from datetime import timedelta

    from zentral.services.jobs import DONE_TTL, FAILED_TTL

    old = utcnow() - DONE_TTL - timedelta(hours=1)
    db.add_all(
        [
            Job(kind="classify_ticket", payload={}, status="done", created_at=old),
            Job(kind="classify_ticket", payload={}, status="failed", created_at=old),  # kept longer
            Job(
                kind="classify_ticket",
                payload={},
                status="failed",
                created_at=utcnow() - FAILED_TTL - timedelta(days=1),
            ),
            Job(kind="classify_ticket", payload={"ticket_id": 0}, status="done"),
        ]
    )
    await db.commit()
    await worker.schedule()
    remaining = (await db.scalars(select(Job).where(Job.kind == "classify_ticket"))).all()
    assert sorted(j.status for j in remaining) == ["done", "failed"]


# --- acknowledging the requester ------------------------------------------------------------


async def test_new_requests_are_acknowledged_with_their_reference(client, auth, worker, graph, db):
    graph.inbox = [mail(1)]
    await channel(client, auth, acknowledge_new_tickets=True)
    await poll(worker)
    await worker.run_once()  # deliver

    (ack,) = graph.sent
    assert ack["message"]["subject"] == "[ZEN-1] Problem 1"  # replies thread back by the key
    assert "logged as ZEN-1" in ack["message"]["body"]["content"]
    assert ack["message"]["toRecipients"] == [{"emailAddress": {"address": "Jane.Doe@example.com"}}]

    # Visible in the conversation, authored by Zentral, delivered.
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert [(m["kind"], m["author_type"], m["delivery_status"]) for m in messages] == [
        ("inbound", "requester", "not_applicable"),
        ("outbound", "system", "sent"),
    ]
    # An automatic message is not the desk responding.
    ticket = (await client.get("/api/v1/tickets/1", headers=auth("admin"))).json()
    assert ticket["first_response_at"] is None and ticket["status"] == "new"
    assert (await audit_events(client, auth, action="ticket.acknowledged"))[0]["resource_id"] == "1"


async def test_acknowledgement_only_once_per_ticket_and_custom_text(client, auth, worker, graph):
    reply = mail(2, subject="RE: [ZEN-1] Problem 1")
    graph.inbox = [mail(1), reply]
    await channel(
        client,
        auth,
        acknowledge_new_tickets=True,
        acknowledgement_text="Got it: {ticket_key}. {ticket_key}! {other} {0}",
    )
    await poll(worker)
    await worker.run_once()
    (ack,) = graph.sent  # the follow-up on the same ticket is not acknowledged again
    # Only {ticket_key} is substituted; nothing else in the template is interpreted.
    assert ack["message"]["body"]["content"] == "Got it: ZEN-1. ZEN-1! {other} {0}"


async def test_no_acknowledgement_without_an_address_or_for_skipped_mail(
    client, auth, worker, graph
):
    no_address = mail(1)
    no_address["from"] = {"emailAddress": {"name": "Ghost"}}
    auto = mail(2, internetMessageHeaders=[{"name": "Auto-Submitted", "value": "auto-replied"}])
    graph.inbox = [no_address, auto]
    await channel(client, auth, acknowledge_new_tickets=True)
    await poll(worker)
    await worker.run_once()
    assert len(await tickets(client, auth)) == 1
    assert graph.sent == []  # nobody to tell, and auto-replies never get an answer (no loops)
