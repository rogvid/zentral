"""The life of a ticket: service-level targets, pausing, overdue, auto-close, insights."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import SUBJECTS, make_settings
from tests.helpers import audit_events, make_source, make_team, make_ticket, mcp_call
from zentral.config import ConfigurationError, Settings
from zentral.db.models import Job, Ticket


async def get(client, auth, ticket_id, who="admin") -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}", headers=auth(who))).json()


async def shift(db, ticket_id: int, **deltas: timedelta) -> None:
    """Move a ticket's timestamps into the past, as if time had passed."""
    ticket = await db.get(Ticket, ticket_id)
    await db.refresh(ticket)
    for field, delta in deltas.items():
        setattr(ticket, field, getattr(ticket, field) - delta)
    await db.commit()


def hours(a: str, b: str) -> float:
    from datetime import datetime

    return (datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds() / 3600


# --- targets ----------------------------------------------------------------------------------


async def test_targets_follow_priority_and_are_replanned(client, auth):
    t = await make_ticket(client, auth, priority="urgent", requester_email="jane@example.com")
    assert round(hours(t["response_due_at"], t["created_at"]), 2) == 1.0
    assert round(hours(t["resolve_due_at"], t["created_at"]), 2) == 8.0
    assert (t["response_state"], t["resolve_state"]) == ("due", "due")

    t = (
        await client.patch(
            f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"priority": "low"}
        )
    ).json()
    assert (
        round(hours(t["resolve_due_at"], t["created_at"]), 2) == 168.0
    )  # re-planned from creation

    internal = await make_ticket(client, auth)
    assert internal["response_state"] == "none"  # nobody outside to respond to


async def test_first_response_is_a_reply_not_a_note(client, auth, db):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    url = f"/api/v1/tickets/{t['id']}"
    await client.post(
        f"{url}/messages", headers=auth("admin"), json={"kind": "note", "body": "looking"}
    )
    assert (await get(client, auth, t["id"]))["first_response_at"] is None
    await client.post(
        f"{url}/messages", headers=auth("admin"), json={"kind": "outbound", "body": "On it"}
    )
    t = await get(client, auth, t["id"])
    assert t["first_response_at"] and t["response_state"] == "met"

    late = await make_ticket(client, auth, requester_email="bob@example.com")
    await shift(db, late["id"], response_due_at=timedelta(hours=9))
    await client.post(
        f"/api/v1/tickets/{late['id']}/messages",
        headers=auth("admin"),
        json={"kind": "outbound", "body": "Sorry"},
    )
    assert (await get(client, auth, late["id"]))["response_state"] == "late"


async def test_waiting_on_the_requester_pauses_the_resolution_clock(client, auth, db):
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    url = f"/api/v1/tickets/{t['id']}"
    before = t["resolve_due_at"]
    t = (await client.patch(url, headers=auth("admin"), json={"status": "pending"})).json()
    assert t["resolve_state"] == "paused"

    await shift(db, t["id"], paused_at=timedelta(hours=5))  # five hours of waiting
    t = (await client.patch(url, headers=auth("admin"), json={"status": "open"})).json()
    assert t["resolve_state"] == "due"
    assert round(hours(t["resolve_due_at"], before), 1) == 5.0  # the deadline moved by the wait


async def test_requester_reply_resumes_the_clock(client, auth, db):
    source = await make_source(client, auth)
    hook = {"Authorization": f"Bearer {source['ingest_token']}"}
    url = f"/api/v1/hooks/{source['id']}"
    payload = {"title": "Printer", "thread_key": "p1", "requester_email": "jane@example.com"}
    await client.post(url, headers=hook, json=payload)
    await client.patch("/api/v1/tickets/1", headers=auth("admin"), json={"status": "pending"})
    await shift(db, 1, paused_at=timedelta(hours=2))
    await client.post(url, headers=hook, json={**payload, "description": "still broken"})
    t = await get(client, auth, 1)
    assert (t["status"], t["resolve_state"]) == ("open", "due")


