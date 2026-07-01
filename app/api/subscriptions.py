from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_
from datetime import datetime, timedelta, timezone
from typing import List

from app.core.database import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription, GymLocation, SubscriptionRule
from app.schemas.subscription import (
    PlanCreate, PlanResponse,
    UserSubscriptionCreate, UserSubscriptionResponse,
    GymLocationCreate, GymLocationResponse
)
from app.api.dependencies import get_current_user_id, RequireRole

router = APIRouter()

# --- BOUNCERS ---
get_current_admin = RequireRole("admin")


# ==========================================
# 1. GYM LOCATIONS (ADMIN ONLY)
# ==========================================
@router.post("/locations", response_model=GymLocationResponse)
async def create_location(
        location: GymLocationCreate,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin registers a new physical gym location.
    """
    new_location = GymLocation(**location.model_dump())
    db.add(new_location)
    await db.commit()
    await db.refresh(new_location)
    return new_location


@router.get("/locations", response_model=List[GymLocationResponse])
async def get_locations(db: AsyncSession = Depends(get_db)):
    """
    Public route: See all available gym locations.
    """
    result = await db.execute(select(GymLocation))
    return result.scalars().all()


# ==========================================
# 2. SUBSCRIPTION PLANS (ADMIN ONLY)
# ==========================================
@router.post("/plans", response_model=PlanResponse)
async def create_plan(
        plan: PlanCreate,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin creates a new Subscription Plan (e.g., Student Plan).
    Assigns allowed locations and time rules dynamically.
    """
    # 1. Create the basic plan
    new_plan = SubscriptionPlan(
        name=plan.name,
        description=plan.description,
        price=plan.price,
        duration_days=plan.duration_days
    )

    # 2. Assign allowed locations (Many-to-Many)
    if plan.location_ids:
        loc_result = await db.execute(select(GymLocation).where(GymLocation.id.in_(plan.location_ids)))
        locations = loc_result.scalars().all()
        new_plan.locations.extend(locations)

     # --- BUG 3 FIX: STRICT VALIDATION ---
     # If the number of found locations doesn't match the requested number, some IDs are invalid.
    if len(locations) != len(plan.location_ids):
        raise HTTPException(
             status_code=400,
              detail="One or more location_ids provided do not exist."
         )


    # 3. Create rules if provided (One-to-One)
    db.add(new_plan)
    await db.commit()  # Commit so new_plan gets an ID
    await db.refresh(new_plan)

    if plan.rule:
        new_rule = SubscriptionRule(
            plan_id=new_plan.id,
            allowed_time_start=plan.rule.allowed_time_start,
            allowed_time_end=plan.rule.allowed_time_end,
            allowed_days=plan.rule.allowed_days
        )
        db.add(new_rule)
        await db.commit()

    # 4. Fetch the fully loaded plan to return to the client
    stmt = select(SubscriptionPlan).options(
        selectinload(SubscriptionPlan.locations),
        selectinload(SubscriptionPlan.rule)
    ).where(SubscriptionPlan.id == new_plan.id)

    final_result = await db.execute(stmt)
    return final_result.scalars().first()


@router.get("/plans", response_model=List[PlanResponse])
async def get_plans(db: AsyncSession = Depends(get_db)):
    """
    Public route: Get all available plans, including their rules and allowed locations.
    """
    stmt = select(SubscriptionPlan).options(
        selectinload(SubscriptionPlan.locations),
        selectinload(SubscriptionPlan.rule)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ==========================================
# 3. USER SUBSCRIPTIONS (LOGGED IN USERS)
# ==========================================
@router.post("/subscribe", response_model=UserSubscriptionResponse)
async def subscribe_user(
        subscription: UserSubscriptionCreate,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
       User buys a plan. Prevents buying if an active subscription already exists.
    """
    # --- NEW: PREVENT DOUBLE SUBSCRIPTIONS ---
    # Check if the user already has an active subscription
    now = datetime.now(timezone.utc)
    active_sub_check = await db.execute(
        select(UserSubscription).where(
            and_(
                UserSubscription.user_id == current_user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
    )
    if active_sub_check.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="You already have an active subscription. Wait for it to expire."
        )
    # --- END NEW VALIDATION ---

    # 1. Check if the requested plan exists
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
    plan = result.scalars().first()

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    start_date = datetime.now(timezone.utc)
    end_date = start_date + timedelta(days=plan.duration_days)

    new_sub = UserSubscription(
        user_id=current_user_id,
        plan_id=plan.id,
        start_date=start_date,
        end_date=end_date,
        is_active=1
    )

    db.add(new_sub)
    await db.commit()
    await db.refresh(new_sub)

    return new_sub