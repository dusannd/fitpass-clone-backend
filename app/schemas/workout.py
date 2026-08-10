from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime


# --- 1. EXERCISE SCHEMAS ---
class ExerciseCreate(BaseModel):
    name: str
    sets: int = 3
    reps: str
    rest_time_seconds: Optional[int] = 60
    requires_weight: bool = True

    # Trainer setup: target weight, the increment of that specific machine, and form cues.
    recommended_weight_kg: Optional[float] = None
    weight_step_kg: float = 2.5
    instructions: Optional[str] = None


class ExerciseResponse(BaseModel):
    id: int
    name: str
    sets: int
    reps: str
    rest_time_seconds: Optional[int]
    requires_weight: bool
    recommended_weight_kg: Optional[float] = None
    weight_step_kg: float = 2.5
    instructions: Optional[str] = None

    class Config:
        from_attributes = True


# --- 2. WORKOUT PLAN SCHEMAS ---
class WorkoutPlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    client_id: Optional[int] = None
    exercises: List[ExerciseCreate] = []


class WorkoutPlanResponse(BaseModel):
    id: int
    trainer_id: int
    client_id: Optional[int] = None
    name: str
    description: Optional[str]
    created_at: datetime
    exercises: List[ExerciseResponse] = []

    model_config = ConfigDict(from_attributes=True)


# --- 3. WORKOUT LOGGING SCHEMAS (PROGRESS TRACKING) ---
class ExerciseLogCreate(BaseModel):
    """One single set. A 3 set exercise sends 3 of these."""
    exercise_id: int
    set_number: int = 1
    reps_completed: str
    weight_kg: Optional[float] = None


class ExerciseLogResponse(BaseModel):
    id: int
    exercise_id: Optional[int]
    set_number: int
    reps_completed: str
    weight_kg: Optional[float]

    # We include basic exercise info so frontend can display the name
    exercise: Optional[ExerciseResponse] = None

    class Config:
        from_attributes = True


class WorkoutSessionCreate(BaseModel):
    plan_id: int
    notes: Optional[str] = None
    exercises: List[ExerciseLogCreate] = []


class WorkoutSessionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: Optional[int]
    date: datetime
    notes: Optional[str]
    exercise_logs: List[ExerciseLogResponse] = []

    class Config:
        from_attributes = True