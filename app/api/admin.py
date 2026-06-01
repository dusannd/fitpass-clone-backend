from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, date

from app.core.database import get_db
from app.api.dependencies import get_current_admin
from app.models.access import EntryLog
from app.models.user import User

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