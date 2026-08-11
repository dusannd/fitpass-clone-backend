from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.redis_client import redis_db
from app.core.websockets import ws_manager
from app.models.user import User, Role
from app.models.access import EntryLog
from app.models.subscription import UserSubscription, SubscriptionPlan
from app.api.dependencies import RequireRole

router = APIRouter()

# Bouncer for the desk worker role
get_current_worker = RequireRole("worker")


def _build_like_pattern(raw: str) -> str:
    """
    Turns what the worker typed into a safe 'contains' LIKE pattern.

    '%' and '_' are wildcards in SQL. Without escaping them, a worker typing a
    single '%' would match every user in the database, which is both a useless
    result and a needless full table scan. The backslash itself goes first,
    otherwise we would escape the escapes we just added.
    """
    escaped = raw.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _full_name(user: User | None) -> str:
    """
    Builds a display name that survives missing data.

    first_name and last_name are nullable, so an account created with nothing but
    an email would otherwise render as the literal string "None None" on the desk
    panel. Falls back to a placeholder rather than an empty string, so a row never
    looks like a rendering bug to the worker reading it.
    """
    if user is None:
        return "Unknown user"

    return f"{user.first_name or ''} {user.last_name or ''}".strip() or "Unknown user"


@router.get("/search")
async def search_users(
        query: str = Query(..., min_length=2, max_length=50, description="Name or email fragment"),
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Desk worker autocomplete: find a member by name or email.

    Exists so the worker never has to know a numeric user ID - they type what the
    member tells them (a name or an email) and pick the row.

    Restricted to active members on purpose. Without the role join the dropdown
    also offers trainers, admins and other workers - handing out staff email
    addresses to anyone on the desk - and without is_active it offers accounts
    that were deactivated precisely so they could not get through the door.
    """
    pattern = _build_like_pattern(query)

    # The fourth condition matches against "first last" as one string, so typing
    # a full name finds the person. SQLAlchemy renders '+' on String columns as
    # the SQL concat operator, which works on both Postgres and SQLite.
    stmt = (
        select(User)
        .join(User.roles)  # Same role join coaching.py and workouts.py use
        .where(
            Role.name == "member",
            User.is_active == True,
            or_(
                User.first_name.ilike(pattern, escape="\\"),
                User.last_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                (User.first_name + " " + User.last_name).ilike(pattern, escape="\\"),
            )
        )
        .distinct()  # A many-to-many join can repeat a row; the dropdown must not
        .order_by(User.first_name, User.last_name, User.id)
        .limit(10)  # Autocomplete, not a report - 10 rows is all a dropdown can show
    )

    result = await db.execute(stmt)
    users = result.scalars().all()

    return [
        {
            "user_id": user.id,
            "full_name": _full_name(user),
            "email": user.email,
        }
        for user in users
    ]


@router.post("/manual-entry/{target_user_id}")
async def manual_entry_override(
        target_user_id: int,
        location_id: int,
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Desk worker manually opens the door for a user.
    Records WHICH worker opened the door.
    """
    result = await db.execute(select(User).where(User.id == target_user_id))
    target_user = result.scalars().first()

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

        # --- NEW: VALIDATE LOCATION ---
    from app.models.subscription import GymLocation  # Add import if needed
    loc_result = await db.execute(select(GymLocation).where(GymLocation.id == location_id))
    if not loc_result.scalars().first():
        raise HTTPException(status_code=404, detail="Gym location not found")

    # Update EntryLog to include location_id
    entry_log = EntryLog(
        user_id=target_user.id,
        worker_id=worker_id,
        location_id=location_id,  # <--- NEW: Store the location in the log
        access_granted=True,
        action_type="ENTRY",  # Spelled out rather than leaning on the column default
        reason="Manual Override by Desk Worker"
    )

    db.add(entry_log)
    await db.commit()
    await db.refresh(entry_log)

    # Mark the member as INSIDE for anti-passback. Redis is what the turnstile
    # actually reads (app/api/access.py), and it only rebuilds itself from the
    # database when the key is MISSING - so without this write the member keeps
    # the stale "OUTSIDE" left over from their last exit, and could generate a
    # second ENTRY QR and walk in again on the back of this one override.
    # force_checkout below does the mirror image of this.
    await redis_db.set(f"user_status:{target_user.id}", "INSIDE")

    # Notify the member's dashboard in real-time so it resyncs instantly
    await ws_manager.send_personal_message(
        message={
            "type": "ACCESS_EVENT",
            "access_granted": True,
            "action_type": "ENTRY",
            "reason": "Manual Override"
        },
        user_id=target_user.id
    )

    return {
        "status": "success",
        "message": f"DOOR OPENED! User {_full_name(target_user)} was let in by worker ID {worker_id}",
        "log_id": entry_log.id
    }


@router.get("/user/{target_user_id}/status")
async def check_user_status(
        target_user_id: int,
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)  # Only workers can access this endpoint
):
    """
    Desk worker checks the status of a user (to see if their subscription is active).
    """
    # 1. Find the user
    result_user = await db.execute(select(User).where(User.id == target_user_id))
    user = result_user.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 2. Check if the user has an active subscription
    now = datetime.now(timezone.utc)

    # SQLAlchemy magic: Join tables to get the plan name alongside the subscription data
    stmt = (
        select(UserSubscription, SubscriptionPlan.name)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            and_(
                UserSubscription.user_id == target_user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
    )
    result_sub = await db.execute(stmt)
    active_sub_record = result_sub.first()

    # 3. Prepare the response for the frontend application
    if not active_sub_record:
        return {
            "user_id": user.id,
            "full_name": _full_name(user),
            "email": user.email,
            "has_active_subscription": False,
            "message": "User DOES NOT have an active subscription! Do not let them in."
        }

    # If active, unpack the tuple returned by the database
    user_sub, plan_name = active_sub_record

    # Calculate how many days are left until expiration
    days_left = (user_sub.end_date - now).days

    return {
        "user_id": user.id,
        "full_name": _full_name(user),
        "email": user.email,
        "has_active_subscription": True,
        "plan_name": plan_name,
        "days_left": days_left,
        "expires_on": user_sub.end_date,
        "message": "Subscription active. Allowed to enter."
    }


@router.get("/currently-inside")
async def get_currently_inside(
        skip: int = Query(0, ge=0, description="Pagination offset"),
        limit: int = Query(10, ge=1, le=50, description="Max 50 records per page"),
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Worker Dashboard: Returns the users currently inside the gym, one page at a time.
    A user is inside when their latest GRANTED log was an 'ENTRY'.

    Paginated because a busy gym would otherwise ship the entire floor on every
    single request. 'total' is still the real headcount, so the panel can show
    "42 Active" while only rendering the ten rows it asked for.
    """
    # 1. Rank each user's GRANTED logs, newest first.
    #    Denied rows are thrown out BEFORE the ranking on purpose: a refused scan
    #    never moved anybody through a door, so it must not change who counts as
    #    inside. Ranking over every row instead - as this used to - meant one
    #    denied re-scan made a member who was genuinely in the building vanish
    #    from the list, leaving a person nobody could see or force-check-out.
    #    This mirrors resolve_user_status() in app/api/access.py, which is what
    #    the turnstile itself trusts; the two have to agree.
    ranked = (
        select(
            EntryLog.id,
            func.row_number().over(
                partition_by=EntryLog.user_id,
                # The id tiebreaker is not cosmetic: two logs can share a
                # timestamp, and without it such a user would rank two rows first
                # and appear twice.
                order_by=(EntryLog.timestamp.desc(), EntryLog.id.desc()),
            ).label("rn"),
        )
        .where(EntryLog.access_granted == True)
        .subquery()
    )

    # 2. Base Query: keep each user's newest granted log, and only if it was an ENTRY
    base = (
        select(EntryLog)
        .join(ranked, and_(EntryLog.id == ranked.c.id, ranked.c.rn == 1))
        .where(EntryLog.action_type == "ENTRY")
    )

    # 3. Count the whole result set before slicing it, so the badge stays honest
    total = await db.scalar(select(func.count()).select_from(base.subquery()))

    # 4. The page itself. The secondary sort on id is not cosmetic: 'timestamp'
    #    comes from the database clock, so two scans can tie, and paginating over
    #    an unstable sort silently repeats or skips rows between pages.
    stmt = (
        base
        .options(selectinload(EntryLog.user))  # Eager load user data
        .order_by(EntryLog.timestamp.desc(), EntryLog.id.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(stmt)
    active_logs = result.scalars().all()

    # 5. Format the response for the frontend
    items = []
    for log in active_logs:
        items.append({
            "user_id": log.user.id,
            "full_name": _full_name(log.user),
            "email": log.user.email,
            "entered_at": log.timestamp
        })

    return {"total": total or 0, "items": items}


@router.get("/logs")
async def get_recent_logs(
        skip: int = Query(0, ge=0, description="Pagination offset"),
        limit: int = Query(10, ge=1, le=50, description="Max 50 records per page"),
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Worker Dashboard: Chronological feed of every entry, exit and denied scan.

    The desk worker needs this to answer "who just walked through?" without
    calling an admin - the admin audit endpoints are role gated and filtered to
    manual overrides only.
    """
    # 1. Total row count, so the panel can render "Page 3 of 12"
    total = await db.scalar(select(func.count()).select_from(EntryLog))

    # 2. Newest first, with the same id tiebreaker the attendance list uses.
    #    selectinload keeps the loop below from lazy loading each user, which
    #    under asyncio would not just be an N+1 but a MissingGreenlet crash.
    stmt = (
        select(EntryLog)
        .options(selectinload(EntryLog.user))
        .order_by(EntryLog.timestamp.desc(), EntryLog.id.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(stmt)
    logs = result.scalars().all()

    items = []
    for log in logs:
        items.append({
            "id": log.id,
            "user_id": log.user_id,
            "full_name": _full_name(log.user),
            "action_type": log.action_type,
            "access_granted": log.access_granted,
            "reason": log.reason,
            "timestamp": log.timestamp,
        })

    return {"total": total or 0, "items": items}


@router.post("/force-checkout/{target_user_id}")
async def force_checkout(
        target_user_id: int,
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Worker Dashboard: Forcefully checks out a user if they forgot to scan the exit QR.
    Frees up their Redis status so they can enter again next time.
    """
    # 1. Reset Redis state to OUTSIDE
    redis_key = f"user_status:{target_user_id}"
    await redis_db.set(redis_key, "OUTSIDE")

    # 2. Write a manual EXIT log to the database
    entry_log = EntryLog(
        user_id=target_user_id,
        worker_id=worker_id,
        access_granted=True,
        action_type="EXIT",
        reason="Force Checkout by Worker"
    )
    db.add(entry_log)
    await db.commit()

    # Notify the member's dashboard in real-time so it resyncs instantly
    await ws_manager.send_personal_message(
        message={
            "type": "ACCESS_EVENT",
            "access_granted": True,
            "action_type": "EXIT",
            "reason": "Force Checkout"
        },
        user_id=target_user_id
    )

    return {"status": "success", "message": "User has been forcefully checked out."}