from __future__ import annotations

import json

from tests.helpers import audit_events, make_team, make_ticket, mcp_call

ACCEPT = {"Accept": "application/json, text/event-stream"}


async def rpc(client, headers, method, params=None):
    return await client.post(
        "/mcp",
        headers={**headers, **ACCEPT},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


async def test_mcp_requires_a_valid_token(client, issuer):
    for headers in ({}, {"Authorization": "Bearer forged"}):
        r = await rpc(client, headers, "tools/list")
        assert r.status_code == 401
        challenge = r.headers["www-authenticate"]
        assert challenge.startswith("Bearer") and "resource_metadata=" in challenge
    expired = issuer.issue(subject="x", roles=["Zentral.Agent"], lifetime=-3600)
    assert (
        await rpc(client, {"Authorization": f"Bearer {expired}"}, "tools/list")
    ).status_code == 401


async def test_protected_resource_metadata(client):
    meta = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert meta["resource"] == "http://testserver/mcp"
    assert meta["authorization_servers"] and meta["bearer_methods_supported"] == ["header"]


async def test_initialize_and_tool_listing(client, auth):
    r = await rpc(
        client,
        auth("bot"),
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "zentral"
    assert "untrusted" in result["instructions"]
    tools = (await rpc(client, auth("bot"), "tools/list")).json()["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "whoami",
        "search_tickets",
        "get_ticket",
        "create_ticket",
        "update_ticket",
        "add_note",
        "reply_to_requester",
        "list_teams",
            "list_categories",
            "list_systems",
    }
    assert all(t["description"] for t in tools)


async def test_agent_gets_redacted_data_and_least_privilege(client, auth):
    team = await make_team(client, auth, "Network")
    ticket = await make_ticket(
        client,
        auth,
        requester_email="jane.doe@example.com",
        requester_name="Jane Doe",
        subject="VPN for jane.doe@example.com",
        body="Call me on +45 12 34 56 78",
    )
    bot = auth("bot")

    who = (await mcp_call(client, bot, "whoami"))["structuredContent"]
    assert who["pii_redacted"] is True and "ticket:reply" not in who["permissions"]

    found = await mcp_call(client, bot, "search_tickets", query="vpn")
    assert found["structuredContent"]["total"] == 1

    read = await mcp_call(client, bot, "get_ticket", ticket_id=ticket["id"])
    text = json.dumps(read)
    assert "jane.doe" not in text and "Jane Doe" not in text and "12 34 56 78" not in text
    assert "[EMAIL]" in text and "[PHONE]" in text

    note = await mcp_call(
        client, bot, "add_note", ticket_id=ticket["id"], body="Looks like a VPN issue."
    )
    assert note["isError"] is False

    denied = await mcp_call(
        client, bot, "reply_to_requester", ticket_id=ticket["id"], body="Hello Jane"
    )
    assert denied["isError"] is True
    assert "ticket:reply" in denied["content"][0]["text"]

    routed = await mcp_call(
        client, bot, "update_ticket", ticket_id=ticket["id"], team_id=team, category="network"
    )
    assert routed["structuredContent"]["team_name"] == "Network"

    # Every call — including the refused one — is on the audit trail, attributed to the agent.
    events = await audit_events(client, auth, actor_id=who["subject"], limit=100)
    assert {e["channel"] for e in events} == {"mcp"}
    assert {"ticket.read", "ticket.note_added", "ticket.updated", "access.denied"} <= {
        e["action"] for e in events
    }
    messages = (
        await client.get(f"/api/v1/tickets/{ticket['id']}/messages", headers=auth("admin"))
    ).json()
    assert [m["kind"] for m in messages] == ["inbound", "note"]  # nothing was sent


async def test_mcp_respects_team_scope_and_validates_input(client, auth):
    team = await make_team(client, auth, "HR")
    hidden = await make_ticket(client, auth, team_id=team)
    bot = auth("bot")
    r = await mcp_call(client, bot, "get_ticket", ticket_id=hidden["id"])
    assert r["isError"] is True and r["content"][0]["text"].endswith("Ticket not found.")
    r = await mcp_call(client, bot, "get_ticket", ticket_id=424242)
    assert r["content"][0]["text"].endswith("Ticket not found.")
    r = await mcp_call(client, bot, "update_ticket", ticket_id=hidden["id"], status="closed")
    assert r["isError"] is True
    visible = await make_ticket(client, auth)
    for bad in ({"category": "made-up"}, {"status": "exploded"}, {"tags": ["<b>"]}):
        r = await mcp_call(client, bot, "update_ticket", ticket_id=visible["id"], **bad)
        assert r["isError"] is True, bad
    assert (await mcp_call(client, bot, "list_categories"))["structuredContent"]["result"][
        0
    ] == "incident"


async def test_human_agent_over_mcp_sees_pii_and_can_create(client, auth):
    created = await mcp_call(
        client, auth("agent"), "create_ticket", subject="From MCP", body="Hello"
    )
    ticket = created["structuredContent"]
    assert ticket["channel"] == "mcp" and ticket["pii_redacted"] is False
    auditor = await mcp_call(client, auth("auditor"), "search_tickets")
    assert auditor["isError"] is True


async def test_mcp_rejects_foreign_host_header(client, auth):
    r = await client.post(
        "/mcp",
        headers={**auth("bot"), **ACCEPT, "Host": "evil.example"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert r.status_code in (400, 421)
