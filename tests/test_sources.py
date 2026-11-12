from datetime import timedelta

import pytest
from icalendar import Calendar

from app.models import Commitment
from app.scheduler.planner import build_plan
from app.sources.demo import demo_busy_blocks
from app.sources.google_calendar import add_workouts, fetch_busy
from app.sources.ics import InvalidCalendarFile, parse_ics, plan_to_ics
from tests.conftest import MONDAY, at

SAMPLE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:class@test
DTSTART;TZID=America/Los_Angeles:20261116T090000
DTEND;TZID=America/Los_Angeles:20261116T103000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
SUMMARY:Algorithms lecture
END:VEVENT
BEGIN:VEVENT
UID:bday@test
DTSTART;VALUE=DATE:20261117
DTEND;VALUE=DATE:20261118
SUMMARY:Mom's birthday
END:VEVENT
BEGIN:VEVENT
UID:free@test
DTSTART;TZID=America/Los_Angeles:20261118T120000
DTEND;TZID=America/Los_Angeles:20261118T130000
TRANSP:TRANSPARENT
SUMMARY:Optional lunch talk
END:VEVENT
BEGIN:VEVENT
UID:utc@test
DTSTART:20261119T010000Z
DTEND:20261119T020000Z
SUMMARY:Call with team (UTC)
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_expands_recurrence_and_skips_allday_and_free():
    blocks = parse_ics(SAMPLE_ICS, at(0, 0), at(7, 0), "America/Los_Angeles")
    titles = [b.title for b in blocks]
    assert titles.count("Algorithms lecture") == 3  # Mon, Wed, Fri
    assert "Mom's birthday" not in titles
    assert "Optional lunch talk" not in titles
    # 01:00 UTC Thursday = 17:00 Wednesday in Los Angeles
    call = next(b for b in blocks if b.title.startswith("Call"))
    assert call.start == at(2, 17)


def test_parse_ics_rejects_garbage():
    with pytest.raises(InvalidCalendarFile):
        parse_ics(b"not a calendar", at(0, 0), at(7, 0), "America/Los_Angeles")


def test_plan_round_trips_through_ics(prefs):
    plan = build_plan([], MONDAY, prefs)
    cal = Calendar.from_ical(plan_to_ics(plan))
    events = list(cal.walk("VEVENT"))
    assert len(events) == len(plan.workouts)
    assert str(events[0]["SUMMARY"]).startswith("Workout:")


def test_demo_busy_week_shows_the_adaptive_fallback(prefs):
    """The busy demo has free time only on Tue/Thu/Fri; Thu+Fri are back-to-back,
    so a moderate lifter gets PPL instead of Full Body x3."""
    plan = build_plan(demo_busy_blocks("busy", MONDAY, prefs.timezone), MONDAY, prefs)
    assert [d.weekday() for d in plan.free_days] == [1, 3, 4]
    assert plan.split_name == "Push/Pull/Legs"

    prefs.commitment = Commitment.LIGHT
    plan = build_plan(demo_busy_blocks("busy", MONDAY, prefs.timezone), MONDAY, prefs)
    assert plan.split_name == "Full Body x2"


# --------------------------------------------- Google Calendar (fake service) ---
class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeService:
    def __init__(self, busy):
        self.busy = busy
        self.inserted = []
        self.freebusy_body = None

    def freebusy(self):
        return self

    def events(self):
        return self

    def query(self, body):
        self.freebusy_body = body
        return FakeRequest({"calendars": {"primary": {"busy": self.busy}}})

    def insert(self, calendarId, body):
        self.inserted.append((calendarId, body))
        return FakeRequest({"id": str(len(self.inserted))})


def test_fetch_busy_parses_google_response():
    service = FakeService([{"start": "2026-11-16T17:00:00Z", "end": "2026-11-16T18:30:00Z"}])
    blocks = fetch_busy(service, at(0, 0), at(7, 0), "America/Los_Angeles")
    assert service.freebusy_body["items"] == [{"id": "primary"}]
    assert blocks[0].start == at(0, 9)  # 17:00 UTC = 9:00 PST
    assert blocks[0].end - blocks[0].start == timedelta(minutes=90)


def test_add_workouts_creates_one_event_each(prefs):
    plan = build_plan([], MONDAY, prefs)
    service = FakeService([])
    assert add_workouts(service, plan, prefs.timezone) == len(plan.workouts)
    _, body = service.inserted[0]
    assert body["summary"].startswith("Workout:")
    assert body["extendedProperties"]["private"]["app"] == "gymschedulebot"
