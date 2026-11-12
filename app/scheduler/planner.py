"""Build a weekly workout plan from free slots.

Steps:
  1. Find free slots for each day (``availability.find_free_slots``).
  2. Decide how many sessions to aim for: min(commitment cap, free days).
  3. For that many days, try each candidate split. For each split, search every
     way of placing its sessions on the free days (at most C(7, k) <= 35 options)
     and keep the best *valid* placement:
       - valid  = no muscle group is trained on two back-to-back days
       - best   = sessions spread most evenly across the week (counting the
                  wrap-around to next week), then closest to the preferred time
  4. If nothing fits, drop one day and try again (e.g. 3 free days in a row
     with a moderate commitment -> Full Body x3 can't fit -> PPL can).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import combinations, pairwise
from statistics import pvariance
from zoneinfo import ZoneInfo

from app.models import BusyBlock, Preferences, Slot, TimeOfDay, WeeklyPlan, Workout
from app.scheduler.availability import find_free_slots, round_up
from app.scheduler.splits import MAX_DAYS, SessionTemplate, Split, candidate_splits

PREFERRED_TARGET: dict[TimeOfDay, time | None] = {
    TimeOfDay.MORNING: time(7, 0),
    TimeOfDay.MIDDAY: time(12, 0),
    TimeOfDay.EVENING: time(18, 0),
    TimeOfDay.ANY: None,
}

# How much one hour away from the preferred time "costs" compared to uneven spacing.
TIME_PENALTY_PER_HOUR = 0.25


def best_start(day: date, slots: list[Slot], prefs: Preferences) -> tuple[datetime, float]:
    """Pick the start time on ``day`` closest to the preferred time of day.

    Returns (start, hours_away_from_preference).
    """
    duration = timedelta(minutes=prefs.session_minutes)
    target_time = PREFERRED_TARGET[prefs.preferred_time]
    best: tuple[datetime, float] | None = None

    for slot in slots:
        latest = slot.end - duration
        if target_time is None:  # no preference: earliest possible start
            candidate = slot.start
            target = slot.start
        else:
            target = datetime.combine(day, target_time, tzinfo=slot.start.tzinfo)
            candidate = min(max(target, slot.start), latest)  # clamp into the slot
        rounded = round_up(candidate)
        if rounded <= latest:
            candidate = rounded
        hours_away = abs((candidate - target).total_seconds()) / 3600
        if best is None or hours_away < best[1]:
            best = (candidate, hours_away)

    assert best is not None, "best_start called with no slots"
    return best


def violates_recovery(sessions: tuple[SessionTemplate, ...], days: tuple[date, ...]) -> bool:
    """True if any muscle group is trained on two consecutive days."""
    for i in range(len(days) - 1):
        back_to_back = (days[i + 1] - days[i]).days < 2
        if back_to_back and sessions[i].muscle_groups & sessions[i + 1].muscle_groups:
            return True
    return False


def spacing_penalty(days: tuple[date, ...]) -> float:
    """Variance of the gaps between sessions, including the wrap to next week.

    Mon/Thu -> gaps [3, 4] -> low penalty. Mon/Tue -> gaps [1, 6] -> high penalty.
    """
    if len(days) < 2:
        return 0.0
    gaps = [(b - a).days for a, b in pairwise(days)]
    gaps.append(7 - (days[-1] - days[0]).days)
    return pvariance(gaps)


def place_split(
    split: Split, free_days: list[date], slots_by_day: dict[date, list[Slot]], prefs: Preferences
) -> list[tuple[SessionTemplate, datetime]] | None:
    """Best valid placement of ``split`` onto ``free_days``, or None if impossible."""
    best_score: float | None = None
    best_choice: tuple[date, ...] | None = None
    starts: dict[date, tuple[datetime, float]] = {d: best_start(d, slots_by_day[d], prefs) for d in free_days}

    for days in combinations(free_days, split.days):  # already in date order
        if violates_recovery(split.sessions, days):
            continue
        time_cost = sum(starts[d][1] for d in days) * TIME_PENALTY_PER_HOUR
        score = spacing_penalty(days) + time_cost
        if best_score is None or score < best_score:
            best_score, best_choice = score, days

    if best_choice is None:
        return None
    return [(s, starts[d][0]) for s, d in zip(split.sessions, best_choice, strict=True)]


def week_start_for(day: date) -> date:
    """Monday of the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def build_plan(
    busy: list[BusyBlock],
    week_start: date,
    prefs: Preferences,
    now: datetime | None = None,
) -> WeeklyPlan:
    """Main entry point: busy calendar + preferences -> weekly workout plan."""
    slots_by_day = find_free_slots(busy, week_start, prefs, now)
    free_days = [d for d in sorted(slots_by_day) if slots_by_day[d]]
    cap = MAX_DAYS[prefs.commitment]
    target = min(cap, len(free_days))
    duration = timedelta(minutes=prefs.session_minutes)

    notes = [
        f"You have {len(free_days)} day(s) this week with a free "
        f"{prefs.session_minutes}-minute window (plus {prefs.buffer_minutes} min travel buffer).",
        f"A '{prefs.commitment.value}' commitment allows up to {cap} sessions per week.",
    ]

    for days in range(target, 0, -1):
        for split in candidate_splits(days, prefs.commitment):
            placement = place_split(split, free_days, slots_by_day, prefs)
            if placement is None:
                notes.append(f"{split.name} didn't fit without back-to-back sessions for the same muscles.")
                continue
            if days < target:
                notes.append(f"Dropped to {days} session(s) so every muscle gets at least a day to recover.")
            workouts = [
                Workout(t.name, t.muscle_groups, t.exercises, start, start + duration) for t, start in placement
            ]
            return WeeklyPlan(week_start, split.name, workouts, free_days, target, notes)

    notes.append("There isn't a free window long enough for a full session this week.")
    return WeeklyPlan(week_start, "Rest week", [], free_days, target, notes)


def local_now(prefs: Preferences) -> datetime:
    return datetime.now(ZoneInfo(prefs.timezone))
