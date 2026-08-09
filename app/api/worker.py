from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.websockets import ws_manager
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
        reason="Manual Override by Desk Worker"
    )

    db.add(entry_log)
    await db.commit()
    await db.refresh(entry_log)

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


@router.get("/currently-inside")
async def get_currently_inside(
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Worker Dashboard: Returns a list of all users currently inside the gym.
    Uses a SQL Subquery to find the absolute latest log for each user,
    and checks if that latest log was a successful 'ENTRY'.
    """
    # 1. Subquery: Find the MAX timestamp (latest log) for each user
    subq = (
        select(EntryLog.user_id, func.max(EntryLog.timestamp).label("max_ts"))
        .group_by(EntryLog.user_id)
        .subquery()
    )

    # 2. Main Query: Join EntryLog with the subquery to get the actual row data
    stmt = (
        select(EntryLog)
        .join(
            subq,
            and_(
                EntryLog.user_id == subq.c.user_id,
                EntryLog.timestamp == subq.c.max_ts
            )
        )
        .where(
            EntryLog.action_type == "ENTRY",
            EntryLog.access_granted == True
        )
        .options(selectinload(EntryLog.user))  # Eager load user data
        .order_by(EntryLog.timestamp.desc())
    )

    result = await db.execute(stmt)
    active_logs = result.scalars().all()

    # 3. Format the response for the frontend
    response = []
    for log in active_logs:
        response.append({
            "user_id": log.user.id,
            "full_name": f"{log.user.first_name} {log.user.last_name}",
            "email": log.user.email,
            "entered_at": log.timestamp
        })

    return response


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
    # Import inside the function to avoid circular import issues
    from app.core.redis_client import redis_db

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