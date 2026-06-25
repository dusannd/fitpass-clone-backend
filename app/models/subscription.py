from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Time, Table, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


# --- 1. GYM LOCATIONS MODEL ---
# Represents physical gym buildings (e.g., "Downtown Gym", "Uptown 24/7")
class GymLocation(Base):
    __tablename__ = "gym_locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    address = Column(String, nullable=True)
    is_24_7 = Column(Boolean, default=True)


# --- 2. MANY-TO-MANY ASSOCIATION TABLE ---
# Connects Subscription Plans to Gym Locations (Which plan allows access to which gyms)
plan_locations = Table(
    "plan_locations",
    Base.metadata,
    Column("plan_id", Integer, ForeignKey("subscription_plans.id", ondelete="CASCADE"), primary_key=True),
    Column("location_id", Integer, ForeignKey("gym_locations.id", ondelete="CASCADE"), primary_key=True),
)


# --- 3. SUBSCRIPTION PLAN MODEL ---
# The base package a user buys (e.g., "Student Pack", "Gold VIP")
class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, nullable=False)
    duration_days = Column(Integer, default=30)

    # Relationships
    user_subscriptions = relationship("UserSubscription", back_populates="plan")

    # One-to-One relationship: A plan can have ONE set of rules (time/days limits)
    rule = relationship("SubscriptionRule", back_populates="plan", uselist=False, cascade="all, delete-orphan")

    # Many-to-Many relationship: A plan can grant access to MULTIPLE locations
    locations = relationship("GymLocation", secondary=plan_locations, lazy="selectin")


# --- 4. SUBSCRIPTION RULE MODEL ---
# Dynamic rules for plans. If these are NULL, the plan works 24/7 every day.
class SubscriptionRule(Base):
    __tablename__ = "subscription_rules"

    id = Column(Integer, primary_key=True, index=True)
    # Linked directly to a specific plan
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Time limits (e.g., 09:00:00 to 16:00:00)
    allowed_time_start = Column(Time, nullable=True)
    allowed_time_end = Column(Time, nullable=True)

    # Comma-separated string of allowed days (0=Monday, 6=Sunday). E.g., "0,1,2,3,4"
    allowed_days = Column(String, nullable=True)

    # Relationship back to the plan
    plan = relationship("SubscriptionPlan", back_populates="rule")


# --- 5. USER SUBSCRIPTION MODEL ---
# The actual active subscription belonging to a specific user
class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="CASCADE"), nullable=False)

    start_date = Column(DateTime(timezone=True), server_default=func.now())
    end_date = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Integer, default=1)  # 1 = Active, 0 = Expired

    # Relationship to fetch plan details easily
    plan = relationship("SubscriptionPlan", back_populates="user_subscriptions")