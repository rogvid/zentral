"""Tickets are about a system; how that link is made is configurable."""

from __future__ import annotations

import pytest

from tests.conftest import make_settings
from tests.helpers import audit_events, make_source, make_team, make_ticket, mcp_call
from zentral.ai.base import Classification, ClassificationInput, SystemHint, system_prompt
from zentral.ai.providers.rules import RulesProvider


async def make_system(client, auth, key, name, **fields) -> dict:
    r = await client.post(
        "/api/v1/systems", headers=auth("admin"), json={"key": key, "name": name, **fields}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def get(client, auth, ticket_id, who="admin") -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}", headers=auth(who))).json()


async def test_catalogue_crud_and_permissions(client, auth):
    pay = await make_system(
        client, auth, "payments", "Payment platform", keywords=["invoice", "checkout"]
    )
    assert pay["active"] is True
    h = auth("admin")
    for bad in (
        {"key": "Has Spaces", "name": "x"},
        {"key": "", "name": "x"},
        {"key": "ok"},
        {"key": "ok", "name": "x", "default_team_id": "00000000-0000-4000-8000-000000000000"},
    ):
        assert (await client.post("/api/v1/systems", headers=h, json=bad)).status_code == 422, bad
    dup = await client.post("/api/v1/systems", headers=h, json={"key": "payments", "name": "Other"})
    assert dup.status_code == 409

    # Everyone who works tickets can read the catalogue; only admins change it.
    assert [
        s["key"] for s in (await client.get("/api/v1/systems", headers=auth("viewer"))).json()
    ] == ["payments"]
    assert (
        await client.post("/api/v1/systems", headers=auth("agent"), json={"key": "x", "name": "x"})
    ).status_code == 403
    assert (await client.get("/api/v1/systems", headers=auth("auditor"))).status_code == 403

    r = await client.put(
        f"/api/v1/systems/{pay['id']}",
        headers=h,
        json={"key": "payments", "name": "Payments", "active": False},
    )
    assert r.json()["active"] is False
    assert (await client.get("/api/v1/systems", headers=auth("agent"))).json() == []
    listed = (
        await client.get("/api/v1/systems", headers=h, params={"include_inactive": "true"})
    ).json()
    assert len(listed) == 1
    # Non-admins cannot peek at retired systems.
    assert (
        await client.get(
            "/api/v1/systems", headers=auth("agent"), params={"include_inactive": "true"}
        )
    ).json() == []
    actions = {e["action"] for e in await audit_events(client, auth, resource_type="system")}
    assert actions == {"system.created", "system.updated", "access.denied"}


