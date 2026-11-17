"""Service-level targets: when a ticket needs a first reply, and a resolution.

How long a ticket has comes from its priority (``ZENTRAL_SLA_RESPONSE_HOURS``,
``ZENTRAL_SLA_RESOLVE_HOURS``), replaced per priority by the targets of the
system it concerns when that system defines its own.

Which clock those hours run on comes from ``ZENTRAL_SLA_CALENDAR``: on the
**business** clock only working hours count (``ZENTRAL_BUSINESS_HOURS`` in
``ZENTRAL_TIMEZONE``, minus ``ZENTRAL_HOLIDAYS``); on **24x7** every hour does.
With no working hours configured, every priority runs around the clock.

* **First response** is due after ``response`` hours. It is met by the first
  reply a *person or agent* sends — automatic acknowledgements do not count.
* **Resolution** is due after ``resolve`` hours plus the time spent *pending*
  (waiting for the requester), measured on the same clock: the clock pauses
  while the ball is in their court.

Changing the priority or the system re-plans both targets from the original
creation time, so raising a ticket to urgent can make it overdue at once —
which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from zentral.config import Settings
from zentral.db.models import Ticket, TicketStatus, utcnow
from zentral.services.calendar import WorkCalendar

TargetState = Literal["met", "late", "due", "breached", "paused", "none"]
AROUND_THE_CLOCK = WorkCalendar()


def clock_name(ticket: Ticket, settings: Settings) -> str:
    calendar = settings.work_calendar()
    if calendar.around_the_clock:
        return "24x7"
    return "24x7" if settings.sla_calendar.get(ticket.priority) == "24x7" else "business"


def clock(ticket: Ticket, settings: Settings) -> WorkCalendar:
    if clock_name(ticket, settings) == "business":
        return settings.work_calendar()
    return AROUND_THE_CLOCK


def target_hours(ticket: Ticket, settings: Settings) -> tuple[float | None, float | None]:
    """(response, resolve) hours for the ticket: the system's own, else the global ones."""
    system = ticket.system if ticket.system_id is not None else None
    own_response = (system.sla_response_hours or {}) if system is not None else {}
    own_resolve = (system.sla_resolve_hours or {}) if system is not None else {}
    response = own_response.get(ticket.priority) or settings.sla_response_hours.get(ticket.priority)
    resolve = own_resolve.get(ticket.priority) or settings.sla_resolve_hours.get(ticket.priority)
    return response, resolve


def refresh_targets(ticket: Ticket, settings: Settings) -> None:
    """(Re)compute both due times from priority, system, clock and paused time."""
    created = ticket.created_at or utcnow()
    calendar = clock(ticket, settings)
    response, resolve = target_hours(ticket, settings)
    paused = (ticket.paused_seconds or 0) / 3600
    ticket.response_due_at = calendar.add(created, response) if response else None
    ticket.resolve_due_at = calendar.add(created, resolve + paused) if resolve else None
    # New deadlines deserve new warnings.
    ticket.response_alert = None
    ticket.resolve_alert = None


def status_changed(ticket: Ticket, old: str, new: str, settings: Settings) -> None:
    """Pause the resolution clock while pending; resume it afterwards."""
    now = utcnow()
    if new == TicketStatus.PENDING and old != TicketStatus.PENDING:
        ticket.paused_at = now
    elif old == TicketStatus.PENDING and new != TicketStatus.PENDING and ticket.paused_at:
        waited = clock(ticket, settings).between(ticket.paused_at, now)
        ticket.paused_seconds = (ticket.paused_seconds or 0) + int(waited.total_seconds())
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


def resolve_view(ticket: Ticket, settings: Settings, now: datetime | None = None) -> TargetView:
    now = now or utcnow()
    due = ticket.resolve_due_at
    if due is None:
        return TargetView(None, "none")
    if ticket.resolved_at is not None:
        return TargetView(due, "met" if ticket.resolved_at <= due else "late")
    if ticket.paused_at is not None:
        # Show the due time as it would be if the requester answered right now.
        calendar = clock(ticket, settings)
        _, resolve = target_hours(ticket, settings)
        if resolve is None:
            return TargetView(due, "paused")
        waited = calendar.between(ticket.paused_at, now)
        paused = timedelta(seconds=ticket.paused_seconds or 0) + waited
        created = ticket.created_at or now
        return TargetView(calendar.add(created, resolve + paused.total_seconds() / 3600), "paused")
    return TargetView(due, "breached" if now > due else "due")
