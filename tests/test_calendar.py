"""Working-time arithmetic and the targets built on it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_settings
from tests.helpers import make_ticket
from zentral.config import ConfigurationError
from zentral.services.calendar import CalendarError, WorkCalendar, parse_hours

CPH = ZoneInfo("Europe/Copenhagen")
OFFICE = WorkCalendar.build("Europe/Copenhagen", "mon-fri 08:00-17:00")


def cph(*args: int) -> datetime:
    return datetime(*args, tzinfo=CPH).astimezone(UTC)


# --- parsing --------------------------------------------------------------------------------


def test_parse_working_hours():
    assert parse_hours("mon-fri 08:00-17:00") == {d: [(480, 1020)] for d in range(5)}
    assert parse_hours("sat 09:00-13:00, sat 14:00-24:00") == {5: [(540, 780), (840, 1440)]}
    assert set(parse_hours("fri-mon 10:00-11:00")) == {4, 5, 6, 0}  # wraps over the weekend
    assert parse_hours(" MON 8:00-9:30 ") == {0: [(480, 570)]}
    assert parse_hours("") == {}


@pytest.mark.parametrize(
    "spec",
    [
        "weekdays 08:00-17:00",
        "mon-fri 8-17",
        "mon-xyz 08:00-17:00",
        "mon 17:00-08:00",
        "mon 08:00-08:00",
        "mon 08:60-09:00",
        "mon 08:00-24:30",
        "mon 08:00-12:00, mon 11:00-13:00",
    ],
)
def test_bad_working_hours_are_rejected(spec):
    with pytest.raises(CalendarError):
        parse_hours(spec)


def test_unknown_time_zone_is_rejected():
    with pytest.raises(CalendarError):
        WorkCalendar.build("Mars/Olympus_Mons", "mon 08:00-17:00")


# --- adding working time ----------------------------------------------------------------------


def test_add_skips_nights_and_weekends():
    friday_4pm = cph(2026, 11, 20, 16, 0)
    assert OFFICE.add(friday_4pm, 1) == cph(2026, 11, 20, 17, 0)  # exactly at closing
    assert OFFICE.add(friday_4pm, 2) == cph(2026, 11, 23, 9, 0)  # Monday morning
    assert OFFICE.add(cph(2026, 11, 21, 12, 0), 1) == cph(2026, 11, 23, 9, 0)  # from Saturday
    assert OFFICE.add(cph(2026, 11, 23, 6, 0), 1) == cph(2026, 11, 23, 9, 0)  # before opening
    # Monday 8h + Tuesday–Thursday 27h = 35h; the 36th hour ends at 09:00 on Friday.
    assert OFFICE.add(cph(2026, 11, 23, 9, 0), 36) == cph(2026, 11, 27, 9, 0)


def test_add_skips_holidays():
    calendar = WorkCalendar.build(
        "Europe/Copenhagen", "mon-fri 08:00-17:00", [date(2026, 12, 24), date(2026, 12, 25)]
    )
    assert calendar.add(cph(2026, 12, 23, 16, 0), 2) == cph(2026, 12, 28, 9, 0)


def test_add_follows_daylight_saving_time():
    # Summer time starts on Sunday 29 March 2026: 08:00 local is 07:00 UTC before
    # and 06:00 UTC after. A working day is still nine local hours.
    before = OFFICE.add(cph(2026, 3, 27, 16, 0), 2)
    assert before == cph(2026, 3, 30, 9, 0)
    assert before.hour == 7  # 09:00 CEST
    assert OFFICE.between(cph(2026, 3, 27, 8, 0), cph(2026, 3, 30, 17, 0)) == timedelta(hours=18)


def test_around_the_clock_is_plain_arithmetic():
    calendar = WorkCalendar()
    start = datetime(2026, 11, 21, 23, 30, tzinfo=UTC)
    assert calendar.around_the_clock
    assert calendar.add(start, 1.5) == start + timedelta(hours=1.5)
    assert calendar.between(start, start + timedelta(days=2)) == timedelta(days=2)


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        OFFICE.add(datetime(2026, 11, 20, 9, 0), 1)  # noqa: DTZ001


# --- measuring working time ---------------------------------------------------------------------


def test_between_counts_only_working_time():
    assert OFFICE.between(cph(2026, 11, 20, 16, 0), cph(2026, 11, 23, 10, 0)) == timedelta(hours=3)
    assert OFFICE.between(cph(2026, 11, 21, 9, 0), cph(2026, 11, 22, 20, 0)) == timedelta(0)
    assert OFFICE.between(cph(2026, 11, 23, 10, 0), cph(2026, 11, 20, 10, 0)) == timedelta(0)


def test_add_and_between_agree():
    start = cph(2026, 11, 18, 7, 13)
    for step in range(0, 200):
        hours = step * 0.37
        due = OFFICE.add(start + timedelta(minutes=step * 97), hours)
        spent = OFFICE.between(start + timedelta(minutes=step * 97), due)
        assert abs(spent.total_seconds() - hours * 3600) < 1e-6, (step, hours)


# --- configuration ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"business_hours": "weekdays 9-5"},
        {"timezone": "Nowhere/Town", "business_hours": "mon 08:00-17:00"},
        {"holidays": ["24/12/2026"]},
        {"sla_calendar": {"urgent": "always"}},
        {"sla_calendar": {"critical": "24x7"}},
    ],
)
def test_calendar_configuration_is_validated(bad):
    with pytest.raises(ConfigurationError):
        make_settings(**bad).validate_for_runtime()


def test_calendar_configuration_from_environment(monkeypatch):
    monkeypatch.setenv("ZENTRAL_BUSINESS_HOURS", "mon-fri 08:00-16:00")
    monkeypatch.setenv("ZENTRAL_TIMEZONE", "Europe/Oslo")
    monkeypatch.setenv("ZENTRAL_HOLIDAYS", "2026-12-24,2026-12-25")
    monkeypatch.setenv("ZENTRAL_SLA_CALENDAR", "urgent=24x7,high=24x7,normal=business,low=business")
    settings = make_settings()
    settings.validate_for_runtime()
    calendar = settings.work_calendar()
    assert calendar.zone.key == "Europe/Oslo" and len(calendar.holidays) == 2
    assert settings.sla_calendar["high"] == "24x7"


# --- targets on tickets -------------------------------------------------------------------------


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def test_targets_run_on_the_business_clock(client, auth, runtime):
    runtime.settings.business_hours = "mon-fri 08:00-17:00"
    runtime.settings.timezone = "Europe/Copenhagen"
    calendar = runtime.settings.work_calendar()

    normal = await make_ticket(client, auth, requester_email="jane@example.com")
    created = parse(normal["created_at"])
    assert normal["target_clock"] == "business"
    assert parse(normal["response_due_at"]) == calendar.add(created, 8)
    assert parse(normal["resolve_due_at"]) == calendar.add(created, 72)

    urgent = await make_ticket(client, auth, requester_email="jane@example.com", priority="urgent")
    assert urgent["target_clock"] == "24x7"  # urgent work does not wait for Monday
    assert parse(urgent["response_due_at"]) == parse(urgent["created_at"]) + timedelta(hours=1)


async def test_systems_can_promise_more(client, auth, runtime):
    r = await client.post(
        "/api/v1/systems",
        headers=auth("admin"),
        json={
            "key": "payments",
            "name": "Payments",
            "sla_response_hours": {"normal": 2},
            "sla_resolve_hours": {"normal": 12, "urgent": 4},
        },
    )
    assert r.status_code == 201, r.text
    system = r.json()
    assert system["sla_resolve_hours"] == {"normal": 12.0, "urgent": 4.0}

    t = await make_ticket(client, auth, requester_email="jane@example.com", system_id=system["id"])
    created = parse(t["created_at"])
    assert parse(t["response_due_at"]) == created + timedelta(hours=2)
    assert parse(t["resolve_due_at"]) == created + timedelta(hours=12)

    # Priorities the system does not define fall back to the global targets.
    t = (
        await client.patch(
            f"/api/v1/tickets/{t['id']}", headers=auth("admin"), json={"priority": "low"}
        )
    ).json()
    assert parse(t["resolve_due_at"]) == created + timedelta(hours=168)

    # Moving a ticket off the system re-plans it with the global targets.
    other = await make_ticket(
        client, auth, requester_email="jane@example.com", system_id=system["id"]
    )
    other = (
        await client.patch(
            f"/api/v1/tickets/{other['id']}", headers=auth("admin"), json={"system_id": None}
        )
    ).json()
    assert parse(other["resolve_due_at"]) == parse(other["created_at"]) + timedelta(hours=72)


@pytest.mark.parametrize(
    "targets",
    [{"critical": 1}, {"normal": 0}, {"normal": -2}, {"normal": 9000}, {"normal": "soon"}],
)
async def test_system_targets_are_validated(client, auth, targets):
    r = await client.post(
        "/api/v1/systems",
        headers=auth("admin"),
        json={"key": "x", "name": "X", "sla_resolve_hours": targets},
    )
    assert r.status_code == 422
