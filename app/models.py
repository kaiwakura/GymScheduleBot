"""Core data types shared across the app.

Everything time-related is a timezone-aware ``datetime``. Calendar sources
(Google, .ics, demo) all produce ``BusyBlock`` objects, so the scheduler never
needs to know where the data came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum


class Commitment(StrEnum):
    """How much the user wants to train. Caps the number of sessions per week."""

    LIGHT = "light"  # "I just want to stay consistent"
    MODERATE = "moderate"  # "I want real progress"
    DEDICATED = "dedicated"  # "Training is a priority"


class TimeOfDay(StrEnum):
    MORNING = "morning"
    MIDDAY = "midday"
    EVENING = "evening"
    ANY = "any"


@dataclass(frozen=True)
class BusyBlock:
    """A span of time when the user is NOT available."""

    start: datetime
    end: datetime
    title: str = "Busy"


@dataclass(frozen=True)
class Slot:
    """A free span of time long enough to hold a workout."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class Preferences:
    timezone: str = "America/Los_Angeles"
    commitment: Commitment = Commitment.MODERATE
    preferred_time: TimeOfDay = TimeOfDay.ANY
    session_minutes: int = 60
    # Time to get to/from the gym, shower, etc. Kept clear before and after.
    buffer_minutes: int = 15
    earliest_start: time = time(6, 0)
    latest_end: time = time(22, 0)
    # 0 = Monday ... 6 = Sunday. Days the user never wants to train.
    blocked_weekdays: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class Workout:
    """One scheduled training session."""

    name: str  # e.g. "Push"
    muscle_groups: frozenset[str]
    exercises: tuple[str, ...]
    start: datetime
    end: datetime

    @property
    def day(self) -> date:
        return self.start.date()


@dataclass
class WeeklyPlan:
    week_start: date
    split_name: str
    workouts: list[Workout]
    free_days: list[date]  # days that had at least one usable slot
    target_days: int  # how many sessions the commitment level asked for
    notes: list[str] = field(default_factory=list)  # human-readable reasons
    explanation: str = ""  # filled in by the AI layer (or the fallback)

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)