async def test_set_manually_filter_and_route_to_owning_team(client, auth):
    hr_team = await make_team(client, auth, "People Ops")
    hr = await make_system(client, auth, "hr-portal", "HR portal", default_team_id=hr_team)
    web = await make_system(client, auth, "webshop", "Web shop")
    ticket = await make_ticket(client, auth, system_id=web["id"])
    assert (ticket["system_key"], ticket["system_name"], ticket["team_id"]) == (
        "webshop",
        "Web shop",
        None,
    )

    r = await client.patch(
        f"/api/v1/tickets/{ticket['id']}", headers=auth("admin"), json={"system_id": hr["id"]}
    )
    body = r.json()
    # Nobody owned the ticket yet, so the system's team takes it.
    assert (body["system_key"], body["team_name"]) == ("hr-portal", "People Ops")
    other = await make_ticket(client, auth)

    async def ids(**params):
        r = await client.get("/api/v1/tickets", headers=auth("admin"), params=params)
        return {t["id"] for t in r.json()["items"]}

    assert await ids(system_id=hr["id"]) == {ticket["id"]}
    assert await ids(no_system="true") == {other["id"]}

    r = await client.patch(
        f"/api/v1/tickets/{ticket['id']}", headers=auth("admin"), json={"system_id": None}
    )
    assert (
        r.json()["system_id"] is None and r.json()["team_name"] == "People Ops"
    )  # routing is kept
    bad = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        headers=auth("admin"),
        json={"system_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert bad.status_code == 422
    changes = [
        e["details"]["changes"] for e in await audit_events(client, auth, action="ticket.updated")
    ]
    assert any("system_id" in c for c in changes)


async def test_existing_team_is_not_overridden_by_system(client, auth):
    desk = await make_team(client, auth, "Service Desk")
    owners = await make_team(client, auth, "Payments team")
    pay = await make_system(client, auth, "payments", "Payments", default_team_id=owners)
    ticket = await make_ticket(client, auth, team_id=desk, system_id=pay["id"])
    assert ticket["team_name"] == "Service Desk"


async def test_channel_default_and_sender_hint(client, auth):
    await make_system(client, auth, "payments", "Payments")
    mon = await make_system(client, auth, "monitoring", "Monitoring")
    source = await make_source(client, auth, default_system_id=mon["id"])
    hook = {"Authorization": f"Bearer {source['ingest_token']}"}
    url = f"/api/v1/hooks/{source['id']}"

    await client.post(url, headers=hook, json={"title": "Disk full"})
    await client.post(
        url, headers=hook, json={"title": "Card declined", "system": "Payments"}
    )  # case-insensitive
    await client.post(url, headers=hook, json={"title": "Unknown", "system": "does-not-exist"})
    keys = [(await get(client, auth, i))["system_key"] for i in (1, 2, 3)]
    # channel default / explicit hint wins / unknown hint falls back to the default
    assert keys == ["monitoring", "payments", "monitoring"]

    bad = await client.post(
        "/api/v1/sources",
        headers=auth("admin"),
        json={
            "name": "x",
            "connector": "webhook",
            "default_system_id": "00000000-0000-4000-8000-000000000000",
        },
    )
    assert bad.status_code in (409, 422)


async def test_ai_suggests_system_from_allow_list(client, auth, worker, runtime):
    from tests.test_ai import use_fake

    team = await make_team(client, auth, "People Ops")
    await make_system(
        client, auth, "hr-portal", "HR portal", description="Leave, payslips", default_team_id=team
    )
    await make_system(client, auth, "payments", "Payments")

    def answer(**kw):
        base = {
            "category": "access",
            "priority": "normal",
            "summary": "s",
            "confidence": 0.9,
            "system": "",
        }
        return Classification(**{**base, **kw})

    fake = use_fake(runtime, result=answer(system="HR-Portal"))
    t = await make_ticket(client, auth)
    await worker.run_once()
    t = await get(client, auth, t["id"])
    assert (t["system_key"], t["ai_system_key"], t["team_name"]) == (
        "hr-portal",
        "hr-portal",
        "People Ops",
    )
    assert [s.key for s in fake.seen[0].systems] == ["hr-portal", "payments"]

    # Not in the catalogue -> ignored. Low confidence -> suggestion only. Human choice -> kept.
    for result, expected in (
        (answer(system="nuclear-codes"), (None, None)),
        (answer(system="payments", confidence=0.2), (None, "payments")),
    ):
        use_fake(runtime, result=result)
        t = await make_ticket(client, auth)
        await worker.run_once()
        t = await get(client, auth, t["id"])
        assert (t["system_key"], t["ai_system_key"]) == expected
    use_fake(runtime, result=answer(system="payments"))
    hr = (await client.get("/api/v1/systems", headers=auth("admin"))).json()[0]
    t = await make_ticket(client, auth, system_id=hr["id"])
    await worker.run_once()
    assert (await get(client, auth, t["id"]))["system_key"] == "hr-portal"
    details = (await audit_events(client, auth, action="ticket.classified"))[0]["details"]
    assert details["system"] == "payments"


async def test_rules_fallback_matches_keywords_and_refuses_to_guess():
    provider = RulesProvider(make_settings(), None)
    systems = (
        SystemHint("payments", "Payment platform", keywords=("invoice", "checkout")),
        SystemHint("hr-portal", "HR portal", keywords=("payslip", "leave request")),
    )

    async def run(text):
        out = await provider.classify(ClassificationInput(text, "", ("other",), systems))
        return out.system

    assert await run("Checkout fails with error 500") == "payments"
    assert await run("My leave request is stuck") == "hr-portal"
    assert await run("The HR portal is slow") == "hr-portal"
    assert await run("Printer is broken") == ""
    assert await run("Invoice shows wrong payslip") == ""  # ambiguous: no suggestion
    assert await run("prepayments are fine") == ""  # whole words only


def test_prompt_lists_systems_and_forbids_guessing():
    prompt = system_prompt(("other",), (SystemHint("payments", "Payments", "Card processing"),))
    assert "payments: Payments — Card processing" in prompt and "Do not guess" in prompt
    assert 'always the empty string ""' in system_prompt(("other",))


async def test_automation_rules_use_and_set_system(client, auth, worker):
    team = await make_team(client, auth, "Payments team")
    pay = await make_system(client, auth, "payments", "Payments", keywords=["invoice"])
    h = auth("admin")
    for rule in (
        {
            "name": "invoice -> payments",
            "position": 1,
            "conditions": {"subject_contains": ["refund"]},
            "actions": {"set_system_id": pay["id"]},
        },
        {
            "name": "payments urgent",
            "position": 2,
            "conditions": {"system_id": pay["id"]},
            "actions": {"set_team_id": team, "set_priority": "high"},
        },
    ):
        assert (await client.post("/api/v1/rules", headers=h, json=rule)).status_code == 201
    t = await make_ticket(client, auth, subject="Refund not received")
    await worker.run_once()
    t = await get(client, auth, t["id"])
    assert (t["system_key"], t["team_name"], t["priority"]) == ("payments", "Payments team", "high")


async def test_system_can_be_required_before_resolving(client, auth, runtime):
    runtime.settings.require_system_to_resolve = True
    pay = await make_system(client, auth, "payments", "Payments")
    t = await make_ticket(client, auth)
    url = f"/api/v1/tickets/{t['id']}"
    r = await client.patch(url, headers=auth("admin"), json={"status": "resolved"})
    assert r.status_code == 422 and "system" in r.json()["detail"]
    assert (
        await client.patch(url, headers=auth("admin"), json={"status": "pending"})
    ).status_code == 200
    r = await client.patch(
        url, headers=auth("admin"), json={"status": "resolved", "system_id": pay["id"]}
    )
    assert r.status_code == 200 and r.json()["status"] == "resolved"


async def test_deleting_a_system_keeps_tickets(client, auth):
    pay = await make_system(client, auth, "payments", "Payments")
    t = await make_ticket(client, auth, system_id=pay["id"])
    assert (
        await client.delete(f"/api/v1/systems/{pay['id']}", headers=auth("admin"))
    ).status_code == 204
    assert (await get(client, auth, t["id"]))["system_id"] is None


async def test_systems_over_mcp(client, auth):
    pay = await make_system(client, auth, "payments", "Payments")
    bot = auth("bot")
    listed = await mcp_call(client, bot, "list_systems")
    assert listed["structuredContent"]["result"][0]["key"] == "payments"
    t = await make_ticket(client, auth)
    r = await mcp_call(client, bot, "update_ticket", ticket_id=t["id"], system_id=pay["id"])
    assert r["structuredContent"]["system_key"] == "payments"
    found = await mcp_call(client, bot, "search_tickets", system_id=pay["id"])
    assert found["structuredContent"]["total"] == 1


@pytest.mark.parametrize("field", ["system_id", "set_system_id"])
async def test_rule_schema_accepts_system_fields(client, auth, field):
    pay = await make_system(client, auth, "payments", "Payments")
    body = {"name": "r", "conditions": {}, "actions": {}}
    body["conditions" if field == "system_id" else "actions"][field] = pay["id"]
    assert (await client.post("/api/v1/rules", headers=auth("admin"), json=body)).status_code == 201
