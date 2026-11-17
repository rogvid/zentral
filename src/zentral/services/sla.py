"""Service-level targets: when a ticket needs a first reply, and a resolution.

Targets are set from the ticket's priority (``ZENTRAL_SLA_RESPONSE_HOURS`` and
``ZENTRAL_SLA_RESOLVE_HOURS``), measured in calendar hours from creation:

* **First response** is due ``response`` hours after the ticket arrived. It is
  met by the first reply a *person or agent* sends to the requester —
  automatic acknowledgements do not count.
* **Resolution** is due ``resolve`` hours after the ticket arrived, plus any
  time spent *pending* (waiting for the requester): the clock pauses while the
  ball is in their court.

Changing the priority re-plans both targets from the original creation time,
so raising a ticket to urgent can make it overdue at once — which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from zentral.config import Settings
from zentral.db.models import Ticket, TicketStatus, utcnow

TargetState = Literal["met", "late", "due", "breached", "paused", "none"]


def refresh_targets(ticket: Ticket, settings: Settings) -> None:
    """(Re)compute both due times from the ticket's priority and paused time."""
    created = ticket.created_at or utcnow()
    response = settings.sla_response_hours.get(ticket.priority)
    resolve = settings.sla_resolve_hours.get(ticket.priority)
    ticket.response_due_at = created + timedelta(hours=response) if response else None
    ticket.resolve_due_at = (
        created + timedelta(hours=resolve, seconds=ticket.paused_seconds or 0) if resolve else None
    )


def status_changed(ticket: Ticket, old: str, new: str, settings: Settings) -> None:
    """Pause the resolution clock while pending; resume it afterwards."""
    now = utcnow()
    if new == TicketStatus.PENDING and old != TicketStatus.PENDING:
        ticket.paused_at = now
    elif old == TicketStatus.PENDING and new != TicketStatus.PENDING and ticket.paused_at:
        ticket.paused_seconds = (ticket.paused_seconds or 0) + int(
            (now - ticket.paused_at).total_seconds()
        )
        ticket.paused_at = None
        refresh_targets(ticket, settings)


def responded(ticket: Ticket) -> None:
    """Record the first human (or agent) reply to the requester."""
    if ticket.first_response_at is None:
        ticket.first_response_at = utcnow()


@dataclass(frozen=True, slots=True)
class TargetView:
    due_at: datetime | None
    state: TargetState


def response_view(ticket: Ticket, now: datetime | None = None) -> TargetView:
    now = now or utcnow()
    due = ticket.response_due_at
    if due is None or (ticket.requester_id is None and ticket.first_response_at is None):
        # Internal tickets have nobody to respond to.
        return TargetView(due, "none")
    if ticket.first_response_at is not None:
        return TargetView(due, "met" if ticket.first_response_at <= due else "late")
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        return TargetView(due, "none")
    return TargetView(due, "breached" if now > due else "due")


def resolve_view(ticket: Ticket, now: datetime | None = None) -> TargetView:
    now = now or utcnow()
    due = ticket.resolve_due_at
    if due is None:
        return TargetView(None, "none")
    if ticket.resolved_at is not None:
        return TargetView(due, "met" if ticket.resolved_at <= due else "late")
    if ticket.paused_at is not None:
        # Show the due time as it would be if the requester answered right now.
        return TargetView(due + (now - ticket.paused_at), "paused")
    return TargetView(due, "breached" if now > due else "due")
