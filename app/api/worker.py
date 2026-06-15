from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timezone

from app.core.database import get_db
from app.models.user import User
from app.models.access import EntryLog
from app.models.subscription import UserSubscription, SubscriptionPlan
from app.api.dependencies import RequireRole

router = APIRouter()

# Bouncer for the desk worker role
get_current_worker = RequireRole("worker")


@router.post("/manual-entry/{target_user_id}")
async def manual_entry_override(
        target_user_id: int,
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

    entry_log = EntryLog(
        user_id=target_user.id,
        worker_id=worker_id,
        access_granted=True,
        reason="Manual Override by Desk Worker"
    )

    db.add(entry_log)
    await db.commit()
    await db.refresh(entry_log)

    return {
        "status": "success",
        "message": f"DOOR OPENED! User {target_user.first_name} was let in by worker ID {worker_id}",
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
            "full_name": f"{user.first_name} {user.last_name}",
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
        "full_name": f"{user.first_name} {user.last_name}",
        "email": user.email,
        "has_active_subscription": True,
        "plan_name": plan_name,
        "days_left": days_left,
        "expires_on": user_sub.end_date,
        "message": "Subscription active. Allowed to enter."
    }