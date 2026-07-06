from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, date
from typing import List

from app.core.database import get_db
from app.api.dependencies import get_current_admin
from app.models.access import EntryLog
from app.models.user import User
from app.schemas.access import AdminEntryLogResponse

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
        limit: int = 50,
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
        .limit(limit)
    )

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/audit/manual-overrides", response_model=List[AdminEntryLogResponse])
async def audit_worker_overrides(
        limit: int = 100,
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
        .limit(limit)
    )

    result = await db.execute(stmt)
    return result.scalars().all()