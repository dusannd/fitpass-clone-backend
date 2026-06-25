from pydantic import BaseModel
from datetime import datetime, time
from typing import Optional, List


# --- 1. GYM LOCATIONS ---
class GymLocationCreate(BaseModel):
    name: str
    address: Optional[str] = None
    is_24_7: bool = True


class GymLocationResponse(BaseModel):
    id: int
    name: str
    address: Optional[str]
    is_24_7: bool

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


# --- 3. SUBSCRIPTION PLANS ---
class PlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    duration_days: int = 30

    # NOVO: Admin šalje listu ID-jeva teretana u koje ovaj paket može da uđe
    location_ids: List[int] = []
    # NOVO: Opciono pravilo (ako ga nema, paket važi 24/7)
    rule: Optional[RuleCreate] = None


class PlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    duration_days: int
    locations: List[GymLocationResponse] = []
    rule: Optional[RuleResponse] = None

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True