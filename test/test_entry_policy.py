from datetime import datetime, time
from types import SimpleNamespace

from app.api.entry_policy import check_entry_policy
from app.api.helpers import gym_timezone


# --- 1. HELPERS ---
# check_entry_policy only reads plan.locations and plan.rule, so plain namespaces
# stand in for the ORM rows - no database needed to ask "is the door open at 03:00?".

def night_plan(allowed_days: str | None = None) -> SimpleNamespace:
    rule = SimpleNamespace(
        allowed_time_start=time(22, 0),
        allowed_time_end=time(6, 0),
        allowed_days=allowed_days,
    )
    return SimpleNamespace(locations=[SimpleNamespace(id=1)], rule=rule)


def gym_local(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment on the gym's wall clock. 2026-09-28 is a Monday."""
    return datetime(2026, 9, 28 + day, hour, minute, tzinfo=gym_timezone())


# --- 2. OVERNIGHT WINDOWS ---
# A start later than the end (22:00-06:00) used to be checked as
# `22:00 <= t <= 06:00`, which no time satisfies, so every member of the plan was
# refused around the clock with no error anywhere to say why.

def test_overnight_window_is_open_on_both_sides_of_midnight():
    plan = night_plan()
    assert check_entry_policy(plan, 1, gym_local(0, 23)) is None
    assert check_entry_policy(plan, 1, gym_local(1, 3)) is None
    assert check_entry_policy(plan, 1, gym_local(0, 22)) is None  # inclusive start
    assert check_entry_policy(plan, 1, gym_local(1, 6)) is None   # inclusive end


def test_overnight_window_is_closed_during_the_day():
    plan = night_plan()
    assert check_entry_policy(plan, 1, gym_local(0, 12)) == "time"
    assert check_entry_policy(plan, 1, gym_local(0, 21, 59)) == "time"
    assert check_entry_policy(plan, 1, gym_local(1, 6, 1)) == "time"


def test_the_small_hours_count_as_the_night_the_window_opened():
    # Monday nights only: 03:00 on Tuesday is still Monday's shift, and 03:00 on
    # Monday belongs to Sunday night, which the plan does not cover.
    plan = night_plan(allowed_days="0")
    assert check_entry_policy(plan, 1, gym_local(0, 23)) is None
    assert check_entry_policy(plan, 1, gym_local(1, 3)) is None
    assert check_entry_policy(plan, 1, gym_local(0, 3)) == "day"


def test_a_same_day_window_is_unchanged():
    plan = night_plan()
    plan.rule.allowed_time_start, plan.rule.allowed_time_end = time(9, 0), time(17, 0)
    assert check_entry_policy(plan, 1, gym_local(0, 12)) is None
    assert check_entry_policy(plan, 1, gym_local(0, 20)) == "time"
