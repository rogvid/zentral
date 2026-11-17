from __future__ import annotations

import pytest

from tests.helpers import audit_events, make_team, make_ticket
from zentral.security.rbac import ROLE_PERMISSIONS, Role, permissions_for
from zentral.security.rbac import Permission as P


def test_unknown_roles_grant_nothing():
    assert permissions_for(frozenset({"Global Administrator", "zentral.admin", ""})) == frozenset()


def test_every_role_is_mapped():
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_segregation_of_duties():
    admin, auditor = ROLE_PERMISSIONS[Role.ADMIN], ROLE_PERMISSIONS[Role.AUDITOR]
    privacy, bot = ROLE_PERMISSIONS[Role.PRIVACY_OFFICER], ROLE_PERMISSIONS[Role.AUTOMATION]
    # Admins configure the system but cannot export or erase personal data.
    assert not admin & {P.PRIVACY_EXPORT, P.PRIVACY_ERASE}
    # Auditors read the trail, never ticket content.
    assert auditor == {P.AUDIT_READ}
    # Privacy officers cannot reconfigure the system.
    assert not privacy & {P.TEAM_MANAGE, P.SOURCE_MANAGE, P.RULE_MANAGE}
    # AI agents never see PII and can never message a requester.
    assert not bot & {P.TICKET_READ_PII, P.TICKET_REPLY, P.TICKET_READ_ALL}
    assert ROLE_PERMISSIONS[Role.INGEST] == {P.INGEST_WRITE}
    assert ROLE_PERMISSIONS[Role.VIEWER] == {P.TICKET_READ}


FORBIDDEN = [
    ("viewer", "POST", "/api/v1/tickets", {"subject": "s", "body": "b"}),
    ("auditor", "GET", "/api/v1/tickets", None),
    ("auditor", "POST", "/api/v1/tickets", {"subject": "s", "body": "b"}),
    ("agent", "POST", "/api/v1/teams", {"name": "x"}),
    ("agent", "GET", "/api/v1/sources", None),
    ("agent", "GET", "/api/v1/rules", None),
    ("agent", "GET", "/api/v1/audit/events", None),
    ("agent", "POST", "/api/v1/audit/verify", None),
    ("admin", "POST", "/api/v1/privacy/contacts/lookup", {"email": "a@b.io"}),
    ("admin", "POST", "/api/v1/privacy/contacts/00000000-0000-4000-8000-000000000000/erase", None),
    ("agent", "GET", "/api/v1/privacy/contacts/00000000-0000-4000-8000-000000000000/export", None),
    ("ingest", "GET", "/api/v1/tickets", None),
    ("ingest", "GET", "/api/v1/teams", None),
    ("viewer", "GET", "/api/v1/users", None),
    ("bot", "GET", "/api/v1/connectors", None),
]


