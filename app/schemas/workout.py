from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


# --- 1. EXERCISE SCHEMAS ---
class ExerciseCreate(BaseModel):
    name: str
    sets: int = 3
    reps: str
    rest_time_seconds: Optional[int] = 60


class ExerciseResponse(BaseModel):
    id: int
    name: str
    sets: int
    reps: str
    rest_time_seconds: Optional[int]

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

    class Config:
        from_attributes = True


# --- 3. WORKOUT LOGGING SCHEMAS (PROGRESS TRACKING) ---
class ExerciseLogCreate(BaseModel):
    exercise_id: int
    sets_completed: int
    reps_completed: str
    weight_kg: Optional[float] = None


class ExerciseLogResponse(BaseModel):
    id: int
    exercise_id: Optional[int]
    sets_completed: int
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