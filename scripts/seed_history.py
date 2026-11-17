"""Add 30 days of realistic ticket history to a *development* database.

    uv run python scripts/seed_history.py

Run after scripts/seed_demo.py (it reuses the systems and teams that creates).
Writes through the ORM, so content is encrypted exactly as in real use, but it
does not go through the API: the history is not in the audit trail, and it
refuses to run unless ZENTRAL_AUTH_MODE=dev.
"""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from sqlalchemy import select

from zentral.config import get_settings
from zentral.db.models import (
    Contact,
    Message,
    MessageKind,
    System,
    Team,
    Ticket,
    TicketStatus,
    utcnow,
)
from zentral.runtime import build_runtime
from zentral.security.crypto import get_cipher
from zentral.services import sla

PEOPLE = [
    "Maja Holm",
    "Liam Berg",
    "Sofie Dahl",
    "Noah Lund",
    "Ida Krogh",
    "Emil Vang",
    "Clara Bech",
    "Oscar Friis",
]
SUBJECTS = {
    "payments": [
        "Card payment declined at checkout",
        "Refund not received",
        "Wrong VAT on statement",
    ],
    "identity": [
        "Locked out after password reset",
        "MFA prompt loops",
        "Sign-in fails on new phone",
    ],
    "hr-portal": [
        "Payslip not visible",
        "Leave request stuck in approval",
        "Onboarding checklist missing",
    ],
    "vpn": ["VPN drops every few minutes", "Cannot reach file share over VPN"],
    "db-platform": [
        "Replica lag alert",
        "Disk usage high on db cluster",
        "Slow queries on reporting DB",
    ],
    None: [
        "Printer on floor 2 jammed",
        "Monitor flickers",
        "Request: second screen",
        "Teams audio crackles",
    ],
}
CATEGORY = {
    "payments": "billing",
    "identity": "access",
    "hr-portal": "service_request",
    "vpn": "network",
    "db-platform": "incident",
    None: "hardware",
}
CHANNELS = ["email_graph", "email_graph", "email_graph", "teams", "webhook", "manual"]


async def main() -> None:
    settings = get_settings()
    if settings.auth_mode != "dev":
        raise SystemExit("refusing: demo history is for development instances only")
    rng = random.Random(7)
    rt = build_runtime(settings)
    cipher = get_cipher()
    now = utcnow()
    async with rt.sessions() as session:
        systems = {s.key: s for s in (await session.scalars(select(System))).all()}
        teams = list((await session.scalars(select(Team))).all())
        contacts = []
        for name in PEOPLE:
            email = name.lower().replace(" ", ".") + "@example.com"
            identity = cipher.blind_index("email", email)
            contact = await session.scalar(select(Contact).where(Contact.identity_hash == identity))
            if contact is None:
                contact = Contact(identity_hash=identity, email=email, display_name=name)
                session.add(contact)
            contacts.append(contact)
        await session.flush()

        created = 0
        for day in range(30, 0, -1):
            weekday = (now - timedelta(days=day)).weekday()
            for _ in range(rng.randint(1, 3) if weekday >= 5 else rng.randint(3, 9)):
                key = rng.choice(list(SUBJECTS))
                system = systems.get(key) if key else None
                priority = rng.choices(["low", "normal", "high", "urgent"], [3, 10, 4, 1])[0]
                at = now - timedelta(days=day, hours=rng.randint(0, 9), minutes=rng.randint(0, 59))
                contact = rng.choice(contacts)
                ticket = Ticket(
                    subject=rng.choice(SUBJECTS[key]),
                    status=TicketStatus.NEW,
                    priority=priority,
                    category=CATEGORY[key],
                    tags=[],
                    channel=rng.choice(CHANNELS),
                    team_id=(
                        system.default_team_id
                        if system and system.default_team_id
                        else rng.choice(teams).id
                    )
                    if teams
                    else None,
                    system_id=system.id if system else None,
                    requester_id=contact.id,
                    created_at=at,
                    updated_at=at,
                )
                session.add(ticket)
                await session.flush()
                sla.refresh_targets(ticket, settings)
                session.add(
                    Message(
                        ticket_id=ticket.id,
                        kind=MessageKind.INBOUND,
                        body="(demo history)",
                        author_contact_id=contact.id,
                        created_at=at,
                    )
                )
                # Most tickets get a reply, most of them in time; older ones are mostly done.
                reply_after = timedelta(minutes=rng.choice([12, 25, 40, 70, 150, 300, 600]))
                if day > 1 or rng.random() < 0.5:
                    ticket.first_response_at = at + reply_after
                    ticket.status = TicketStatus.OPEN
                if day > 2 and rng.random() < 0.88:
                    ticket.resolved_at = (
                        at + reply_after + timedelta(hours=rng.choice([1, 3, 6, 20, 30, 50, 80]))
                    )
                    ticket.status = TicketStatus.CLOSED if day > 9 else TicketStatus.RESOLVED
                elif rng.random() < 0.25:
                    ticket.status = TicketStatus.PENDING
                    ticket.paused_at = now - timedelta(hours=rng.randint(2, 30))
                ticket.updated_at = ticket.resolved_at or ticket.first_response_at or at
                created += 1
        await session.commit()
    await rt.aclose()
    print(f"Added {created} tickets of demo history over 30 days.")


if __name__ == "__main__":
    asyncio.run(main())
