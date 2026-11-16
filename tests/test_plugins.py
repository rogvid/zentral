"""The connector plug-in contract, exercised with the example Slack connector."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.conftest import make_settings
from tests.helpers import make_source
from zentral.connectors.registry import ConnectorRegistry

sys.path.insert(0, str(Path(__file__).parents[1] / "examples/zentral-connector-slack/src"))
from zentral_connector_slack import SlackConnector

SECRET = "slack-signing-secret"


def signed(payload: dict, *, secret: str = SECRET, age: int = 0) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = str(int(time.time()) - age)
    digest = hmac.new(
        secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return body, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": f"v0={digest}"}


def mention(text="<@U0BOT> the HR portal is down", ts="1700000000.000100", **event):
    return {
        "type": "event_callback",
        "event_id": f"Ev{ts}",
        "event": {
            "type": "app_mention",
            "user": "U123",
            "text": text,
            "channel": "C42",
            "ts": ts,
            **event,
        },
    }


@pytest.fixture
async def slack(client, auth, runtime):
    runtime.registry._connectors["slack"] = SlackConnector  # what the entry point does
    runtime.settings.outbound_host_allowlist = [
        *runtime.settings.outbound_host_allowlist,
        "slack.com",
    ]
    source = await make_source(
        client, auth, "slack", secrets={"signing_secret": SECRET, "bot_token": "xoxb-test"}
    )
    return f"/api/v1/hooks/{source['id']}"


async def test_only_allow_listed_connectors_are_loaded():
    assert ConnectorRegistry.load(make_settings(enabled_connectors=["webhook"])).names() == [
        "webhook"
    ]
    assert ConnectorRegistry.load(make_settings(enabled_connectors=[])).names() == []
    described = ConnectorRegistry.load(make_settings()).describe()
    assert {d["name"] for d in described} == {"webhook", "teams", "email_graph"}


async def test_plugin_ingests_threads_and_replies(client, auth, slack, worker, outbound):
    body, headers = signed({"type": "url_verification", "challenge": "abc"})
    r = await client.post(slack, content=body, headers=headers)
    assert (r.status_code, r.json()) == (200, {"challenge": "abc"})

    body, headers = signed(mention())
    assert (await client.post(slack, content=body, headers=headers)).status_code == 200
    # A reply in the same Slack thread, and Slack's retry of the first event.
    reply, reply_headers = signed(
        mention("more detail", ts="1700000000.000200", thread_ts="1700000000.000100")
    )
    await client.post(slack, content=reply, headers=reply_headers)
    await client.post(slack, content=body, headers=headers)
    # Bot messages are ignored (otherwise our own replies would loop back in).
    bot, bot_headers = signed(mention("I am a bot", ts="1700000000.000300", bot_id="B1"))
    await client.post(slack, content=bot, headers=bot_headers)

    tickets = (await client.get("/api/v1/tickets", headers=auth("admin"))).json()["items"]
    assert len(tickets) == 1 and tickets[0]["channel"] == "slack"
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert [m["body"] for m in messages] == ["<@U0BOT> the HR portal is down", "more detail"]

    # An agent's reply goes back into the originating Slack thread.
    outbound.on("slack.com/api/chat.postMessage", lambda r: httpx.Response(200, json={"ok": True}))
    await client.post(
        "/api/v1/tickets/1/messages",
        headers=auth("admin"),
        json={"kind": "outbound", "body": "On it."},
    )
    await worker.run_once()
    sent = next(r for r in outbound.requests if "slack.com" in str(r.url))
    assert sent.headers["authorization"] == "Bearer xoxb-test"
    assert json.loads(sent.content) == {
        "channel": "C42",
        "thread_ts": "1700000000.000100",
        "text": "*ZEN-1* — On it.",
    }
    messages = (await client.get("/api/v1/tickets/1/messages", headers=auth("admin"))).json()
    assert messages[-1]["delivery_status"] == "sent"


async def test_plugin_rejects_forged_and_replayed_requests(client, auth, slack):
    body, headers = signed(mention())
    _, wrong = signed(mention(), secret="other")
    _, old = signed(mention(), age=3600)
    for content, h in (
        (body.replace(b"HR portal", b"payroll db"), headers),
        (body, wrong),
        (body, old),  # valid signature, but replayed an hour later
        (body, {}),
    ):
        assert (await client.post(slack, content=content, headers=h)).status_code == 401
    assert (await client.get("/api/v1/tickets", headers=auth("admin"))).json()["total"] == 0
