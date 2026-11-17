"""Fill a local *development* instance with sample data.

    uv run python scripts/seed_demo.py [http://127.0.0.1:8000]

Uses the public API and the dev persona tokens, so it only works when the
server runs with ZENTRAL_AUTH_MODE=dev.
"""

from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

ALERTS = [
    {
        "title": "Disk usage 95% on db-prod-01",
        "description": "Filesystem /var/lib/postgresql is at 95%. Alert fired by monitoring.",
        "severity": "critical",
        "thread_key": "disk:db-prod-01",
        "system": "db-platform",
    },
    {
        "title": "Cannot sign in after password change",
        "description": "Hi, I changed my password this morning and now I am locked out of everything. Please call me on +45 12 34 56 78.\n\nJane",
        "requester_email": "jane.doe@example.com",
        "requester_name": "Jane Doe",
        "severity": "high",
    },
    {
        "title": "Suspicious e-mail asking for my credentials",
        "description": "I received a mail from it-support@examp1e.com asking me to confirm my password. Looks like phishing. I did not click anything.",
        "requester_email": "omar.haddad@example.com",
        "requester_name": "Omar Haddad",
    },
    {
        "title": "New laptop for onboarding on Monday",
        "description": "We have a new colleague starting Monday. Please provide a laptop, docking station and two monitors.",
        "requester_email": "lena.berg@example.com",
        "requester_name": "Lena Berg",
        "severity": "low",
    },
    {
        "title": "VPN keeps disconnecting",
        "description": "The VPN drops every 10 minutes when I work from home. Wifi is otherwise fine.",
        "requester_email": "tom.nielsen@example.com",
        "requester_name": "Tom Nielsen",
    },
    {
        "title": "Invoice 2026-1184 charged twice",
        "description": "Our card was charged twice for invoice 2026-1184. Please refund one payment.",
        "requester_email": "billing@customer.example",
        "requester_name": "Accounts Payable",
    },
]


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=15) as http:

        def token(persona: str) -> dict[str, str]:
            r = http.post("/api/v1/dev/token", json={"persona": persona})
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        admin, agent = token("admin"), token("agent")
        me = http.get("/api/v1/me", headers=agent).json()
        http.get("/api/v1/me", headers=token("automation"))

        teams = {}
        for name in ("Service Desk", "Infrastructure", "Security"):
            r = http.post("/api/v1/teams", headers=admin, json={"name": name})
            teams[name] = (
                r.json()["id"]
                if r.status_code == 201
                else next(
                    t["id"]
                    for t in http.get("/api/v1/teams", headers=admin).json()
                    if t["name"] == name
                )
            )
        http.post(
            f"/api/v1/teams/{teams['Service Desk']}/members",
            headers=admin,
            json={"user_id": me["subject"]},
        )

        systems = {}
        for key, name, description, keywords, team in (
            (
                "payments",
                "Payment platform",
                "Card payments, invoicing and refunds",
                ["invoice", "refund", "checkout"],
                None,
            ),
            (
                "identity",
                "Identity & sign-in",
                "Accounts, passwords and MFA",
                ["password", "sign in", "locked out"],
                "Service Desk",
            ),
            (
                "hr-portal",
                "HR portal",
                "Onboarding, leave and payslips",
                ["onboarding", "payslip"],
                None,
            ),
            ("vpn", "Remote access VPN", "", ["vpn"], "Infrastructure"),
            (
                "db-platform",
                "Database platform",
                "Managed PostgreSQL clusters",
                ["db-prod", "postgresql"],
                "Infrastructure",
            ),
        ):
            r = http.post(
                "/api/v1/systems",
                headers=admin,
                json={
                    "key": key,
                    "name": name,
                    "description": description,
                    "keywords": keywords,
                    "default_team_id": teams[team] if team else None,
                },
            )
            if r.status_code == 201:
                systems[key] = r.json()["id"]

        for rule in (
            {
                "name": "Security incidents to Security",
                "position": 10,
                "conditions": {"category": "security"},
                "actions": {
                    "set_team_id": teams["Security"],
                    "set_priority": "high",
                    "add_tags": ["security-review"],
                },
            },
            {
                "name": "Infrastructure alerts",
                "position": 20,
                "conditions": {"category": "incident"},
                "actions": {"set_team_id": teams["Infrastructure"]},
            },
        ):
            http.post("/api/v1/rules", headers=admin, json=rule)

        for name, body, team in (
            (
                "Account unlocked",
                "Hi {requester_first_name},\n\nI have unlocked your account. If it locks "
                "again, update the saved password on your phone too.\n\nReference: "
                "{ticket_key}\n\nBest regards,\n{agent_name}",
                None,
            ),
            (
                "Need more information",
                "Hi {requester_first_name},\n\nTo look into this, could you tell us when it "
                "started and send the exact error message?\n\n{agent_name}",
                None,
            ),
            (
                "Scheduled maintenance",
                "Hi {requester_first_name},\n\nThis was caused by planned maintenance, which "
                "has now finished. Let us know if it still happens.\n\n{agent_name}",
                "Infrastructure",
            ),
        ):
            http.post(
                "/api/v1/templates",
                headers=admin,
                json={"name": name, "body": body, "team_id": teams[team] if team else None},
            )

        r = http.post(
            "/api/v1/sources",
            headers=admin,
            json={
                "name": "Monitoring & forms",
                "connector": "webhook",
            },
        )
        if r.status_code != 201:
            print("Demo data already present.")
            return
        source = r.json()
        hook = {"Authorization": f"Bearer {source['ingest_token']}"}
        for alert in ALERTS:
            http.post(f"/api/v1/hooks/{source['id']}", headers=hook, json=alert).raise_for_status()

        http.patch("/api/v1/tickets/2", headers=agent, json={"assignee_id": me["subject"]})
        # Someone else hands the agent a ticket: it shows up in their notifications.
        http.patch("/api/v1/tickets/5", headers=admin, json={"assignee_id": me["subject"]})
        http.post(
            "/api/v1/tickets/2/messages",
            headers=agent,
            json={
                "kind": "note",
                "body": "Account was locked by the lockout policy after 5 failed attempts from her phone.",
            },
        )
        http.post(
            "/api/v1/tickets/2/messages",
            headers=agent,
            json={
                "kind": "outbound",
                "body": "Hi Jane,\n\nI have unlocked your account. Please update the saved password on your phone as well, otherwise it will lock again.\n\nBest regards,\nService Desk",
            },
        )
        print(
            f"Seeded {len(ALERTS)} tickets, {len(systems)} systems, 3 teams, 2 rules. Open {BASE}"
        )


if __name__ == "__main__":
    main()
