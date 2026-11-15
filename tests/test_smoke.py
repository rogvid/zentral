from __future__ import annotations


async def test_health_and_me(client, auth):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200
    r = await client.get("/api/v1/me", headers=auth("agent"))
    assert r.status_code == 200, r.text
    assert "ticket:reply" in r.json()["permissions"]


async def test_ticket_roundtrip(client, auth, worker):
    r = await client.post(
        "/api/v1/tickets",
        headers=auth("agent"),
        json={"subject": "VPN is down", "body": "Cannot connect", "requester_email": "j@x.io"},
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert await worker.run_once() == 1
    t = (await client.get(f"/api/v1/tickets/{tid}", headers=auth("agent"))).json()
    assert t["ai_category"] == "network"
    assert t["key"] == f"ZEN-{tid}"
