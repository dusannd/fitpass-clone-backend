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

    if rule.allowed_days:
        allowed_days_list = [int(d) for d in rule.allowed_days.split(",") if d.strip() != ""]
        if current_weekday not in allowed_days_list:
            return "day"

    # Both boundaries have to be set for the window to mean anything, and the
    # comparison stays inclusive on both ends, exactly as the turnstile had it.
    if rule.allowed_time_start and rule.allowed_time_end:
        if not (rule.allowed_time_start <= current_time <= rule.allowed_time_end):
            return "time"

    return None
