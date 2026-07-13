from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Table
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


# Association table for members following a workout plan
user_saved_plans = Table(
    "user_saved_plans",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("plan_id", Integer, ForeignKey("workout_plans.id", ondelete="CASCADE"), primary_key=True),
)

class WorkoutPlan(Base):
    """
    Represents a workout program created by a user with the 'trainer' role.
    """
    __tablename__ = "workout_plans"

    id = Column(Integer, primary_key=True, index=True)

    # Foreign key linking to the User (Trainer) who created this plan
    trainer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # NEW: If NULL, the plan is public. If an ID is present, it's a private plan assigned to a specific client.
    client_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    name = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    # A plan can have multiple exercises.
    exercises = relationship("Exercise", back_populates="plan", cascade="all, delete-orphan")

    # FIX: Explicitly tell SQLAlchemy to use 'trainer_id' using string references
    trainer = relationship("User", foreign_keys="WorkoutPlan.trainer_id", back_populates="created_plans")

    # NEW: Relationship for the client assigned to this private plan
    client = relationship("User", foreign_keys="WorkoutPlan.client_id")

    # List of regular users (members) who saved/followed this plan
    saved_by_users = relationship("User", secondary=user_saved_plans, back_populates="saved_plans")


class Exercise(Base):
    """
    A specific exercise that belongs to a WorkoutPlan.
    """
    __tablename__ = "exercises"

    id = Column(Integer, primary_key=True, index=True)

    # Foreign key linking to the WorkoutPlan
    plan_id = Column(Integer, ForeignKey("workout_plans.id", ondelete="CASCADE"), nullable=False)

    name = Column(String, nullable=False)
    sets = Column(Integer, default=3)

    # Stored as string because reps can be "8-12", "15", or "Till failure"
    reps = Column(String, nullable=False)
    rest_time_seconds = Column(Integer, default=60, nullable=True)

    # Relationship back to the parent plan
    plan = relationship("WorkoutPlan", back_populates="exercises")