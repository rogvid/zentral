"""Working-time arithmetic for service-level targets.

A :class:`WorkCalendar` answers two questions:

* ``add(start, hours)`` — when is ``hours`` of working time used up, starting at
  ``start``? This turns "8 business hours" into a due time.
* ``between(a, b)`` — how much working time lies between two instants? This is
  what a pause (waiting for the requester) costs the clock.

Working hours are local wall-clock times in one IANA time zone, so daylight-
saving changes are handled by the zone database rather than by us. The empty
calendar (no hours configured) means "around the clock" and reduces to plain
datetime arithmetic, which is also the behaviour for priorities configured as
``24x7``.

Everything here is pure and deterministic; it is exhaustively unit-tested.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Safety bound: a target can never need more than ~10 years of searching.
_MAX_DAYS = 3660

_SPEC = re.compile(
    r"^(?P<d1>[a-z]{3})(?:-(?P<d2>[a-z]{3}))?\s+"
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})-(?P<h2>\d{1,2}):(?P<m2>\d{2})$"
)


class CalendarError(ValueError):
    """The working-hours specification is invalid."""


Window = tuple[int, int]  # minutes after local midnight: [start, end)


def parse_hours(spec: str) -> dict[int, list[Window]]:
    """Parse ``"mon-fri 08:00-17:00, sat 09:00-13:00"`` into windows per weekday.

    Days are ``mon``…``sun``; a range wraps (``fri-mon``). ``24:00`` ends a day.
    Windows on the same day must not overlap.
    """
    windows: dict[int, list[Window]] = {}
    for part in (p.strip().lower() for p in spec.split(",")):
        if not part:
            continue
        match = _SPEC.match(part)
        if match is None:
            raise CalendarError(
                f"cannot read working hours {part!r}; use e.g. 'mon-fri 08:00-17:00'"
            )
        try:
            first = DAYS.index(match["d1"])
            last = DAYS.index(match["d2"] or match["d1"])
        except ValueError:
            raise CalendarError(f"unknown day in {part!r}; use mon, tue, … sun") from None
        start = int(match["h1"]) * 60 + int(match["m1"])
        end = int(match["h2"]) * 60 + int(match["m2"])
        if int(match["m1"]) > 59 or int(match["m2"]) > 59 or start >= end or end > 24 * 60:
            raise CalendarError(f"invalid time range in {part!r}")
        day = first
        while True:
            windows.setdefault(day, []).append((start, end))
            if day == last:
                break
            day = (day + 1) % 7
    for day, spans in windows.items():
        spans.sort()
        for (_, end), (start, _) in pairwise(spans):
            if start < end:
                raise CalendarError(f"overlapping working hours on {DAYS[day]}")
    return windows


def load_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CalendarError(f"unknown time zone {name!r}") from None


@dataclass(frozen=True, slots=True)
class WorkCalendar:
    zone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    windows: dict[int, list[Window]] = field(default_factory=dict)
    holidays: frozenset[date] = frozenset()

    @classmethod
    def build(cls, zone: str, hours: str, holidays: Iterable[date] = ()) -> WorkCalendar:
        windows = parse_hours(hours)
        if hours.strip() and not windows:
            raise CalendarError("working hours are empty")
        return cls(load_zone(zone), windows, frozenset(holidays))

    @property
    def around_the_clock(self) -> bool:
        return not self.windows

    # --- the two operations -------------------------------------------------------

    def add(self, start: datetime, hours: float) -> datetime:
        """The instant at which ``hours`` of working time, counted from ``start``, run out."""
        if self.around_the_clock:
            return start + timedelta(hours=hours)
        remaining = timedelta(hours=hours)
        for begin, end in self._spans(start):
            length = end - begin
            if remaining <= length:
                return begin + remaining
            remaining -= length
        raise CalendarError("no working time found within ten years")  # pragma: no cover

    def between(self, a: datetime, b: datetime) -> timedelta:
        """Working time in ``[a, b)``. Zero when ``b`` is not after ``a``."""
        if b <= a:
            return timedelta(0)
        if self.around_the_clock:
            return b - a
        total = timedelta(0)
        for begin, end in self._spans(a):
            if begin >= b:
                break
            total += min(end, b) - begin
        return total

    # --- internals -------------------------------------------------------------------

    def _spans(self, start: datetime) -> Iterator[tuple[datetime, datetime]]:
        """Working spans (UTC) from ``start`` onwards, clipped to begin at ``start``."""
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        local_day = start.astimezone(self.zone).date()
        for offset in range(_MAX_DAYS):
            day = local_day + timedelta(days=offset)
            if day in self.holidays:
                continue
            for first, last in self.windows.get(day.weekday(), ()):
                begin = self._instant(day, first)
                end = self._instant(day, last)
                if end <= start:
                    continue
                yield max(begin, start), end

    def _instant(self, day: date, minutes: int) -> datetime:
        if minutes >= 24 * 60:
            local = datetime.combine(day + timedelta(days=1), time(0), self.zone)
        else:
            local = datetime.combine(day, time(minutes // 60, minutes % 60), self.zone)
        return local.astimezone(UTC)
