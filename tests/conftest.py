from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.models import BusyBlock, Preferences

TZ = ZoneInfo("America/Los_Angeles")
MONDAY = date(2026, 11, 16)  # a Monday


def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """Datetime on MONDAY + day_offset at hour:minute, Los Angeles time."""
    d = date.fromordinal(MONDAY.toordinal() + day_offset)
    return datetime.combine(d, time(hour, minute), tzinfo=TZ)


def busy(day_offset: int, start_hour: float, end_hour: float, title: str = "Busy") -> BusyBlock:
    sh, sm = int(start_hour), int(round((start_hour % 1) * 60))
    eh, em = int(end_hour), int(round((end_hour % 1) * 60))
    return BusyBlock(at(day_offset, sh, sm), at(day_offset, eh, em), title)


def block_whole_day(day_offset: int) -> BusyBlock:
    return busy(day_offset, 0, 23.99, "All day")


@pytest.fixture
def prefs() -> Preferences:
    return Preferences(timezone="America/Los_Angeles")