@pytest.mark.parametrize(("who", "method", "url", "body"), FORBIDDEN)
async def test_forbidden_matrix(client, auth, who, method, url, body):
    r = await client.request(method, url, headers=auth(who), json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "forbidden"


async def test_denied_attempts_are_audited(client, auth):
    await client.get("/api/v1/sources", headers=auth("agent"))
    events = await audit_events(client, auth, outcome="denied")
    assert events[0]["action"] == "access.denied"
    assert events[0]["details"] == {"permission": "source:manage"}


async def test_team_scoping(client, auth):
    net = await make_team(client, auth, "Network", ["agent"])
    hr = await make_team(client, auth, "HR", ["agent2"])
    mine = await make_ticket(client, auth, team_id=net)
    theirs = await make_ticket(client, auth, team_id=hr, subject="Salary question")
    triage = await make_ticket(client, auth)

    listed = (await client.get("/api/v1/tickets", headers=auth("agent"))).json()
    assert {t["id"] for t in listed["items"]} == {mine["id"], triage["id"]}
    assert listed["total"] == 2

    # Out-of-scope tickets look exactly like missing ones, for every operation…
    for method, url, body in [
        ("GET", f"/api/v1/tickets/{theirs['id']}", None),
        ("GET", f"/api/v1/tickets/{theirs['id']}/messages", None),
        ("PATCH", f"/api/v1/tickets/{theirs['id']}", {"status": "closed"}),
        ("POST", f"/api/v1/tickets/{theirs['id']}/messages", {"kind": "note", "body": "x"}),
    ]:
        r = await client.request(method, url, headers=auth("agent"), json=body)
        assert (r.status_code, r.json()) == (404, {"detail": "ticket not found"})
    missing = await client.get("/api/v1/tickets/999999", headers=auth("agent"))
    assert (missing.status_code, missing.json()) == (404, {"detail": "ticket not found"})

    # …but the attempt is on record.
    denied = await audit_events(client, auth, outcome="denied", resource_id=str(theirs["id"]))
    assert len(denied) == 4

    # Admins see everything; viewers without a team see nothing.
    assert (await client.get("/api/v1/tickets", headers=auth("admin"))).json()["total"] == 3
    assert (await client.get("/api/v1/tickets", headers=auth("viewer"))).json()["total"] == 0


async def test_entra_group_grants_team_membership(client, auth):
    group = "11111111-2222-4333-8444-555555555555"
    r = await client.post(
        "/api/v1/teams", headers=auth("admin"), json={"name": "Finance", "entra_group_id": group}
    )
    ticket = await make_ticket(client, auth, team_id=r.json()["id"])
    url = f"/api/v1/tickets/{ticket['id']}"
    assert (await client.get(url, headers=auth("viewer"))).status_code == 404
    assert (await client.get(url, headers=auth("viewer", groups=[group]))).status_code == 200


async def test_assignee_keeps_access_after_reroute(client, auth):
    net = await make_team(client, auth, "Network", ["agent"])
    hr = await make_team(client, auth, "HR")
    ticket = await make_ticket(client, auth, team_id=net)
    url = f"/api/v1/tickets/{ticket['id']}"
    await client.get("/api/v1/me", headers=auth("agent"))
    me = (await client.get("/api/v1/me", headers=auth("agent"))).json()["subject"]
    r = await client.patch(url, headers=auth("admin"), json={"assignee_id": me, "team_id": hr})
    assert r.status_code == 200, r.text
    assert (await client.get(url, headers=auth("agent"))).status_code == 200


async def test_automation_role_can_route_but_not_assign_or_reply(client, auth):
    team = await make_team(client, auth, "Network")
    ticket = await make_ticket(client, auth, requester_email="jane@example.com")
    url = f"/api/v1/tickets/{ticket['id']}"
    bot = auth("bot")
    assert (await client.patch(url, headers=bot, json={"team_id": team})).status_code == 200
    # Routed away from triage: the bot loses sight of it again (least privilege).
    assert (await client.get(url, headers=bot)).status_code == 404

    other = await make_ticket(client, auth, requester_email="jane@example.com")
    url = f"/api/v1/tickets/{other['id']}"
    r = await client.patch(url, headers=bot, json={"assignee_id": "someone"})
    assert r.status_code == 403
    r = await client.post(f"{url}/messages", headers=bot, json={"kind": "outbound", "body": "hi"})
    assert (r.status_code, r.json()["missing_permission"]) == (403, "ticket:reply")
    r = await client.post(f"{url}/messages", headers=bot, json={"kind": "note", "body": "triaged"})
    assert r.status_code == 201


async def test_team_membership_hidden_from_non_admins(client, auth):
    await make_team(client, auth, "Network", ["agent"])
    for_agent = (await client.get("/api/v1/teams", headers=auth("agent"))).json()
    assert for_agent[0]["member_ids"] == []
    for_admin = (await client.get("/api/v1/teams", headers=auth("admin"))).json()
    assert len(for_admin[0]["member_ids"]) == 1
