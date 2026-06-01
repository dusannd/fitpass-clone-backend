from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.schemas.subscription import PlanCreate, PlanResponse, UserSubscriptionCreate, UserSubscriptionResponse
from app.api.dependencies import get_current_user_id, get_current_admin


router = APIRouter()


# --- 1. ADMIN: Create a new Subscription Plan (e.g. Gold, Standard) ---
@router.post("/plans", response_model=PlanResponse)
async def create_plan(
    plan: PlanCreate,
    db: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin) # <--- THE ADMIN BOUNCER IS HERE NOW
):
    new_plan = SubscriptionPlan(
        name=plan.name,
        description=plan.description,
        price=plan.price,
        duration_days=plan.duration_days
    )
    db.add(new_plan)
    await db.commit()
    await db.refresh(new_plan)
    return new_plan

# --- 2. PUBLIC: Get all available plans ---
@router.get("/plans", response_model=list[PlanResponse])
async def get_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SubscriptionPlan))
    return result.scalars().all()


# --- 3. USER: Subscribe to a plan ---
# NOTICE: We added Depends(get_current_user_id) here! This route is PROTECTED.
@router.post("/subscribe", response_model=UserSubscriptionResponse)
async def subscribe_user(
        subscription: UserSubscriptionCreate,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)  # <--- THE BOUNCER
):
    # Check if the plan exists
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
    plan = result.scalars().first()

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Calculate start and end dates
    start_date = datetime.now(timezone.utc)
    end_date = start_date + timedelta(days=plan.duration_days)

    # Create the user subscription
    new_sub = UserSubscription(
        user_id=current_user_id,  # We got this safely from the JWT token!
        plan_id=plan.id,
        start_date=start_date,
        end_date=end_date,
        is_active=1
    )

    db.add(new_sub)
    await db.commit()
    await db.refresh(new_sub)

    return new_sub