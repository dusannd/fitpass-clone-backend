from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

# --- EXERCISE SCHEMAS ---
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


# --- WORKOUT PLAN SCHEMAS ---
class WorkoutPlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    # If provided, this plan becomes private for this specific client
    client_id: Optional[int] = None
    # A trainer can submit a list of exercises right when creating the plan
    exercises: List[ExerciseCreate] = []

class WorkoutPlanResponse(BaseModel):
    id: int
    trainer_id: int
    # Let the frontend know if this is a private plan
    client_id: Optional[int] = None
    name: str
    description: Optional[str]
    created_at: datetime
    # We will return the list of exercises inside the plan
    exercises: List[ExerciseResponse] = []

    class Config:
        from_attributes = True