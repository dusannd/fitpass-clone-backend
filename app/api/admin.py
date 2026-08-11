from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, date, timedelta
from typing import List

from app.core.database import get_db
from app.api.dependencies import get_current_admin
from app.models.access import EntryLog
from app.models.user import User, Role
from app.schemas.access import AdminEntryLogResponse
from app.schemas.user import RoleManageRequest, StaffResponse


router = APIRouter()


# Notice the Depends(get_current_admin)! Only admins can enter here.
@router.get("/analytics/today")
async def get_todays_analytics(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)  # <--- ADMIN BOUNCER
):
    """
    Returns gym statistics for the current day.
    """
    today = date.today()

    # 1. Total number of successful entries today
    result_entries = await db.execute(
        select(func.count(EntryLog.id)).where(
            func.date(EntryLog.timestamp) == today,
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
            func.date(EntryLog.timestamp) == today,
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


@router.get("/users/{target_user_id}/logs", response_model=List[AdminEntryLogResponse])
async def get_user_entry_history(
        target_user_id: int,
        skip: int = 0,  # <--- NEW: Pagination offset,
        limit: int = 50,  # <--- NEW: Pagination limit,
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

    # 2. Fetch logs with joined location and worker data, ordered by newest first
    stmt = (
        select(EntryLog)
        .options(
            selectinload(EntryLog.location),
            selectinload(EntryLog.worker)
        )
        .where(EntryLog.user_id == target_user_id)
        .order_by(EntryLog.timestamp.desc())
        .offset(skip)  # <--- NEW: Apply skip
        .limit(limit)  # <--- Applied limit
    )

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/audit/manual-overrides", response_model=List[AdminEntryLogResponse])
async def audit_worker_overrides(
        skip: int = 0,     # <--- NEW: Pagination offset
        limit: int = 100,  # <--- NEW: Pagination limit
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Anti-Fraud Audit: Returns a list of all instances where a desk worker
    manually opened the gym door for someone.
    """
    # Fetch logs where worker_id is NOT NULL
    stmt = (
        select(EntryLog)
        .options(
            selectinload(EntryLog.user),  # Who entered?
            selectinload(EntryLog.worker),  # Which worker let them in?
            selectinload(EntryLog.location)  # At which gym?
        )
        .where(EntryLog.worker_id.isnot(None))  # <--- SMART FILTER
        .order_by(EntryLog.timestamp.desc())
        .offset(skip)  # <--- NEW: Apply skip
        .limit(limit)  # <--- Applied limit
    )

    result = await db.execute(stmt)
    return result.scalars().all()


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


from datetime import timedelta

@router.get("/analytics/weekly")
async def get_weekly_attendance(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Returns real attendance counts for the last 7 days grouped by day.
    """
    today = date.today()
    seven_days_ago = today - timedelta(days=6)

    # Query all successful entry logs in the last 7 days
    result = await db.execute(
        select(EntryLog).where(
            func.date(EntryLog.timestamp) >= seven_days_ago,
            EntryLog.access_granted == True
        )
    )
    logs = result.scalars().all()

    # Initialize last 7 days map (e.g. Mon, Tue, Wed...)
    days_map = {}
    for i in range(7):
        day_date = seven_days_ago + timedelta(days=i)
        day_name = day_date.strftime("%a")  # Mon, Tue, Wed...
        days_map[day_name] = 0

    # Count real entries per day
    for log in logs:
        if log.timestamp:
            day_name = log.timestamp.strftime("%a")
            if day_name in days_map:
                days_map[day_name] += 1

    # Format response for Recharts
    weekly_data = [{"day": day, "entries": count} for day, count in days_map.items()]
    return weekly_data