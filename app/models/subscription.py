from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class SubscriptionPlan(Base):
    """
    Available gym plans: Standard, Gold, Platinum.
    """
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)  # e.g., "Gold"
    description = Column(String, nullable=True)
    price = Column(Float, nullable=False)
    duration_days = Column(Integer, default=30)

    # Relationships
    user_subscriptions = relationship("UserSubscription", back_populates="plan")


class UserSubscription(Base):
    """
    Records which user bought which plan and when it expires.
    """
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)

    start_date = Column(DateTime(timezone=True), server_default=func.now())
    end_date = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Integer, default=1)  # 1 = Active, 0 = Expired

    # Relationships
    plan = relationship("SubscriptionPlan", back_populates="user_subscriptions")
    # We will add a relationship in the User model later