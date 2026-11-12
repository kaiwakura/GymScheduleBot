"""Training split definitions and the rules for choosing one.

A *split* is an ordered list of session templates for one week. Which split we
use depends on two things:
  * how many days the user actually has free this week, and
  * how committed they are (which caps the number of sessions).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Commitment


@dataclass(frozen=True)
class SessionTemplate:
    name: str
    muscle_groups: frozenset[str]
    exercises: tuple[str, ...]


@dataclass(frozen=True)
class Split:
    name: str
    sessions: tuple[SessionTemplate, ...]

    @property
    def days(self) -> int:
        return len(self.sessions)


# ---------------------------------------------------------------- sessions ---
CHEST, BACK, SHOULDERS, BICEPS, TRICEPS, QUADS, HAMSTRINGS, GLUTES, CALVES, CORE = (
    "chest", "back", "shoulders", "biceps", "triceps",
    "quads", "hamstrings", "glutes", "calves", "core",
)  # fmt: skip

FULL_BODY = SessionTemplate(
    "Full Body",
    frozenset({CHEST, BACK, SHOULDERS, QUADS, HAMSTRINGS, GLUTES, CORE}),
    ("Back Squat 3x6-8", "Bench Press 3x6-8", "Barbell Row 3x8-10",
     "Romanian Deadlift 3x8-10", "Overhead Press 2x8-10", "Plank 3x45s"),
)  # fmt: skip
PUSH = SessionTemplate(
    "Push",
    frozenset({CHEST, SHOULDERS, TRICEPS}),
    ("Bench Press 4x6-8", "Overhead Press 3x8-10", "Incline Dumbbell Press 3x10",
     "Lateral Raise 3x12-15", "Triceps Pushdown 3x12"),
)  # fmt: skip
PULL = SessionTemplate(
    "Pull",
    frozenset({BACK, BICEPS}),
    ("Deadlift 3x5", "Pull-up 3xAMRAP", "Seated Cable Row 3x10",
     "Face Pull 3x15", "Barbell Curl 3x10"),
)  # fmt: skip
LEGS = SessionTemplate(
    "Legs",
    frozenset({QUADS, HAMSTRINGS, GLUTES, CALVES, CORE}),
    ("Back Squat 4x6-8", "Romanian Deadlift 3x8-10", "Walking Lunge 3x10/leg",
     "Leg Curl 3x12", "Standing Calf Raise 4x12"),
)  # fmt: skip
UPPER = SessionTemplate(
    "Upper",
    frozenset({CHEST, BACK, SHOULDERS, BICEPS, TRICEPS}),
    ("Bench Press 4x6-8", "Barbell Row 4x6-8", "Overhead Press 3x8-10",
     "Lat Pulldown 3x10", "Curl + Pushdown superset 3x12"),
)  # fmt: skip
LOWER = SessionTemplate(
    "Lower",
    frozenset({QUADS, HAMSTRINGS, GLUTES, CALVES, CORE}),
    ("Back Squat 4x6-8", "Romanian Deadlift 3x8", "Leg Press 3x10-12",
     "Leg Curl 3x12", "Hanging Leg Raise 3x12"),
)  # fmt: skip

# ------------------------------------------------------------------ splits ---
SPLITS: dict[str, Split] = {
    s.name: s
    for s in (
        Split("Full Body x1", (FULL_BODY,)),
        Split("Full Body x2", (FULL_BODY, FULL_BODY)),
        Split("Upper/Lower x1", (UPPER, LOWER)),
        Split("Full Body x3", (FULL_BODY, FULL_BODY, FULL_BODY)),
        Split("Push/Pull/Legs", (PUSH, PULL, LEGS)),
        Split("Upper/Lower x2", (UPPER, LOWER, UPPER, LOWER)),
        Split("PPL + Upper/Lower", (PUSH, PULL, LEGS, UPPER, LOWER)),
        Split("Push/Pull/Legs x2", (PUSH, PULL, LEGS, PUSH, PULL, LEGS)),
    )
}

# Most sessions per week each commitment level asks for.
MAX_DAYS: dict[Commitment, int] = {
    Commitment.LIGHT: 2,
    Commitment.MODERATE: 4,
    Commitment.DEDICATED: 6,
}


def candidate_splits(days: int, commitment: Commitment) -> list[Split]:
    """Splits that use exactly ``days`` sessions, best option first.

    The planner tries them in order and keeps the first one it can fit into the
    week without breaking recovery rules.
    """
    if days <= 0:
        return []
    by_days: dict[int, list[str]] = {
        1: ["Full Body x1"],
        2: ["Full Body x2", "Upper/Lower x1"],
        # Full body 3x is the classic choice for 3 days, but it needs rest days
        # between sessions. PPL works even on 3 days in a row, so it's the backup.
        # Dedicated lifters usually prefer PPL's higher per-muscle volume.
        3: (["Push/Pull/Legs", "Full Body x3"]
            if commitment is Commitment.DEDICATED
            else ["Full Body x3", "Push/Pull/Legs"]),
        4: ["Upper/Lower x2"],
        5: ["PPL + Upper/Lower"],
        6: ["Push/Pull/Legs x2"],
    }  # fmt: skip
    return [SPLITS[name] for name in by_days.get(min(days, 6), [])]
