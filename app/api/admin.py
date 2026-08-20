from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, case
from sqlalchemy.orm import selectinload
from datetime import datetime, date, timedelta, timezone
from typing import List

from app.core.database import get_db
from app.api.dependencies import get_current_admin
from app.api.helpers import build_like_pattern, full_name, to_gym_time, gym_day_bounds_utc, gym_timezone
from app.models.access import EntryLog
from app.models.user import User, Role
from app.models.subscription import UserSubscription, SubscriptionPlan
from app.schemas.access import AdminEntryLogResponse, PaginatedEntryLogs
from app.schemas.user import RoleManageRequest, StaffResponse


router = APIRouter()


# Notice the Depends(get_current_admin)! Only admins can enter here.
@router.get("/analytics/today")
async def get_todays_analytics(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)  # <--- ADMIN BOUNCER
):
    """
    Returns gym statistics for the current day, as the gym's own clock sees it.
    """
    # "Today" means the local day, expressed as a UTC range. The old version
    # compared func.date(timestamp) - extracted in the database, in UTC - against
    # date.today() on the server, so a member scanning in at 01:30 local (23:30
    # UTC the day before) was counted against yesterday and vanished from this
    # card. Comparing a bare column against a range also keeps the timestamp index
    # usable, which wrapping it in date() did not.
    day_start, day_end = gym_day_bounds_utc()
    today = datetime.now(gym_timezone()).date()

    # 1. Total number of successful entries today
    result_entries = await db.execute(
        select(func.count(EntryLog.id)).where(
            EntryLog.timestamp >= day_start,
            EntryLog.timestamp < day_end,
            EntryLog.access_granted == True
        )
    )
    total_entries_today = result_entries.scalar()

    # 2. Total registered users in the gym
    result_users = await db.execute(select(func.count(User.id)))
    total_users = result_users.scalar()

    # 3. Failed entry attempts today (e.g., expired subs, invalid QR)
    result_failed = await db.execute(
        select(func.count(EntryLog.id)).where(
            EntryLog.timestamp >= day_start,
            EntryLog.timestamp < day_end,
            EntryLog.access_granted == False
        )
    )
    failed_attempts = result_failed.scalar()

    return {
        "date": str(today),
        "total_successful_entries_today": total_entries_today,
        "total_failed_attempts_today": failed_attempts,
        "total_registered_users": total_users,
        "requested_by_admin_id": admin_id
    }


