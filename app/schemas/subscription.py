from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime, time
from typing import Optional, List, Literal


# --- 0. PLAN TIERS ---
# The visual/marketing tier of a plan. A Literal gives us a 422 on a bad value for
# free, so no custom validator is needed on the way in.
PlanTier = Literal["Standard", "Pro", "VIP"]


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
    tier: PlanTier = "Standard"
    location_ids: List[int] = []
    rule: Optional[RuleCreate] = None


class PlanUpdate(BaseModel):
    # Every field is optional because this is a partial update: an omitted key means
    # "leave this alone". None as the default is only a marker for "not supplied" -
    # see the validator below for why an *explicitly* sent null is a different thing.
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(None, ge=0, description="Price must be 0 or greater")
    duration_days: Optional[int] = Field(None, gt=0, description="Duration must be at least 1 day")
    tier: Optional[PlanTier] = None

    @field_validator("name", "price", "duration_days", "tier")
    @classmethod
    def reject_explicit_null(cls, v):
        """
        Refuse an explicitly sent null on the fields that cannot hold one.

        name, price and tier are NOT NULL columns, so a null would travel through
        model_dump(exclude_unset=True) into setattr and blow up as an unhandled
        IntegrityError - a 500 for what is really a bad request.

        duration_days is worse: its column IS nullable, so the write succeeds, but
        PlanResponse.duration_days is a plain int, which means every later read of
        that plan fails response validation. One bad request would poison the row.

        Omitting a field is how you leave it unchanged, so a null here is always a
        client mistake and deserves a 422.

        description is deliberately NOT in this list: its column is nullable and the
        admin form sends `description: null` to clear it.

        Pydantic does not run validators on defaults, so an omitted field never
        reaches this and exclude_unset keeps working untouched.
        """
        if v is None:
            raise ValueError("cannot be null - omit this field to leave it unchanged")
        return v


class PlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    duration_days: int
    is_active: bool
    # Typed as a plain str, not PlanTier, so a row holding an unexpected value still
    # serializes instead of 500-ing. The frontend falls back to the Standard theme.
    tier: str
    locations: List[GymLocationResponse] = []
    rule: Optional[RuleResponse] = None

    # Handle legacy database rows where is_active might be NULL before the migration
    @field_validator('is_active', mode='before')
    @classmethod
    def handle_null_is_active(cls, v):
        return True if v is None else v

    # Same reasoning for tier: rows written before the migration have no value.
    @field_validator('tier', mode='before')
    @classmethod
    def handle_null_tier(cls, v):
        return "Standard" if v is None else v

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