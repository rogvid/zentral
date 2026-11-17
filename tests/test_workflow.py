"""Desk workflow: saved replies, merging duplicates, objection to AI processing."""

from __future__ import annotations

from tests.helpers import audit_events, make_source, make_team, make_ticket, mcp_call
from tests.test_ai import FakeProvider, result


async def get(client, auth, ticket_id, who="admin"):
    return await client.get(f"/api/v1/tickets/{ticket_id}", headers=auth(who))


async def messages(client, auth, ticket_id, who="admin") -> list[dict]:
    r = await client.get(f"/api/v1/tickets/{ticket_id}/messages", headers=auth(who))
    assert r.status_code == 200, r.text
    return r.json()


# --- saved replies -------------------------------------------------------------------------


async def test_saved_replies_are_scoped_to_teams(client, auth):
    ops = await make_team(client, auth, "Ops", members=["agent"])
    await make_team(client, auth, "HR", members=["agent2"])
    hr = (await client.get("/api/v1/teams", headers=auth("admin"))).json()
    hr_id = next(t["id"] for t in hr if t["name"] == "HR")
    for name, team in (("Welcome", None), ("Reset VPN", ops), ("Payroll", hr_id)):
        r = await client.post(
            "/api/v1/templates",
            headers=auth("admin"),
            json={"name": name, "body": f"{name} text for {{ticket_key}}", "team_id": team},
        )
        assert r.status_code == 201, r.text

    seen = {
        who: (await client.get("/api/v1/templates", headers=auth(who))).json()
        for who in ("admin", "agent", "agent2")
    }

    def names(who: str) -> list[str]:
        return [t["name"] for t in seen[who]]

    assert names("admin") == ["Payroll", "Reset VPN", "Welcome"]
    assert names("agent") == ["Reset VPN", "Welcome"]
    assert names("agent2") == ["Payroll", "Welcome"]
    assert seen["agent"][0]["team_name"] == "Ops"

    # Only template managers change them; auditors and ingest identities see nothing.
    r = await client.post(
        "/api/v1/templates", headers=auth("agent"), json={"name": "x", "body": "y"}
    )
    assert r.status_code == 403
    assert (await client.get("/api/v1/templates", headers=auth("auditor"))).status_code == 403
    assert (await client.get("/api/v1/templates", headers=auth("ingest"))).status_code == 403


