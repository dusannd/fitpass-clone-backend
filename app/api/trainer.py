from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List

from app.core.database import get_db
from app.api.dependencies import RequireRole
from app.models.workout import WorkoutPlan, Exercise
from app.schemas.workout import WorkoutPlanCreate, WorkoutPlanResponse
from app.models.coaching import TrainerClientLink
router = APIRouter()

# Bouncer: Only users with the 'trainer' role can access these endpoints
get_current_trainer = RequireRole("trainer")


@router.post("/plans", response_model=WorkoutPlanResponse)
async def create_workout_plan(
        plan: WorkoutPlanCreate,
        db: AsyncSession = Depends(get_db),
        trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Dashboard: Create a new workout plan (Public or Private).
    """
    # 1. SECURITY CHECK: If this is a private plan, ensure the user is actually their client!
    if plan.client_id:
        stmt_check = select(TrainerClientLink).where(
            TrainerClientLink.trainer_id == trainer_id,
            TrainerClientLink.client_id == plan.client_id,
            TrainerClientLink.status == "ACCEPTED"
        )
        result_check = await db.execute(stmt_check)
        if not result_check.scalars().first():
            raise HTTPException(
                status_code=403,
                detail="You can only assign private plans to your officially accepted clients."
            )

    # 2. Create the parent Workout Plan object
    new_plan = WorkoutPlan(
        trainer_id=trainer_id,
        client_id=plan.client_id,
        name=plan.name,
        description=plan.description
    )

    db.add(new_plan)
    await db.flush()

    # 3. Add Exercises
    for ex in plan.exercises:
        new_exercise = Exercise(
            plan_id=new_plan.id,
            name=ex.name,
            sets=ex.sets,
            reps=ex.reps,
            rest_time_seconds=ex.rest_time_seconds
        )
        db.add(new_exercise)

    await db.commit()

    # 4. Reload to fetch nested exercises
    stmt = (
        select(WorkoutPlan)
        .options(selectinload(WorkoutPlan.exercises))
        .where(WorkoutPlan.id == new_plan.id)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


@router.get("/plans", response_model=List[WorkoutPlanResponse])
async def get_my_workout_plans(
        db: AsyncSession = Depends(get_db),
        trainer_id: int = Depends(get_current_trainer)
):
    """
    Trainer Dashboard: Returns a list of all workout plans created by this specific trainer.
    """
    stmt = (
        select(WorkoutPlan)
        .options(selectinload(WorkoutPlan.exercises))
        .where(WorkoutPlan.trainer_id == trainer_id)
        .order_by(WorkoutPlan.created_at.desc())
    )

    result = await db.execute(stmt)
    return result.scalars().all()