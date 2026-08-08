from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime, time
from typing import Optional, List


# --- 1. GYM LOCATIONS ---
class GymLocationCreate(BaseModel):
    name: str
    address: Optional[str] = None
    is_24_7: bool = True


class GymLocationUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    is_24_7: Optional[bool] = None


class GymLocationResponse(BaseModel):
    id: int
    name: str
    address: Optional[str]
    is_24_7: bool

    # Handle legacy database rows where is_24_7 might be NULL
    @field_validator('is_24_7', mode='before')
    @classmethod
    def handle_null_is_24_7(cls, v):
        return True if v is None else v

    model_config = ConfigDict(from_attributes=True)


# --- 2. SUBSCRIPTION RULES ---
class RuleCreate(BaseModel):
    allowed_time_start: Optional[time] = None
    allowed_time_end: Optional[time] = None
    allowed_days: Optional[str] = None  # e.g., "0,1,2,3,4"


class RuleResponse(BaseModel):
    id: int
    allowed_time_start: Optional[time]
    allowed_time_end: Optional[time]
    allowed_days: Optional[str]

    model_config = ConfigDict(from_attributes=True)


# --- 3. SUBSCRIPTION PLANS ---
class PlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float = Field(..., ge=0, description="Price must be 0 or greater")
    duration_days: int = Field(default=30, gt=0, description="Duration must be at least 1 day")
    location_ids: List[int] = []
    rule: Optional[RuleCreate] = None


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(None, ge=0, description="Price must be 0 or greater")
    duration_days: Optional[int] = Field(None, gt=0, description="Duration must be at least 1 day")


class PlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    duration_days: int
    is_active: bool
    locations: List[GymLocationResponse] = []
    rule: Optional[RuleResponse] = None

    # Handle legacy database rows where is_active might be NULL before the migration
    @field_validator('is_active', mode='before')
    @classmethod
    def handle_null_is_active(cls, v):
        return True if v is None else v

    model_config = ConfigDict(from_attributes=True)


# --- 4. USER SUBSCRIPTIONS ---
class UserSubscriptionCreate(BaseModel):
    plan_id: int


class UserSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    start_date: datetime
    end_date: datetime
    is_active: int

    model_config = ConfigDict(from_attributes=True)


# --- 5. "MY SUBSCRIPTION" (used by GET /my-subscription) ---
class MySubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    start_date: datetime
    end_date: datetime
    is_active: int
    plan: PlanResponse

    model_config = ConfigDict(from_attributes=True)