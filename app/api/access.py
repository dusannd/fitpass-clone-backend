from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timezone
import jwt
import os

from app.core.database import get_db
from app.core.security import create_qr_token
from app.api.dependencies import get_current_user_id
from app.models.access import EntryLog
from app.models.subscription import UserSubscription
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


@router.post("/scan", response_model=ScanResponse)
async def scan_qr(scan_data: ScanRequest, db: AsyncSession = Depends(get_db)):
    """
    Simulates the physical scanner at the gym door.
    Includes Redis anti-replay protection.
    """

    # 1. REDIS ANTI-REPLAY CHECK: Has this token been used already?
    is_used = await redis_db.get(scan_data.qr_token)
    if is_used:
        return ScanResponse(
            access_granted=False,
            message="Security Alert: This QR code has already been used!",
            user_id=0
        )

    # 2. Decode and verify the JWT token
    try:
        payload = jwt.decode(scan_data.qr_token, SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("type") != "qr_access":
            raise jwt.InvalidTokenError()

        user_id = int(payload.get("sub"))

    except jwt.ExpiredSignatureError:
        return ScanResponse(access_granted=False, message="QR Code expired. Refresh your app.", user_id=0)
    except Exception:
        return ScanResponse(access_granted=False, message="Invalid QR Code", user_id=0)

    # 3. Check if the user has an ACTIVE subscription right now
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(UserSubscription).where(
            and_(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
    )
    active_sub = result.scalars().first()

    entry_log = EntryLog(user_id=user_id)

    if not active_sub:
        entry_log.access_granted = False
        entry_log.reason = "No active subscription found or subscription expired"
        db.add(entry_log)
        await db.commit()
        return ScanResponse(access_granted=False, message=entry_log.reason, user_id=user_id)

    # 4. Grant access
    entry_log.access_granted = True
    entry_log.reason = "Success"
    db.add(entry_log)
    await db.commit()

    # 5. REDIS SAVE: Mark this token as "used" so nobody else can use it.
    # 'ex=60' means Redis will automatically delete this record after 60 seconds
    # (because the token itself expires in 60s anyway, no need to keep it forever).
    await redis_db.set(scan_data.qr_token, "used", ex=60)

    return ScanResponse(
        access_granted=True,
        message="DOOR OPENED! Welcome to the gym.",
        user_id=user_id
    )