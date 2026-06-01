from pydantic import BaseModel
from datetime import datetime
from typing import Optional

# ---- For Subscription Plans (Admin creates these) ----
class PlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    duration_days: int = 30

class PlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    duration_days: int

    class Config:
        from_attributes = True

# ---- For User Subscriptions (When user buys a plan) ----
class UserSubscriptionCreate(BaseModel):
    plan_id: int
    # We don't need user_id here, because we will get it from the JWT Token!

class UserSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    start_date: datetime
    end_date: datetime
    is_active: int

    class Config:
        from_attributes = True