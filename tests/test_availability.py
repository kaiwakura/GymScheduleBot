from datetime import datetime

from app.scheduler.availability import find_free_slots, free_slots_for_day, merge_intervals, round_up
from tests.conftest import MONDAY, TZ, at, block_whole_day, busy


def test_round_up():
    assert round_up(datetime(2026, 1, 1, 7, 5, tzinfo=TZ)) == datetime(2026, 1, 1, 7, 15, tzinfo=TZ)
    assert round_up(datetime(2026, 1, 1, 7, 0, tzinfo=TZ)) == datetime(2026, 1, 1, 7, 0, tzinfo=TZ)
    assert round_up(datetime(2026, 1, 1, 7, 50, tzinfo=TZ)) == datetime(2026, 1, 1, 8, 0, tzinfo=TZ)


def test_merge_intervals_overlapping_and_unsorted():
    merged = merge_intervals([(at(0, 10), at(0, 12)), (at(0, 8), at(0, 9)), (at(0, 11), at(0, 13))])
    assert merged == [(at(0, 8), at(0, 9)), (at(0, 10), at(0, 13))]


def test_empty_day_is_one_big_slot(prefs):
    slots = free_slots_for_day(MONDAY, [], prefs)
    assert len(slots) == 1
    assert slots[0].start == at(0, 6) and slots[0].end == at(0, 22)


def test_buffer_is_applied_around_busy_blocks(prefs):
    # Class 9-17. With a 15 min buffer the free slots are 6:00-8:45 and 17:15-22:00.
    slots = free_slots_for_day(MONDAY, [busy(0, 9, 17)], prefs)
    assert [(s.start, s.end) for s in slots] == [(at(0, 6), at(0, 8, 45)), (at(0, 17, 15), at(0, 22))]


def test_short_gaps_are_dropped(prefs):
    # Only 45 min free between 10:00 and 11:00 once buffers are applied -> not enough for 60 min.
    blocks = [busy(0, 6, 10), busy(0, 11, 22)]
    assert free_slots_for_day(MONDAY, blocks, prefs) == []


def test_past_times_are_excluded(prefs):
    now = at(0, 20, 10)  # 8:10pm Monday -> only 8:15-10pm left
    slots = free_slots_for_day(MONDAY, [], prefs, now=now)
    assert slots[0].start == at(0, 20, 15)
    assert slots[0].minutes == 105


def test_blocked_weekdays_have_no_slots(prefs):
    prefs.blocked_weekdays = {6}  # never on Sunday
    result = find_free_slots([], MONDAY, prefs)
    assert len(result) == 7
    assert result[date_of(6)] == []
    assert all(result[date_of(i)] for i in range(6))


def test_busy_block_spanning_midnight(prefs):
    # Overnight shift Mon 20:00 -> Tue 10:00 blocks both days' windows.
    blocks = [busy(0, 20, 23.99), busy(1, 0, 10)]
    result = find_free_slots(blocks, MONDAY, prefs)
    assert result[date_of(0)][-1].end == at(0, 19, 45)
    assert result[date_of(1)][0].start == at(1, 10, 15)


def test_whole_day_busy(prefs):
    assert find_free_slots([block_whole_day(2)], MONDAY, prefs)[date_of(2)] == []


def date_of(offset: int):
    return at(offset, 0).date()
