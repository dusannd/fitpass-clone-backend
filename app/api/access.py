from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_
from datetime import datetime, timezone
import jwt
import os

from app.core.database import get_db
from app.core.security import create_qr_token
from app.api.dependencies import get_current_user_id
from app.models.access import EntryLog
from app.models.subscription import UserSubscription, SubscriptionPlan
from app.schemas.access import QRTokenResponse, ScanRequest, ScanResponse
from app.core.redis_client import redis_db

router = APIRouter()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")


# --- 1. USER: Generates a QR Code for his phone ---
@router.post("/generate-qr", response_model=QRTokenResponse)
async def generate_qr(current_user_id: int = Depends(get_current_user_id)):
    """
    User requests a short-lived token to display as a QR code on their screen.
    """
    qr_token = create_qr_token(current_user_id)
    return {"qr_token": qr_token, "expires_in_seconds": 60}


# --- 2. DOOR SCANNER: Reads the QR code and verifies access ---
@router.post("/scan", response_model=ScanResponse)
async def scan_qr(scan_data: ScanRequest, db: AsyncSession = Depends(get_db)):
    """
    Simulates the physical scanner at the gym door.
    Includes location verification, time/day rules checking, and Redis anti-replay.
    """

    # 1. REDIS ANTI-REPLAY CHECK: Has this token been used already?
    is_used = await redis_db.get(scan_data.qr_token)
    if is_used:
        return ScanResponse(
            access_granted=False,
            message="Security Alert: This QR code has already been used!",
            user_id=0
        )

    # 2. DECODE JWT TOKEN: Verify signature and expiration
    try:
        payload = jwt.decode(scan_data.qr_token, SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "qr_access":
            raise jwt.InvalidTokenError()

        user_id = int(payload.get("sub"))

    except jwt.ExpiredSignatureError:
        return ScanResponse(access_granted=False, message="QR Code expired. Refresh your app.", user_id=0)
    except Exception:
        return ScanResponse(access_granted=False, message="Invalid QR Code", user_id=0)

    # 3. FETCH DB DATA: Get the user's active subscription, plan, allowed locations, and rules
    now = datetime.now(timezone.utc)

    stmt = (
        select(UserSubscription)
        .options(
            selectinload(UserSubscription.plan).selectinload(SubscriptionPlan.locations),
            selectinload(UserSubscription.plan).selectinload(SubscriptionPlan.rule)
        )
        .where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
    )
    result = await db.execute(stmt)
    active_sub = result.scalars().first()

    # Pre-create the log entry with the location where the scan happened
    entry_log = EntryLog(user_id=user_id, location_id=scan_data.location_id)

    # 4. CHECK A: Does the user have an ACTIVE subscription right now?
    if not active_sub:
        entry_log.access_granted = False
        entry_log.reason = "No active subscription found or subscription expired"
        db.add(entry_log)
        await db.commit()
        return ScanResponse(access_granted=False, message=entry_log.reason, user_id=user_id)

    plan = active_sub.plan

    # 5. CHECK B: Is the user allowed at THIS specific location?
    allowed_location_ids = [loc.id for loc in plan.locations]
    if scan_data.location_id not in allowed_location_ids:
        entry_log.access_granted = False
        entry_log.reason = "Subscription does not cover this gym location"
        db.add(entry_log)
        await db.commit()
        return ScanResponse(access_granted=False, message=entry_log.reason, user_id=user_id)

    # 6. CHECK C: Time and Day Rules (if any exist for this plan)
    if plan.rule:
        current_time = datetime.now().time()
        current_day = str(datetime.now().weekday())  # 0=Monday, 6=Sunday

        # Check allowed days
        if plan.rule.allowed_days and current_day not in plan.rule.allowed_days.split(","):
            entry_log.access_granted = False
            entry_log.reason = "Access not allowed on this day of the week"
            db.add(entry_log)
            await db.commit()
            return ScanResponse(access_granted=False, message=entry_log.reason, user_id=user_id)

        # Check allowed hours
        if plan.rule.allowed_time_start and plan.rule.allowed_time_end:
            if not (plan.rule.allowed_time_start <= current_time <= plan.rule.allowed_time_end):
                entry_log.access_granted = False
                entry_log.reason = f"Access only allowed between {plan.rule.allowed_time_start} and {plan.rule.allowed_time_end}"
                db.add(entry_log)
                await db.commit()
                return ScanResponse(access_granted=False, message=entry_log.reason, user_id=user_id)

    # 7. GRANT ACCESS: All checks passed!
    entry_log.access_granted = True
    entry_log.reason = "Success"
    db.add(entry_log)
    await db.commit()

    # 8. REDIS SAVE: Mark this token as "used" so nobody else can use it.
    # 'ex=60' means Redis will automatically delete this record after 60 seconds
    await redis_db.set(scan_data.qr_token, "used", ex=60)

    return ScanResponse(
        access_granted=True,
        message="DOOR OPENED! Welcome to the gym.",
        user_id=user_id
    )