async def test_overdue_is_counted_filtered_and_shown(client, auth, db):
    late = await make_ticket(client, auth, requester_email="jane@example.com", subject="Late one")
    await make_ticket(client, auth, requester_email="bob@example.com", subject="Fine one")
    await shift(db, late["id"], response_due_at=timedelta(hours=9))  # normal: 8 h to reply

    stats = (await client.get("/api/v1/tickets/stats", headers=auth("admin"))).json()
    assert stats["overdue"] == 1
    page = (
        await client.get("/api/v1/tickets", headers=auth("admin"), params={"overdue": "true"})
    ).json()
    assert [t["subject"] for t in page["items"]] == ["Late one"]
    assert page["items"][0]["response_state"] == "breached"

    # Answering clears the response breach; pending stops the resolution clock.
    await client.post(
        f"/api/v1/tickets/{late['id']}/messages",
        headers=auth("admin"),
        json={"kind": "outbound", "body": "Hi"},
    )
    await shift(db, late["id"], resolve_due_at=timedelta(days=4))
    assert (await client.get("/api/v1/tickets/stats", headers=auth("admin"))).json()["overdue"] == 1
    await client.patch(
        f"/api/v1/tickets/{late['id']}", headers=auth("admin"), json={"status": "pending"}
    )
    assert (await client.get("/api/v1/tickets/stats", headers=auth("admin"))).json()["overdue"] == 0


def test_targets_are_configurable_and_validated(monkeypatch):
    monkeypatch.setenv("ZENTRAL_SLA_RESPONSE_HOURS", "urgent=0.5, high=2")
    s = Settings(_env_file=None)
    assert s.sla_response_hours == {"urgent": 0.5, "high": 2.0}
    for bad in ({"sla_response_hours": {"critical": 1}}, {"sla_resolve_hours": {"low": 0}}):
        with pytest.raises(ConfigurationError):
            make_settings(**bad).validate_for_runtime()


async def test_priorities_without_a_target_have_none(client, auth, runtime):
    runtime.settings.sla_response_hours = {"urgent": 1.0}
    runtime.settings.sla_resolve_hours = {}
    t = await make_ticket(client, auth, requester_email="jane@example.com")
    assert (t["response_due_at"], t["resolve_due_at"], t["resolve_state"]) == (None, None, "none")


# --- closing the loop --------------------------------------------------------------------------


async def run_housekeeping(db, worker):
    db.add(Job(kind="retention_sweep", payload={}))
    await db.commit()
    await worker.run_once()


async def test_resolved_tickets_close_after_the_grace_period(client, auth, db, worker, runtime):
    old = await make_ticket(client, auth, subject="Old")
    recent = await make_ticket(client, auth, subject="Recent")
    await worker.run_once()  # classification jobs
    for t in (old, recent):
        await client.patch(
            f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "resolved"}
        )
    await shift(db, old["id"], resolved_at=timedelta(days=runtime.settings.auto_close_days + 1))

    await run_housekeeping(db, worker)
    assert (await get(client, auth, old["id"]))["status"] == "closed"
    assert (await get(client, auth, recent["id"]))["status"] == "resolved"
    (event,) = await audit_events(client, auth, action="ticket.auto_closed")
    assert event["details"] == {"after_days": 7} and event["actor_id"] == "system:worker"
    activity = (
        await client.get(f"/api/v1/tickets/{old['id']}/activity", headers=auth("admin"))
    ).json()
    assert activity[0]["action"] == "ticket.auto_closed"


async def test_auto_close_can_be_turned_off(client, auth, db, worker, runtime):
    runtime.settings.auto_close_days = 0
    t = await make_ticket(client, auth)
    await client.patch(
        f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"status": "resolved"}
    )
    await shift(db, t["id"], resolved_at=timedelta(days=60))  # well past 7, short of retention
    await run_housekeeping(db, worker)
    assert (await get(client, auth, t["id"]))["status"] == "resolved"


# --- outcomes ----------------------------------------------------------------------------------


