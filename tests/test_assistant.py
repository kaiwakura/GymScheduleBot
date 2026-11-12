from datetime import time
from types import SimpleNamespace

from app.ai.assistant import explain_plan, parse_notes, parse_notes_with_rules
from app.models import Commitment, Preferences, TimeOfDay
from app.scheduler.planner import build_plan
from tests.conftest import MONDAY


# ------------------------------------------------------------ rule parser ---
def test_rules_detect_time_of_day():
    assert parse_notes_with_rules("I like to lift in the morning").preferred_time is TimeOfDay.MORNING
    assert parse_notes_with_rules("after work works best").preferred_time is TimeOfDay.EVENING
    no_mornings = parse_notes_with_rules("I hate mornings")
    assert no_mornings.preferred_time is TimeOfDay.EVENING
    assert no_mornings.earliest_start == time(10, 0)


def test_rules_detect_blocked_days():
    assert parse_notes_with_rules("no gym on sundays").blocked_weekdays == {6}
    assert parse_notes_with_rules("Saturdays off please").blocked_weekdays == {5}
    # "piano" ends in "no" — must not be read as "no ... monday"
    assert parse_notes_with_rules("piano on monday 5-6pm").blocked_weekdays == set()


def test_rules_detect_recurring_commitment():
    notes = parse_notes_with_rules("I have soccer on tuesdays 6-7:30pm")
    [r] = notes.recurring_busy
    assert (r.weekday, r.start, r.end, r.title) == (1, time(18, 0), time(19, 30), "Soccer")


def test_rules_detect_length_and_commitment():
    notes = parse_notes_with_rules("super busy semester, 45 min sessions max")
    assert notes.commitment is Commitment.LIGHT
    assert notes.session_minutes == 45


def test_notes_apply_to_preferences():
    prefs = Preferences(blocked_weekdays={0})
    updated = parse_notes_with_rules("evenings, no workouts on fridays").apply(prefs)
    assert updated.preferred_time is TimeOfDay.EVENING
    assert updated.blocked_weekdays == {0, 4}
    assert prefs.blocked_weekdays == {0}  # original untouched


def test_empty_note_changes_nothing():
    assert parse_notes("   ").apply(Preferences()) == Preferences()


# ------------------------------------------------- Claude path (fake client) ---
class FakeClient:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


def test_ai_tool_output_is_converted():
    tool_input = {
        "preferred_time": "evening",
        "commitment": "dedicated",
        "blocked_weekdays": ["sunday"],
        "recurring_busy": [{"weekday": "tuesday", "start": "18:00", "end": "19:30", "title": "Soccer"}],
    }
    client = FakeClient([SimpleNamespace(type="tool_use", input=tool_input)])
    notes = parse_notes("evenings, very committed, no sundays, soccer tue 6-7:30pm", client=client)
    assert notes.source == "ai"
    assert notes.commitment is Commitment.DEDICATED
    assert notes.blocked_weekdays == {6}
    assert notes.recurring_busy[0].start == time(18, 0)
    assert client.calls[0]["tool_choice"]["name"] == "set_preferences"


def test_ai_failure_falls_back_to_rules():
    notes = parse_notes("mornings please", client=FakeClient(error=RuntimeError("API down")))
    assert notes.source == "rules"
    assert notes.preferred_time is TimeOfDay.MORNING


def test_explanation_fallback_without_ai():
    plan = build_plan([], MONDAY, Preferences())
    client = FakeClient(error=RuntimeError("API down"))
    assert plan.split_name in explain_plan(plan, client=client)
