"""
Small formatting helpers shared by the API routers.

These started life as private functions inside app/api/worker.py. The admin
router needs the same two, and copying the LIKE-escaping in particular would
have left two versions of a security-relevant detail to keep in sync.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.user import User


def build_like_pattern(raw: str) -> str:
    """
    Turns what the user typed into a safe 'contains' LIKE pattern.

    '%' and '_' are wildcards in SQL. Without escaping them, somebody typing a
    single '%' would match every user in the database, which is both a useless
    result and a needless full table scan. The backslash itself goes first,
    otherwise we would escape the escapes we just added.
    """
    escaped = raw.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def full_name(user: User | None) -> str:
    """
    Builds a display name that survives missing data.

    first_name and last_name are nullable, so an account created with nothing but
    an email would otherwise render as the literal string "None None" on the desk
    panel. Falls back to a placeholder rather than an empty string, so a row never
    looks like a rendering bug to whoever is reading it.
    """
    if user is None:
        return "Unknown user"

    return f"{user.first_name or ''} {user.last_name or ''}".strip() or "Unknown user"


def gym_timezone() -> ZoneInfo:
    """
    The gym's local zone, read from settings so a second location in another
    country is an environment variable rather than a code change.
    """
    return ZoneInfo(settings.GYM_TIMEZONE)


def ensure_utc(ts: datetime) -> datetime:
    """
    Stamps a stored timestamp as UTC if the database handed it back naive.

    SQLite drops the offset, so the test database returns naive datetimes while
    Postgres returns aware ones - and mixing the two is not a subtle difference:
    subtracting one from the other raises TypeError outright, and .astimezone() on
    a naive value silently assumes the SERVER's timezone instead of UTC.

    Anything that does arithmetic on a column value needs this. to_gym_time below
    is built on it for exactly that reason.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)

    return ts


def to_gym_time(ts: datetime) -> datetime:
    """
    Moves a stored timestamp onto the gym's wall clock.

    Naive values are stamped as UTC first (see ensure_utc), and that step is the
    whole point: it is what stops the same row bucketing one way under test and
    another in production - the kind of bug that only shows up once it is deployed
    somewhere that isn't UTC.
    """
    return ensure_utc(ts).astimezone(gym_timezone())


def gym_day_bounds_utc(days_back: int = 0) -> tuple[datetime, datetime]:
    """
    Returns [start, end) in UTC for a whole local day.

    Used instead of comparing func.date(timestamp) to date.today(). That
    comparison extracts the date inside the database, where it is UTC, and matches
    it against the server's local date - so a scan at 01:30 local (23:30 UTC the
    day before) was counted against yesterday. Working out the boundaries in local
    time and converting them back to UTC also leaves the column bare, so the
    timestamp index still gets used; date(timestamp) would have discarded it.
    """
    local_now = datetime.now(gym_timezone())
    local_midnight = (local_now - timedelta(days=days_back)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    start = local_midnight.astimezone(timezone.utc)
    end = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)

    return start, end