async def test_insights_report_volume_sources_and_targets(client, auth, worker):
    pay = (
        await client.post(
            "/api/v1/systems", headers=auth("admin"), json={"key": "payments", "name": "Payments"}
        )
    ).json()
    a = await make_ticket(
        client,
        auth,
        subject="Card declined for Jane",
        system_id=pay["id"],
        requester_email="jane@example.com",
        priority="high",
    )
    b = await make_ticket(
        client, auth, subject="Refund", system_id=pay["id"], requester_email="bob@example.com"
    )
    await make_ticket(client, auth, subject="Laptop")
    await worker.run_once()
    await client.post(
        f"/api/v1/tickets/{a['id']}/messages",
        headers=auth("admin"),
        json={"kind": "outbound", "body": "Fixed"},
    )
    await client.patch(
        f"/api/v1/tickets/{a['id']}", headers=auth("admin"), json={"status": "resolved"}
    )

    r = await client.get("/api/v1/tickets/insights", headers=auth("admin"))
    assert r.status_code == 200
    data = r.json()
    assert (data["created"], data["resolved"], data["open_now"]) == (3, 1, 2)
    assert data["by_system"][0] == {"label": "Payments", "count": 2}
    assert {"label": "No system yet", "count": 1} in data["by_system"]
    assert data["first_response"] == {"met": 1, "missed": 0, "rate": 1.0}
    assert data["resolution"]["met"] == 1
    assert data["median_first_response_minutes"] == 0
    assert len(data["timeline"]) == 31 and data["timeline"][-1]["created"] == 3
    assert {p["label"] for p in data["by_priority"]} == {"high", "normal"}
    # Aggregates only: no subjects, no people.
    assert "Jane" not in r.text and "Refund" not in r.text and "example.com" not in r.text
    assert b["id"]


async def test_insights_with_a_paused_ticket_on_a_system(client, auth):
    """A paused ticket's resolution view reads its system's own targets.

    Insights skips loading ticket content for speed; this path must still work
    (it failed in the browser walkthrough when the system was not loaded)."""
    system = (
        await client.post(
            "/api/v1/systems",
            headers=auth("admin"),
            json={"key": "vpn", "name": "VPN", "sla_resolve_hours": {"normal": 8}},
        )
    ).json()
    ticket = await make_ticket(
        client, auth, subject="VPN drops", system_id=system["id"], requester_email="a@example.com"
    )
    r = await client.patch(
        f"/api/v1/tickets/{ticket['id']}", headers=auth("admin"), json={"status": "pending"}
    )
    assert r.status_code == 200
    r = await client.get("/api/v1/tickets/insights", headers=auth("admin"))
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1


async def test_insights_respect_scope_and_permissions(client, auth):
    team = await make_team(client, auth, "Support", ["agent"])
    other = await make_team(client, auth, "HR")
    await make_ticket(client, auth, team_id=team)
    await make_ticket(client, auth, team_id=other)
    await make_ticket(client, auth, team_id=other)
    agent = (await client.get("/api/v1/tickets/insights", headers=auth("agent"))).json()
    admin = (await client.get("/api/v1/tickets/insights", headers=auth("admin"))).json()
    assert (agent["created"], admin["created"]) == (1, 3)
    assert (
        await client.get("/api/v1/tickets/insights", headers=auth("auditor"))
    ).status_code == 403
    assert (
        await client.get("/api/v1/tickets/insights", headers=auth("admin"), params={"days": 1000})
    ).status_code == 422


async def test_insights_over_mcp(client, auth):
    await make_ticket(client, auth)
    result = await mcp_call(client, auth("bot"), "get_insights", days=7)
    assert result["structuredContent"]["created"] == 1
    assert SUBJECTS["bot"]  # the agent sees only its own scope (triage), like everywhere else


def test_existing_tickets_get_targets_on_upgrade(tmp_path, monkeypatch):
    """The 0004 migration back-fills targets for tickets created before it."""
    import json

    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, text
    from typer.testing import CliRunner

    from zentral.cli import app
    from zentral.config import get_settings

    db = tmp_path / "upgrade.db"
    for name, value in {
        "ZENTRAL_ENV": "test",
        "ZENTRAL_AUTH_MODE": "dev",
        "ZENTRAL_DATABASE_URL": f"sqlite+aiosqlite:///{db}",
        "ZENTRAL_ENCRYPTION_KEYS": json.dumps([Fernet.generate_key().decode()]),
        "ZENTRAL_BLIND_INDEX_KEY": "b" * 40,
        "ZENTRAL_AUDIT_HMAC_KEY": "a" * 40,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    runner = CliRunner()
    assert runner.invoke(app, ["db", "upgrade", "--revision", "0003"]).exit_code == 0
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tickets (subject, status, priority, tags, channel, created_at, updated_at) "
                "VALUES ('x', 'new', 'urgent', '[]', 'manual', '2026-01-01 08:00:00', '2026-01-01 08:00:00')"
            )
        )
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    with engine.connect() as conn:
        due = conn.execute(
            text("SELECT response_due_at, resolve_due_at, paused_seconds FROM tickets")
        ).one()
    assert str(due[0]).startswith("2026-01-01 09:00") and str(due[1]).startswith("2026-01-01 16:00")
    assert due[2] == 0
    get_settings.cache_clear()
