from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_
from datetime import datetime, timezone
from typing import List


from app.core.database import get_db
from app.models.subscription import SubscriptionPlan, UserSubscription, GymLocation, SubscriptionRule
from app.schemas.subscription import (
    PlanCreate, PlanUpdate, PlanResponse,
    MySubscriptionResponse,
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


@router.get("/locations", response_model=List[GymLocationResponse])
async def get_locations(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin route: list all gym locations. Used by ManagePlans.tsx to build the
    location checkboxes when creating/editing a plan.
    """
    result = await db.execute(select(GymLocation))
    return result.scalars().all()


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
        duration_days=plan.duration_days,
        tier=plan.tier,
        includes_trainer=plan.includes_trainer,
        includes_group_classes=plan.includes_group_classes,
        has_sauna_access=plan.has_sauna_access,
        has_towel_service=plan.has_towel_service,
        allows_guest=plan.allows_guest,
    )

    # 2. Assign allowed locations (Many-to-Many)
    if plan.location_ids:
        loc_result = await db.execute(select(GymLocation).where(GymLocation.id.in_(plan.location_ids)))
        locations = loc_result.scalars().all()

        # --- STRICT VALIDATION: make sure every location_id actually exists ---
        if len(locations) != len(set(plan.location_ids)):
            raise HTTPException(
                status_code=400,
                detail="One or more location_ids provided do not exist."
            )

        for loc in locations:
            new_plan.locations.append(loc)

    # 3. Save the plan first so it gets an ID (the rule below needs it)
    db.add(new_plan)
    await db.commit()
    await db.refresh(new_plan)

    # 4. Create the rule if provided (One-to-One)
    if plan.rule:
        new_rule = SubscriptionRule(
            plan_id=new_plan.id,
            allowed_time_start=plan.rule.allowed_time_start,
            allowed_time_end=plan.rule.allowed_time_end,
            allowed_days=plan.rule.allowed_days
        )
        db.add(new_rule)
        await db.commit()

    # 5. Fetch the fully loaded plan to return to the client
    stmt = select(SubscriptionPlan).options(
        selectinload(SubscriptionPlan.locations),
        selectinload(SubscriptionPlan.rule)
    ).where(SubscriptionPlan.id == new_plan.id)

    final_result = await db.execute(stmt)
    return final_result.scalars().first()


@router.get("/plans/all", response_model=List[PlanResponse])
async def get_all_plans(
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin route: list EVERY plan, active or archived.
    (GET /plans below only returns active ones, so without this the admin
    panel would lose sight of a plan the moment it gets deactivated.)
    """
    stmt = select(SubscriptionPlan).options(
        selectinload(SubscriptionPlan.locations),
        selectinload(SubscriptionPlan.rule)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/plans/{plan_id}", response_model=PlanResponse)
async def update_plan(
        plan_id: int,
        plan_data: PlanUpdate,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin updates an existing subscription plan's basic fields.
    Only provided fields will be updated. (Locations/rule are managed separately.)
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


@router.put("/plans/{plan_id}/toggle-active", response_model=PlanResponse)
async def toggle_plan_active(
        plan_id: int,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin soft-deletes/restores a plan by flipping is_active.
    Deactivated plans disappear from GET /plans, but members who already
    bought them keep access until their subscription naturally expires.
    """
    stmt = select(SubscriptionPlan).options(
        selectinload(SubscriptionPlan.locations),
        selectinload(SubscriptionPlan.rule)
    ).where(SubscriptionPlan.id == plan_id)

    result = await db.execute(stmt)
    plan_to_toggle = result.scalars().first()

    if not plan_to_toggle:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan_to_toggle.is_active = not plan_to_toggle.is_active
    db.add(plan_to_toggle)
    await db.commit()
    await db.refresh(plan_to_toggle)

    return plan_to_toggle


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
# PUBLIC / MEMBER ROUTES
# ==========================================

@router.get("/plans", response_model=List[PlanResponse])
async def get_plans(db: AsyncSession = Depends(get_db)):
    """
    Public route: Get all ACTIVE plans.
    Archived (Soft Deleted) plans are hidden from the frontend.
    Locations + rule are eagerly loaded so the pricing cards can render
    everything in one request.
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


@router.get("/my-subscription", response_model=MySubscriptionResponse)
async def get_my_subscription(
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id)
):
    """
    Member route: returns the caller's currently active subscription
    (is_active == 1 and not yet expired), with its plan, rule and
    locations eagerly loaded.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(UserSubscription)
        .options(
            selectinload(UserSubscription.plan).selectinload(SubscriptionPlan.locations),
            selectinload(UserSubscription.plan).selectinload(SubscriptionPlan.rule),
        )
        .where(
            and_(
                UserSubscription.user_id == current_user_id,
                UserSubscription.is_active == 1,
                UserSubscription.end_date > now
            )
        )
        .order_by(UserSubscription.end_date.desc())
    )
    result = await db.execute(stmt)
    active_sub = result.scalars().first()

    if not active_sub:
        raise HTTPException(status_code=404, detail="No active subscription found.")

    return active_sub
