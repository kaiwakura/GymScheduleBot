"""Turn a list of busy blocks into free workout slots, day by day.

Algorithm (per day):
  1. Build the day's "allowed window" from the user's earliest start / latest end.
  2. Pad every busy block with the travel buffer on both sides.
  3. Clip the padded blocks to the window, sort them, and merge overlaps.
  4. The gaps between merged blocks are free time; keep gaps long enough for a session.

Runs in O(B log B) per day, where B is the number of busy blocks that day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import BusyBlock, Preferences, Slot

ROUND_TO_MINUTES = 15


def round_up(dt: datetime, minutes: int = ROUND_TO_MINUTES) -> datetime:
    """Round a datetime up to the next multiple of ``minutes`` (e.g. 7:05 -> 7:15)."""
    dt = dt.replace(second=0, microsecond=0)
    remainder = dt.minute % minutes
    if remainder == 0:
        return dt
    return dt + timedelta(minutes=minutes - remainder)


def merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or touching intervals. Input does not need to be sorted."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def free_slots_for_day(
    day: date,
    busy: list[BusyBlock],
    prefs: Preferences,
    now: datetime | None = None,
) -> list[Slot]:
    """Free slots on one day that can hold a full session."""
    tz = ZoneInfo(prefs.timezone)
    window_start = datetime.combine(day, prefs.earliest_start, tzinfo=tz)
    window_end = datetime.combine(day, prefs.latest_end, tzinfo=tz)

    # Don't suggest times that have already passed.
    if now is not None and now > window_start:
        window_start = round_up(now.astimezone(tz))
    if window_start >= window_end:
        return []

    buffer = timedelta(minutes=prefs.buffer_minutes)
    blocked: list[tuple[datetime, datetime]] = []
    for block in busy:
        start, end = block.start - buffer, block.end + buffer
        if end > window_start and start < window_end:  # overlaps today's window
            blocked.append((max(start, window_start), min(end, window_end)))

    slots: list[Slot] = []
    cursor = window_start
    for start, end in merge_intervals(blocked):
        if start > cursor:
            slots.append(Slot(cursor, start))
        cursor = max(cursor, end)
    if cursor < window_end:
        slots.append(Slot(cursor, window_end))

    return [s for s in slots if s.minutes >= prefs.session_minutes]


def find_free_slots(
    busy: list[BusyBlock],
    week_start: date,
    prefs: Preferences,
    now: datetime | None = None,
) -> dict[date, list[Slot]]:
    """Free slots for each of the 7 days starting at ``week_start``.

    Days the user blocked (e.g. "never on Sundays") always map to an empty list.
    """
    result: dict[date, list[Slot]] = {}
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if day.weekday() in prefs.blocked_weekdays:
            result[day] = []
        else:
            result[day] = free_slots_for_day(day, busy, prefs, now)
    return result
