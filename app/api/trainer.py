from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List

from app.core.database import get_db
from app.api.dependencies import RequireRole
from app.models.workout import WorkoutPlan, Exercise
from app.schemas.workout import WorkoutPlanCreate, WorkoutPlanResponse

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
    Trainer Dashboard: Create a new workout plan along with its exercises.
    """
    # 1. Create the parent Workout Plan object
    new_plan = WorkoutPlan(
        trainer_id=trainer_id,
        name=plan.name,
        description=plan.description
    )

    db.add(new_plan)
    # Flush sends the data to the database to get the ID, but doesn't commit the transaction yet
    await db.flush()

    # 2. Loop through the exercises provided in the request and link them to the new plan
    for ex in plan.exercises:
        new_exercise = Exercise(
            plan_id=new_plan.id,
            name=ex.name,
            sets=ex.sets,
            reps=ex.reps,
            rest_time_seconds=ex.rest_time_seconds
        )
        db.add(new_exercise)

    # 3. Commit everything together (Atomicity: If one exercise fails, the whole plan fails)
    await db.commit()

    # 4. Reload the plan from the database so SQLAlchemy attaches the 'exercises' list for the response
    stmt = (
        select(WorkoutPlan)
        .options(selectinload(WorkoutPlan.exercises))
        .where(WorkoutPlan.id == new_plan.id)
    )
    result = await db.execute(stmt)
    created_plan = result.scalars().first()

    return created_plan


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