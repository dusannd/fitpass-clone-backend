"""
The door policy: may this pass open this gym, right now?

This lived inline in app/api/access.py, where the QR turnstile is. The desk
worker's status check in app/api/worker.py needs exactly the same verdict - a
second copy of it would drift, and a desk that says "allowed to enter" while the
turnstile refuses the same person is worse than no check at all.

The rules themselves are unchanged from the turnstile's original version. What
DID change is the clock: the checks used to read .weekday() and .time() straight
off a UTC timestamp, so a plan open 09:00-17:00 was really being enforced as
11:00-19:00 local in summer. Everything here goes through to_gym_time first.
"""
from datetime import datetime, timezone

from app.api.helpers import to_gym_time
from app.models.subscription import SubscriptionPlan


def check_entry_policy(
        plan: SubscriptionPlan,
        location_id: int | None,
        now: datetime | None = None,
) -> str | None:
    """
    Returns None when the plan allows entry, otherwise a short reason code:
    "location", "day" or "time".

    A code rather than a message on purpose. The turnstile and the desk phrase
    the same refusal differently - one tells a member "Access Denied", the other
    tells a worker why the green light did not come on - so the wording belongs
    with the caller, not here.

    `plan.locations` and `plan.rule` must already be loaded. `locations` is
    lazy="selectin" on the model so it always is; `rule` is a plain relationship
    and needs an explicit selectinload, or serialization blows up with
    MissingGreenlet.

    Passing location_id=None skips the location check. The desk may ask about a
    member without naming a gym ("is this pass any good at all?"), and answering
    that with a location refusal would be a lie.
    """
    # --- 1. LOCATION: does this plan grant access to this gym? ---
    # A plan with no locations attached matches nothing, which denies entry
    # everywhere. That is deny-by-default and intentional.
    if location_id is not None:
        allowed_location_ids = [loc.id for loc in plan.locations]
        if location_id not in allowed_location_ids:
            return "location"

    # --- 2. TIME/DAY RULE: no rule at all means unrestricted (24/7, every day) ---
    rule = plan.rule
    if not rule:
        return None

    # The gym's wall clock, not UTC - see the module docstring.
    local_now = to_gym_time(now or datetime.now(timezone.utc))
    current_time = local_now.time()
    current_weekday = local_now.weekday()  # 0=Monday ... 6=Sunday, matches allowed_days

    # --- 3. TIME WINDOW ---
    # Both boundaries have to be set for the window to mean anything, and the
    # comparison stays inclusive on both ends, exactly as the turnstile had it.
    # A start later than the end is an overnight window (22:00-06:00). A plain
    # `start <= t <= end` can never hold for one, so it locked every member of
    # such a plan out around the clock. It is open from the start until midnight
    # OR from midnight until the end.
    window_day = current_weekday
    if rule.allowed_time_start and rule.allowed_time_end:
        start, end = rule.allowed_time_start, rule.allowed_time_end
        if start <= end:
            if not (start <= current_time <= end):
                return "time"
        else:
            if not (current_time >= start or current_time <= end):
                return "time"
            # The small hours belong to the night the window opened on: at 03:00
            # on Tuesday, a "Monday 22:00-06:00" pass is still on its Monday.
            if current_time <= end:
                window_day = (current_weekday - 1) % 7

    # --- 4. DAY RULE ---
    if rule.allowed_days:
        allowed_days_list = [int(d) for d in rule.allowed_days.split(",") if d.strip() != ""]
        if window_day not in allowed_days_list:
            return "day"

    return None
