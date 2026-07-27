from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timezone

from app.core.database import get_db
from app.api.dependencies import get_current_user_id
from app.models.subscription import UserSubscription
from app.core.security import create_qr_token
from app.schemas.access import QRTokenResponse

router = APIRouter()

@router.get("/qr-token", response_model=QRTokenResponse)
async def get_qr_access_token(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id)
):
    """
    Member Route: Generates a short-lived (60s) QR token for gym entry.
    Strictly requires an active subscription.
    """
    now = datetime.now(timezone.utc)

    # 1. Check if the user has an active, non-expired subscription
    stmt = select(UserSubscription).where(
        and_(
            UserSubscription.user_id == user_id,
            UserSubscription.is_active == 1,
            UserSubscription.end_date > now
        )
    )
    result = await db.execute(stmt)
    active_sub = result.scalars().first()

    # If no active subscription is found, block QR generation
    if not active_sub:
        raise HTTPException(
            status_code=403,
            detail="You do not have an active subscription. Please purchase one to enter."
        )

    # 2. Generate the 60-second secure token
    token = create_qr_token(user_id)

    return {"qr_token": token, "expires_in_seconds": 60}