@router.get("/analytics/finances")
async def get_finances(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Returns Monthly Recurring Revenue and the active-subscription count.

    MRR is normalised to 30 days rather than being a plain SUM(price). A plan
    carries its own duration_days, so summing raw prices would count a 365-day
    membership as if the whole year's fee arrived every month - on a gym selling
    any annual plan that reads several times higher than the real monthly figure.
    For a 30-day plan the two are identical, so this only differs where the plain
    sum would have been wrong.
    """
    # Same "active subscription" definition used by the turnstile (access.py), the
    # desk panel (worker.py) and the member page (subscriptions.py). All four have
    # to agree, or the dashboard contradicts the door.
    now = datetime.now(timezone.utc)

    # duration_days is nullable and could be 0 on a badly seeded plan. Dividing by
    # it unguarded would raise straight out of the database and take the whole
    # dashboard down, so anything non-positive falls back to 30.
    safe_duration = case(
        (SubscriptionPlan.duration_days > 0, SubscriptionPlan.duration_days),
        else_=30,
    )

    stmt = (
        select(
            func.count(UserSubscription.id),
            func.sum(SubscriptionPlan.price / safe_duration * 30),
        )
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            UserSubscription.is_active == 1,
            UserSubscription.end_date > now
        )
    )

    result = await db.execute(stmt)
    active_count, mrr_total = result.one()

    # An empty gym makes SUM() return NULL rather than 0
    total_users = await db.scalar(select(func.count(User.id)))

    return {
        "active_subscriptions": active_count or 0,
        "total_users": total_users or 0,
        "mrr": round(float(mrr_total or 0.0), 2),
    }


@router.get("/analytics/peak-hours")
async def get_peak_hours(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Shows when the gym is busiest, as entries per hour over the last 7 days.

    Only granted ENTRY scans count: a denied scan never moved anybody through the
    door, and an EXIT would count the same visit twice.
    """
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    # Only the timestamp column is needed, so there is no reason to build whole
    # EntryLog objects for what may be thousands of rows.
    stmt = (
        select(EntryLog.timestamp)
        .where(
            EntryLog.timestamp >= seven_days_ago,
            EntryLog.access_granted == True,
            EntryLog.action_type == "ENTRY"
        )
    )
    result = await db.execute(stmt)

    # Bucketing happens in Python rather than SQL on purpose: hour extraction is
    # EXTRACT(HOUR FROM ...) on Postgres but strftime('%H', ...) on SQLite, so a
    # database-side GROUP BY would work in production and break under test.
    # /analytics/weekly above groups in Python for the same reason.
    hourly_counts = {hour: 0 for hour in range(24)}

    for (timestamp,) in result.all():
        if timestamp is not None:
            # Converted before reading .hour. The column is UTC, so an 18:00
            # Belgrade rush hour was being charted at 16:00 - and a scan just
            # after local midnight was landing in the previous evening.
            hourly_counts[to_gym_time(timestamp).hour] += 1

    # Every hour is emitted even at zero, so the chart has 24 evenly spaced bars
    # instead of collapsing around whichever hours happened to be busy. All 24 are
    # kept rather than a 06:00-23:00 window because locations can be 24/7, and an
    # overnight spike is exactly the kind of thing worth seeing.
    return [
        {"hour": f"{hour:02d}:00", "count": count}
        for hour, count in sorted(hourly_counts.items())
    ]


@router.get("/users/search")
async def admin_search_users(
        query: str = Query(..., min_length=2, max_length=50, description="Name or email fragment"),
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin autocomplete for the audit dossier: find ANY user by name or email.

    Deliberately unfiltered, and NOT the same query as /worker/search. That one is
    restricted to active members because a desk worker should only ever be opening
    the door for one. An audit is the opposite situation: the accounts most worth
    looking up are the deactivated one somebody is appealing and the staff member
    whose overrides are being reviewed. Do not "fix" this to match the worker
    version - the missing role and is_active filters are the feature.
    """
    pattern = build_like_pattern(query)

    stmt = (
        select(User)
        .options(selectinload(User.roles))
        .where(
            or_(
                User.first_name.ilike(pattern, escape="\\"),
                User.last_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                # Matches "first last" as one string, so typing a full name works
                (User.first_name + " " + User.last_name).ilike(pattern, escape="\\"),
            )
        )
        .order_by(User.first_name, User.last_name, User.id)
        .limit(10)  # Autocomplete, not a report
    )

    result = await db.execute(stmt)
    users = result.scalars().all()

    return [
        {
            "user_id": user.id,
            "full_name": full_name(user),
            "email": user.email,
            # Both included so the dropdown can badge a banned account or a staff
            # member instead of showing every row as an ordinary member.
            "is_active": bool(user.is_active),
            "roles": [role.name for role in user.roles],
        }
        for user in users
    ]


@router.get("/users/{target_user_id}/logs", response_model=PaginatedEntryLogs)
async def get_user_entry_history(
        target_user_id: int,
        skip: int = Query(0, ge=0, description="Pagination offset"),
        limit: int = Query(10, ge=1, le=50, description="Max 50 records per page"),
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    User Dossier: Returns the entry history for a specific user.
    Helps admins see when and where the user scanned their app.
    """
    # 1. Check if user exists
    result_user = await db.execute(select(User).where(User.id == target_user_id))
    if not result_user.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")

    # 2. Count the whole history before slicing it, so the page counter is honest
    total = await db.scalar(
        select(func.count(EntryLog.id)).where(EntryLog.user_id == target_user_id)
    )

    # 3. Fetch logs with joined location and worker data, ordered by newest first
    stmt = (
        select(EntryLog)
        .options(
            selectinload(EntryLog.location),
            selectinload(EntryLog.worker),
            # AdminEntryLogResponse also carries `user`, and none of these three
            # relationships is lazy="selectin" on the model - so leaving this out
            # means Pydantic touches an unloaded attribute on an async session and
            # the request dies with MissingGreenlet.
            selectinload(EntryLog.user)
        )
        .where(EntryLog.user_id == target_user_id)
        # The id tiebreaker is not cosmetic: timestamps come from the database
        # clock and can tie, and paging over an unstable sort silently repeats
        # rows on one page and drops them from the next.
        .order_by(EntryLog.timestamp.desc(), EntryLog.id.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(stmt)
    return {"total": total or 0, "items": result.scalars().all()}


@router.get("/audit/manual-overrides", response_model=PaginatedEntryLogs)
async def audit_worker_overrides(
        skip: int = Query(0, ge=0, description="Pagination offset"),
        limit: int = Query(10, ge=1, le=50, description="Max 50 records per page"),
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Anti-Fraud Audit: Returns a list of all instances where a desk worker
    manually opened the gym door for someone.
    """
    # Count every override before slicing, so the page counter reflects the whole
    # audit trail rather than the page in front of the admin
    total = await db.scalar(
        select(func.count(EntryLog.id)).where(EntryLog.worker_id.isnot(None))
    )

    # Fetch logs where worker_id is NOT NULL
    stmt = (
        select(EntryLog)
        .options(
            selectinload(EntryLog.user),  # Who entered?
            selectinload(EntryLog.worker),  # Which worker let them in?
            selectinload(EntryLog.location)  # At which gym?
        )
        .where(EntryLog.worker_id.isnot(None))  # <--- SMART FILTER
        # id breaks timestamp ties, so a row cannot show up on two pages
        .order_by(EntryLog.timestamp.desc(), EntryLog.id.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(stmt)
    return {"total": total or 0, "items": result.scalars().all()}


# The roles that make somebody "staff". Named once so the HR list and any future
# staff-only check cannot drift apart.
STAFF_ROLES = ["admin", "worker", "trainer"]


@router.get("/hr/staff", response_model=List[StaffResponse])
async def get_staff(
        limit: int = Query(200, ge=1, le=500, description="Safety cap on the staff list"),
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    HR Panel: Returns every user holding at least one staff role.

    The panel used to fetch GET /users/ and filter for staff in the browser. That
    endpoint caps at 50 rows, so once the gym grew past 50 users any staff member
    outside that first page silently vanished from the list - no error, no empty
    state, just a missing person. Filtering in the database instead means the
    response contains the staff themselves rather than whichever staff happen to
    sit in the first page of the users table.
    """
    stmt = (
        select(User)
        # .any() builds an EXISTS over the user_roles table, so somebody holding
        # two staff roles still comes back as exactly one row - no join fan-out
        # to clean up afterwards with .distinct().
        .where(User.roles.any(Role.name.in_(STAFF_ROLES)))
        # Redundant today (User.roles is lazy="selectin" on the model) but stated
        # anyway, so this endpoint cannot quietly turn into N+1 if that default
        # ever changes.
        .options(selectinload(User.roles))
        # id last so the list cannot reshuffle between refreshes when two people
        # share a name.
        .order_by(User.first_name, User.last_name, User.id)
        .limit(limit)
    )

    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/hr/hire")
async def hire_staff(
        request: RoleManageRequest,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    HR Panel: Assigns a new role ('worker' or 'trainer') to an existing user via their email.
    """
    # 1. Ensure the requested role is valid (prevent making accidental admins unless intended)
    allowed_roles = ["worker", "trainer", "admin"]
    if request.role_name not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"Role '{request.role_name}' is not allowed for manual assignment.")

    # 2. Find the user by email, eagerly loading their current roles
    user_result = await db.execute(select(User).options(selectinload(User.roles)).where(User.email == request.email))
    user = user_result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail=f"User with email {request.email} not found.")

    # 3. Check if user already has the requested role
    if any(r.name == request.role_name for r in user.roles):
        raise HTTPException(status_code=400, detail=f"User is already a {request.role_name}.")

    # 4. Find or create the role in the database
    role_result = await db.execute(select(Role).where(Role.name == request.role_name))
    role_obj = role_result.scalars().first()

    if not role_obj:
        # Dynamically create the role if it doesn't exist yet
        role_obj = Role(name=request.role_name, description=f"System role: {request.role_name}")
        db.add(role_obj)
        await db.commit()
        await db.refresh(role_obj)

    # 5. Assign the role and save to database
    user.roles.append(role_obj)
    db.add(user)
    await db.commit()

    return {
        "status": "success",
        "message": f"User {user.email} has been successfully hired as a {request.role_name}."
    }


@router.post("/hr/fire")
async def fire_staff(
        request: RoleManageRequest,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    HR Panel: Revokes a specific role from a user.
    """
    # 1. Find the user
    user_result = await db.execute(select(User).options(selectinload(User.roles)).where(User.email == request.email))
    user = user_result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # 2. Prevent admins from accidentally removing the base 'member' role
    if request.role_name == "member":
        raise HTTPException(status_code=400, detail="Cannot remove the base 'member' role.")

    # 3. Find the role to remove
    role_to_remove = next((r for r in user.roles if r.name == request.role_name), None)

    if not role_to_remove:
        raise HTTPException(status_code=400, detail=f"User {user.email} does not have the '{request.role_name}' role.")

    # 4. Remove the role and save
    user.roles.remove(role_to_remove)
    db.add(user)
    await db.commit()

    return {
        "status": "success",
        "message": f"Role '{request.role_name}' has been revoked from {user.email}."
    }


@router.get("/analytics/weekly")
async def get_weekly_attendance(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Returns attendance counts for the last 7 local days, grouped by weekday.
    """
    # The window is anchored to the gym's calendar, not the server's. Only the
    # timestamp column itself is compared in UTC.
    today = datetime.now(gym_timezone()).date()
    seven_days_ago = today - timedelta(days=6)

    window_start, _ = gym_day_bounds_utc(days_back=6)

    # Only the timestamp is needed to bucket by day
    result = await db.execute(
        select(EntryLog.timestamp).where(
            EntryLog.timestamp >= window_start,
            EntryLog.access_granted == True
        )
    )

    # Initialize last 7 days map (e.g. Mon, Tue, Wed...)
    days_map = {}
    for i in range(7):
        day_date = seven_days_ago + timedelta(days=i)
        day_name = day_date.strftime("%a")  # Mon, Tue, Wed...
        days_map[day_name] = 0

    # Count real entries per day, on the gym's clock. Reading .strftime straight
    # off the stored value put a Sunday 01:00 visit on Saturday, because the row
    # is held as 23:00 UTC the day before.
    for (timestamp,) in result.all():
        if timestamp:
            day_name = to_gym_time(timestamp).strftime("%a")
            if day_name in days_map:
                days_map[day_name] += 1

    # Format response for Recharts
    weekly_data = [{"day": day, "entries": count} for day, count in days_map.items()]
    return weekly_data