"""Read busy time from an .ics file, and export a plan as an .ics file.

Any calendar app can export .ics (Google Calendar: Settings -> Import & export),
so this is the no-login way to use the app.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar, Event

from app.models import BusyBlock, WeeklyPlan


class InvalidCalendarFile(ValueError):
    pass


def _as_aware(value: datetime, tz: ZoneInfo) -> datetime:
    # "Floating" times (no time zone in the file) are treated as the user's local time.
    return value if value.tzinfo else value.replace(tzinfo=tz)


def parse_ics(data: bytes, start: datetime, end: datetime, timezone: str) -> list[BusyBlock]:
    """Busy blocks between ``start`` and ``end``, with recurring events expanded.

    Skipped on purpose:
      * all-day events (birthdays, holidays, "WFH" markers usually don't block a workout)
      * events marked "Free" (TRANSP:TRANSPARENT) or cancelled
    """
    tz = ZoneInfo(timezone)
    try:
        calendar = Calendar.from_ical(data)
    except Exception as exc:  # icalendar raises several different error types
        raise InvalidCalendarFile("That doesn't look like a valid .ics calendar file.") from exc

    blocks: list[BusyBlock] = []
    for event in recurring_ical_events.of(calendar).between(start, end):
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        ev_start = event.decoded("DTSTART")
        if not isinstance(ev_start, datetime):  # a plain date = all-day event
            continue
        if "DTEND" in event:
            ev_end = event.decoded("DTEND")
        elif "DURATION" in event:
            ev_end = ev_start + event.decoded("DURATION")
        else:
            ev_end = ev_start
        if isinstance(ev_end, date) and not isinstance(ev_end, datetime):
            continue
        blocks.append(BusyBlock(_as_aware(ev_start, tz), _as_aware(ev_end, tz), str(event.get("SUMMARY", "Busy"))))
    return sorted(blocks, key=lambda b: b.start)


def plan_to_ics(plan: WeeklyPlan) -> bytes:
    """Export the plan's workouts so they can be imported into any calendar app."""
    cal = Calendar()
    cal.add("prodid", "-//GymScheduleBot//Workout Planner//EN")
    cal.add("version", "2.0")
    for w in plan.workouts:
        event = Event()
        event.add("uid", f"{w.start.isoformat()}-{w.name}@gymschedulebot")
        event.add("summary", f"Workout: {w.name}")
        event.add("dtstart", w.start)
        event.add("dtend", w.end)
        event.add("dtstamp", datetime.now(tz=w.start.tzinfo))
        event.add("description", f"{plan.split_name}\n\n" + "\n".join(w.exercises))
        cal.add_component(event)
    return cal.to_ical()
