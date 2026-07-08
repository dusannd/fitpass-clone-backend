from fastapi import APIRouter, Depends, HTTPException, status
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
    GymLocationCreate, GymLocationResponse, GymLocationUpdate
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


@router.put("/locations/{location_id}", response_model=GymLocationResponse)
async def update_location(
        location_id: int,
        location_data: GymLocationUpdate,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin updates an existing gym location (e.g., changes address or 24/7 status).
    """
    result = await db.execute(select(GymLocation).where(GymLocation.id == location_id))
    location_to_update = result.scalars().first()

    if not location_to_update:
        raise HTTPException(status_code=404, detail="Gym location not found")

    # Update only the fields provided by the admin
    update_data = location_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(location_to_update, key, value)

    db.add(location_to_update)
    await db.commit()
    await db.refresh(location_to_update)

    return location_to_update


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
        location_id: int,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin deletes a gym location.
    Due to database design (ondelete="SET NULL" for logs, and "CASCADE" for plan links),
    this is safe to do without breaking user entry history.
    """
    result = await db.execute(select(GymLocation).where(GymLocation.id == location_id))
    location_to_delete = result.scalars().first()

    if not location_to_delete:
        raise HTTPException(status_code=404, detail="Gym location not found")

    await db.delete(location_to_delete)
    await db.commit()

    return None

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


        # --- 🛡️ BUG 3 FIX: STRICT VALIDATION ---
        if len(locations) != len(plan.location_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more location_ids provided do not exist."
            )
        # --- END FIX ---

        new_plan.locations.extend(locations)


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


from app.schemas.subscription import PlanUpdate  # Ne zaboravi da importuješ PlanUpdate na vrhu!


@router.put("/plans/{plan_id}", response_model=PlanResponse)
async def update_plan(
        plan_id: int,
        plan_data: PlanUpdate,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin updates an existing subscription plan.
    Only provided fields will be updated.
    """
    # 1. Find the plan in the database
    stmt = select(SubscriptionPlan).options(
        selectinload(SubscriptionPlan.locations),
        selectinload(SubscriptionPlan.rule)
    ).where(SubscriptionPlan.id == plan_id)

    result = await db.execute(stmt)
    plan_to_update = result.scalars().first()

    if not plan_to_update:
        raise HTTPException(status_code=404, detail="Plan not found")

    # 2. Update only the fields that the client sent
    update_data = plan_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(plan_to_update, key, value)

    # 3. Save changes
    db.add(plan_to_update)
    await db.commit()
    await db.refresh(plan_to_update)

    return plan_to_update


@router.delete("/plans/{plan_id}")
async def delete_plan(
        plan_id: int,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin deletes a subscription plan.
    SMART DELETE:
    - If the plan was never bought by anyone, it is permanently deleted (Hard Delete).
    - If users have bought this plan in the past, it is archived (Soft Delete)
      to preserve historical entry logs and subscriptions.
    """
    # 1. Check if plan exists
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    plan_to_delete = result.scalars().first()

    if not plan_to_delete:
        raise HTTPException(status_code=404, detail="Plan not found")

    # 2. Check if any user has EVER bought this plan
    sub_check = await db.execute(
        select(UserSubscription).where(UserSubscription.plan_id == plan_id)
    )
    existing_subscriptions = sub_check.scalars().all()

    if existing_subscriptions:
        # SOFT DELETE: Users exist. Just hide it from the public frontend.
        plan_to_delete.is_active = False
        db.add(plan_to_delete)
        await db.commit()
        return {"message": "Plan successfully archived. Existing users can still use it, but no new purchases are allowed.", "hard_deleted": False}
    else:
        # HARD DELETE: Nobody ever bought it. Safe to remove completely.
        await db.delete(plan_to_delete)
        await db.commit()
        return {"message": "Plan permanently deleted from the database.", "hard_deleted": True}


# ==========================================
# PUBLIC ROUTES (USER FRONTEND)
# ==========================================

@router.get("/plans", response_model=List[PlanResponse])
async def get_plans(db: AsyncSession = Depends(get_db)):
    """
    Public route: Get all ACTIVE plans.
    Archived (Soft Deleted) plans are hidden from the frontend.
    """
    stmt = (
        select(SubscriptionPlan)
        .options(
            selectinload(SubscriptionPlan.locations),
            selectinload(SubscriptionPlan.rule)
        )
        .where(SubscriptionPlan.is_active == True) # <--- SMART FILTER
    )
    result = await db.execute(stmt)
    return result.scalars().all()

"""

# ==========================================
# 3. USER SUBSCRIPTIONS (LOGGED IN USERS)
# ==========================================
@router.post("/subscribe", response_model=UserSubscriptionResponse)
async def subscribe_user(
        subscription: UserSubscriptionCreate,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
   
      # User buys a plan. Prevents buying if an active subscription already exists.
    
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

"""