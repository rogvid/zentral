from __future__ import annotations

from typing import Any

import httpx


async def make_team(client: httpx.AsyncClient, auth, name: str, members: list[str] = ()) -> str:
    from tests.conftest import SUBJECTS

    r = await client.post("/api/v1/teams", headers=auth("admin"), json={"name": name})
    assert r.status_code == 201, r.text
    team_id = r.json()["id"]
    for member in members:
        r = await client.post(
            f"/api/v1/teams/{team_id}/members",
            headers=auth("admin"),
            json={"user_id": SUBJECTS[member]},
        )
        assert r.status_code == 200, r.text
    return team_id


async def make_ticket(client: httpx.AsyncClient, auth, who: str = "admin", **fields: Any) -> dict:
    payload = {"subject": "Printer broken", "body": "It will not print.", **fields}
    r = await client.post("/api/v1/tickets", headers=auth(who), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def make_source(
    client: httpx.AsyncClient, auth, connector: str = "webhook", **fields: Any
) -> dict:
    payload = {"name": f"{connector}-source", "connector": connector, **fields}
    r = await client.post("/api/v1/sources", headers=auth("admin"), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def audit_events(client: httpx.AsyncClient, auth, **params: Any) -> list[dict]:
    r = await client.get("/api/v1/audit/events", headers=auth("auditor"), params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def mcp_call(client: httpx.AsyncClient, headers: dict, tool: str, **arguments: Any) -> dict:
    r = await client.post(
        "/mcp",
        headers={**headers, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]
