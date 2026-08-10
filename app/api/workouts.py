from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import delete, insert
from typing import List

from app.core.database import get_db
from app.api.dependencies import RequireRole
from app.models.user import User, Role
from app.schemas.user import UserResponse
from app.models.workout import WorkoutPlan, WorkoutSession, ExerciseLog, user_dismissed_plans
from app.schemas.workout import WorkoutPlanResponse, WorkoutSessionCreate, WorkoutSessionResponse

router = APIRouter()

# Bouncer: Available to all standard members
get_current_member = RequireRole("member")


@router.get("/trainers", response_model=List[UserResponse])
async def get_all_trainers(
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Browse a list of all active trainers in the gym.
    """
    # Join Users and Roles, filter by the 'trainer' role
    stmt = (
        select(User)
        .join(User.roles)
        .options(
            selectinload(User.roles),
            selectinload(User.subscriptions),  # <--- FIX: Eagerly load subscriptions to satisfy Pydantic
            selectinload(User.profile)  # NEW: bio + specialties for the marketplace cards
        )
        .where(Role.name == "trainer")
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/trainers/{trainer_id}/plans", response_model=List[WorkoutPlanResponse])
async def get_trainer_plans(
    trainer_id: int,
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: View all workout plans published by a specific trainer.
    """
    stmt = (
        select(WorkoutPlan)
        .options(selectinload(WorkoutPlan.exercises))
        .where(
            WorkoutPlan.trainer_id == trainer_id,
            WorkoutPlan.client_id.is_(None)
        )
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{plan_id}/follow")
async def follow_workout_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Save/Follow a workout plan to access it later.
    """
    # 1. Fetch the user and eagerly load their currently saved plans
    user_result = await db.execute(select(User).options(selectinload(User.saved_plans)).where(User.id == member_id))
    user = user_result.scalars().first()

    # 2. Check if the plan exists
    plan_result = await db.execute(select(WorkoutPlan).where(WorkoutPlan.id == plan_id))
    plan = plan_result.scalars().first()

    if not plan:
        raise HTTPException(status_code=404, detail="Workout plan not found.")

    # 3. Check if already following to prevent duplicates
    if any(p.id == plan_id for p in user.saved_plans):
        raise HTTPException(status_code=400, detail="You are already following this plan.")

    # 4. Add the plan to user's saved plans
    user.saved_plans.append(plan)
    db.add(user)
    await db.commit()

    return {"status": "success", "message": f"You are now following '{plan.name}'"}


@router.delete("/{plan_id}/follow")
async def unfollow_workout_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Remove a saved plan from "My Plans".

    This only detaches the plan from THIS member. The plan itself belongs to the trainer
    who wrote it and stays in the marketplace for everybody else.
    """
    # 1. Fetch the user together with what they currently follow
    user_result = await db.execute(
        select(User).options(selectinload(User.saved_plans)).where(User.id == member_id)
    )
    user = user_result.scalars().first()

    # 2. Find the plan inside their saved list
    saved_plan = next((p for p in user.saved_plans if p.id == plan_id), None)

    if not saved_plan:
        raise HTTPException(status_code=400, detail="This plan is not in your saved plans.")

    # 3. Detach it
    user.saved_plans.remove(saved_plan)
    db.add(user)
    await db.commit()

    return {"status": "success", "message": f"Removed '{saved_plan.name}' from your plans"}


async def _get_own_assigned_plan(plan_id: int, member_id: int, db: AsyncSession) -> WorkoutPlan:
    """
    Shared guard for the two dismiss routes: the plan has to exist AND be the one this
    trainer assigned to this member. Hiding somebody else's plan is meaningless, and
    letting it through would leak whether an arbitrary plan id exists.
    """
    result = await db.execute(select(WorkoutPlan).where(WorkoutPlan.id == plan_id))
    plan = result.scalars().first()

    if not plan:
        raise HTTPException(status_code=404, detail="Workout plan not found.")

    if plan.client_id != member_id:
        raise HTTPException(status_code=403, detail="This plan was not assigned to you.")

    return plan


@router.post("/{plan_id}/dismiss")
async def dismiss_assigned_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Hide a plan a trainer assigned to you.

    A member cannot delete their trainer's work, so instead of touching the plan we note
    that this member does not want to see it. The trainer keeps their copy untouched.
    """
    await _get_own_assigned_plan(plan_id, member_id, db)

    # Idempotent on purpose: a double tap must not blow up on the primary key.
    already_hidden = await db.execute(
        select(user_dismissed_plans).where(
            user_dismissed_plans.c.user_id == member_id,
            user_dismissed_plans.c.plan_id == plan_id
        )
    )

    if not already_hidden.first():
        await db.execute(insert(user_dismissed_plans).values(user_id=member_id, plan_id=plan_id))
        await db.commit()

    return {"status": "success", "message": "Plan hidden from your workout center"}


@router.delete("/{plan_id}/dismiss")
async def restore_assigned_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Undo a dismissal and bring the assigned plan back.
    """
    await _get_own_assigned_plan(plan_id, member_id, db)

    await db.execute(
        delete(user_dismissed_plans).where(
            user_dismissed_plans.c.user_id == member_id,
            user_dismissed_plans.c.plan_id == plan_id
        )
    )
    await db.commit()

    return {"status": "success", "message": "Plan restored to your workout center"}


@router.get("/my-plans", response_model=List[WorkoutPlanResponse])
async def get_my_saved_plans(
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Retrieve all workout plans the user has saved/followed.
    """
    # Fetch the user with their saved plans and the exercises inside those plans
    stmt = (
        select(User)
        .options(
            selectinload(User.saved_plans)
            .selectinload(WorkoutPlan.exercises) # Nested eager loading!
        )
        .where(User.id == member_id)
    )
    result = await db.execute(stmt)
    user = result.scalars().first()

    return user.saved_plans


@router.get("/my-private-plans", response_model=List[WorkoutPlanResponse])
async def get_my_private_plans(
    db: AsyncSession = Depends(get_db),
    member_id: int = Depends(get_current_member)
):
    """
    Member App: Retrieve customized private plans created specifically for this user by their trainer.
    """
    # Anything the member dismissed drops out here, which is the only place the hiding
    # actually takes effect - the plan itself is never modified.
    stmt = (
        select(WorkoutPlan)
        .options(selectinload(WorkoutPlan.exercises))
        .where(
            WorkoutPlan.client_id == member_id,
            ~WorkoutPlan.id.in_(
                select(user_dismissed_plans.c.plan_id)
                .where(user_dismissed_plans.c.user_id == member_id)
            )
        )
        .order_by(WorkoutPlan.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ==========================================
# WORKOUT LOGGING (PROGRESS TRACKING)
# ==========================================

@router.post("/log-session", response_model=WorkoutSessionResponse)
async def log_workout_session(
        payload: WorkoutSessionCreate,
        db: AsyncSession = Depends(get_db),
        member_id: int = Depends(get_current_member)
):
    """
    Member App: Log a completed workout session, one entry per set that was actually performed.
    """
    # 1. Create the parent session
    new_session = WorkoutSession(
        user_id=member_id,
        plan_id=payload.plan_id,
        notes=payload.notes
    )
    db.add(new_session)
    await db.flush()  # Flush to get the new_session.id

    # 2. Add one row per completed set (the client sends a flat list of sets)
    for log in payload.exercises:
        new_log = ExerciseLog(
            session_id=new_session.id,
            exercise_id=log.exercise_id,
            set_number=log.set_number,
            reps_completed=log.reps_completed,
            weight_kg=log.weight_kg
        )
        db.add(new_log)

    await db.commit()

    # 3. Reload from DB to attach nested relationships for the response
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.exercise_logs)
            .selectinload(ExerciseLog.exercise)
        )
        .where(WorkoutSession.id == new_session.id)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


@router.get("/history", response_model=List[WorkoutSessionResponse])
async def get_workout_history(
        skip: int = 0,
        limit: int = 10,
        db: AsyncSession = Depends(get_db),
        member_id: int = Depends(get_current_member)
):
    """
    Member App: Fetch the user's workout history (useful for plotting progress charts).
    """
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.exercise_logs)
            .selectinload(ExerciseLog.exercise)
        )
        .where(WorkoutSession.user_id == member_id)
        .order_by(WorkoutSession.date.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()