async def test_saved_reply_lifecycle_is_audited_without_content(client, auth):
    r = await client.post(
        "/api/v1/templates", headers=auth("admin"), json={"name": "Hi", "body": "secret text"}
    )
    template = r.json()
    r = await client.patch(
        f"/api/v1/templates/{template['id']}", headers=auth("admin"), json={"body": "new text"}
    )
    assert r.json()["body"] == "new text"
    r = await client.patch(
        f"/api/v1/templates/{template['id']}",
        headers=auth("admin"),
        json={"team_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert r.status_code == 422
    r = await client.delete(f"/api/v1/templates/{template['id']}", headers=auth("admin"))
    assert r.status_code == 204
    assert (await client.get("/api/v1/templates", headers=auth("admin"))).json() == []
    events = await audit_events(client, auth, resource_type="template")
    assert [e["action"] for e in events] == [
        "template.deleted",
        "template.updated",
        "template.created",
    ]
    assert "text" not in str(events)


async def test_saved_reply_input_is_bounded(client, auth):
    r = await client.post(
        "/api/v1/templates", headers=auth("admin"), json={"name": "x", "body": "y" * 20_001}
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/templates", headers=auth("admin"), json={"name": "", "body": "y"}
    )
    assert r.status_code == 422


async def test_agents_read_saved_replies_over_mcp(client, auth):
    await client.post(
        "/api/v1/templates", headers=auth("admin"), json={"name": "Hi", "body": "Hello"}
    )
    out = await mcp_call(client, auth("bot"), "list_saved_replies")
    assert out["structuredContent"]["result"] == [{"name": "Hi", "body": "Hello", "team": None}]


# --- merging --------------------------------------------------------------------------------


async def test_merge_moves_the_conversation_and_closes_the_duplicate(client, auth):
    first = await make_ticket(client, auth, requester_email="jane@example.com", tags=["vpn"])
    dup = await make_ticket(
        client, auth, requester_email="jane@example.com", body="Still broken", tags=["remote"]
    )
    r = await client.post(
        f"/api/v1/tickets/{dup['id']}/merge",
        headers=auth("admin"),
        json={"into_ticket_id": first["id"]},
    )
    assert r.status_code == 200, r.text
    target = r.json()
    assert target["id"] == first["id"] and target["tags"] == ["remote", "vpn"]

    bodies = [m["body"] for m in await messages(client, auth, first["id"])]
    assert "Still broken" in bodies
    assert any(b.startswith(f"{dup['key']} was merged into this ticket") for b in bodies)

    closed = (await get(client, auth, dup["id"])).json()
    assert closed["status"] == "closed"
    assert (closed["merged_into_id"], closed["merged_into_key"]) == (first["id"], first["key"])
    (note,) = await messages(client, auth, dup["id"])
    assert note["body"] == f"Merged into {first['key']}. The conversation continues there."

    merged = await audit_events(client, auth, action="ticket.merged")
    assert (
        merged[0]["resource_id"] == str(dup["id"]) and merged[0]["details"]["into"] == first["id"]
    )
    received = await audit_events(client, auth, action="ticket.merge_received")
    assert received[0]["details"] == {"source": dup["id"], "messages": 1}


async def test_merge_reopens_a_resolved_target(client, auth):
    first = await make_ticket(client, auth, requester_email="jane@example.com")
    await client.patch(
        f"/api/v1/tickets/{first['id']}", headers=auth("admin"), json={"status": "resolved"}
    )
    dup = await make_ticket(client, auth, requester_email="jane@example.com")
    r = await client.post(
        f"/api/v1/tickets/{dup['id']}/merge",
        headers=auth("admin"),
        json={"into_ticket_id": first["id"]},
    )
    assert r.json()["status"] == "open" and r.json()["resolved_at"] is None


async def test_merge_refuses_what_would_mix_people_or_lose_history(client, auth):
    jane = await make_ticket(client, auth, requester_email="jane@example.com")
    bob = await make_ticket(client, auth, requester_email="bob@example.com")
    internal = await make_ticket(client, auth)
    url = lambda t: f"/api/v1/tickets/{t['id']}/merge"  # noqa: E731

    async def merge(source, target, who="admin"):
        return await client.post(
            url(source), headers=auth(who), json={"into_ticket_id": target["id"]}
        )

    r = await merge(jane, bob)
    assert r.status_code == 422 and "same requester" in r.text
    assert (await merge(jane, jane)).status_code == 422
    # A ticket without a requester (internal) can be folded into anyone's.
    assert (await merge(internal, jane)).status_code == 200
    assert (await merge(internal, bob)).status_code == 422  # already merged
    later = await make_ticket(client, auth, requester_email="jane@example.com")
    assert (await merge(later, internal)).status_code == 422  # target was merged itself
    assert (await merge(jane, {"id": 999})).status_code == 404
    # Seeing both tickets is not enough: merging changes them.
    team = await make_team(client, auth, "Ops", members=["viewer"])
    a = await make_ticket(client, auth, requester_email="ann@example.com", team_id=team)
    b = await make_ticket(client, auth, requester_email="ann@example.com", team_id=team)
    assert (await merge(a, b, who="viewer")).status_code == 403


async def test_merge_respects_team_scope(client, auth):
    ops = await make_team(client, auth, "Ops", members=["agent"])
    hr = await make_team(client, auth, "HR")
    mine = await make_ticket(client, auth, requester_email="jane@example.com", team_id=ops)
    theirs = await make_ticket(client, auth, requester_email="jane@example.com", team_id=hr)
    for source, target in ((mine, theirs), (theirs, mine)):
        r = await client.post(
            f"/api/v1/tickets/{source['id']}/merge",
            headers=auth("agent"),
            json={"into_ticket_id": target["id"]},
        )
        assert r.status_code == 404  # existence of the other team's ticket is not disclosed


async def test_replies_on_a_merged_thread_land_on_the_target(client, auth):
    source = await make_source(client, auth)

    async def hook(thread, text):
        r = await client.post(
            f"/api/v1/hooks/{source['id']}",
            headers={"Authorization": f"Bearer {source['ingest_token']}"},
            json={
                "title": text,
                "description": text,
                "thread_key": thread,
                "requester_email": "jane@example.com",
            },
        )
        assert r.status_code == 202, r.text
        return r.json()["tickets"]

    a = await hook("thread-a", "VPN down")
    b = await hook("thread-b", "VPN still down")
    a_id, b_id = int(a.split("-")[1]), int(b.split("-")[1])
    r = await client.post(
        f"/api/v1/tickets/{b_id}/merge", headers=auth("admin"), json={"into_ticket_id": a_id}
    )
    assert r.status_code == 200
    assert await hook("thread-b", "Any news?") == a  # follows the merge
    assert "Any news?" in [m["body"] for m in await messages(client, auth, a_id)]


async def test_agents_can_merge_over_mcp(client, auth):
    a = await make_ticket(client, auth)
    b = await make_ticket(client, auth)
    out = await mcp_call(
        client, auth("bot"), "merge_tickets", duplicate_ticket_id=b["id"], into_ticket_id=a["id"]
    )
    assert out["structuredContent"]["id"] == a["id"]


# --- objection to AI processing --------------------------------------------------------------


async def lookup(client, auth, email):
    r = await client.post(
        "/api/v1/privacy/contacts/lookup", headers=auth("privacy"), json={"email": email}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def object_to_ai(client, auth, contact_id, objected=True, who="privacy"):
    return await client.put(
        f"/api/v1/privacy/contacts/{contact_id}/ai-objection",
        headers=auth(who),
        json={"objected": objected},
    )


async def test_objection_keeps_tickets_away_from_models_and_agents(client, auth, runtime, worker):
    fake = FakeProvider(runtime.settings, runtime.http, result=result())
    runtime.ai = fake
    first = await make_ticket(client, auth, requester_email="jane@example.com", subject="VPN")
    contact = await lookup(client, auth, "jane@example.com")
    assert contact["ai_objected_at"] is None

    r = await object_to_ai(client, auth, contact["id"])
    assert r.status_code == 200 and r.json()["ai_objected_at"]

    # The already-queued ticket and a new one are both kept from the model.
    second = await make_ticket(client, auth, requester_email="jane@example.com")
    await worker.run_once()
    assert fake.seen == []
    for t in (first, second):
        ticket = (await get(client, auth, t["id"])).json()
        assert ticket["ai_excluded"] is True and ticket["ai_category"] is None
    skipped = await audit_events(client, auth, action="ticket.classification_skipped")
    assert len(skipped) == 2 and skipped[0]["details"] == {"reason": "objection"}

    # People still see the ticket; AI agents do not, whichever door they use.
    assert (await get(client, auth, first["id"], who="agent")).status_code == 200
    assert (await get(client, auth, first["id"], who="bot")).status_code == 404
    found = await mcp_call(client, auth("admin"), "search_tickets")
    assert found["structuredContent"]["total"] == 0
    denied = await mcp_call(client, auth("admin"), "get_ticket", ticket_id=first["id"])
    assert denied["isError"]
    listed = (await client.get("/api/v1/tickets", headers=auth("bot"))).json()
    assert listed["total"] == 0

    # Withdrawal restores the normal path for new work.
    r = await object_to_ai(client, auth, contact["id"], objected=False)
    assert r.json()["ai_objected_at"] is None
    assert (await get(client, auth, first["id"], who="bot")).status_code == 200
    actions = [e["action"] for e in await audit_events(client, auth, resource_type="contact")]
    assert "privacy.ai_objection_recorded" in actions
    assert "privacy.ai_objection_withdrawn" in actions


async def test_only_privacy_officers_record_objections(client, auth):
    await make_ticket(client, auth, requester_email="jane@example.com")
    contact = await lookup(client, auth, "jane@example.com")
    for who in ("admin", "agent", "auditor", "bot"):
        assert (await object_to_ai(client, auth, contact["id"], who=who)).status_code == 403
    r = await object_to_ai(client, auth, "00000000-0000-4000-8000-000000000000")
    assert r.status_code == 404


async def test_objection_is_part_of_the_export(client, auth):
    await make_ticket(client, auth, requester_email="jane@example.com")
    contact = await lookup(client, auth, "jane@example.com")
    await object_to_ai(client, auth, contact["id"])
    export = (
        await client.get(
            f"/api/v1/privacy/contacts/{contact['id']}/export", headers=auth("privacy")
        )
    ).json()
    assert export["data_subject"]["objected_to_ai_processing"]
