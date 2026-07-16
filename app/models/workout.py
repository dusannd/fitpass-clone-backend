from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Table, Float
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

    trainer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    name = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    exercises = relationship("Exercise", back_populates="plan", cascade="all, delete-orphan")
    trainer = relationship("User", foreign_keys="WorkoutPlan.trainer_id", back_populates="created_plans")
    client = relationship("User", foreign_keys="WorkoutPlan.client_id")
    saved_by_users = relationship("User", secondary=user_saved_plans, back_populates="saved_plans")


class Exercise(Base):
    """
    A specific exercise that belongs to a WorkoutPlan.
    """
    __tablename__ = "exercises"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("workout_plans.id", ondelete="CASCADE"), nullable=False)

    name = Column(String, nullable=False)
    sets = Column(Integer, default=3)
    reps = Column(String, nullable=False)
    rest_time_seconds = Column(Integer, default=60, nullable=True)

    # Relationship back to the parent plan
    plan = relationship("WorkoutPlan", back_populates="exercises")


class WorkoutSession(Base):
    """
    Represents a single instance of a user going to the gym and performing a workout plan.
    """
    __tablename__ = "workout_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("workout_plans.id", ondelete="SET NULL"), nullable=True)

    date = Column(DateTime(timezone=True), server_default=func.now())
    notes = Column(String, nullable=True)

    # Relationships
    user = relationship("User", back_populates="workout_sessions")
    plan = relationship("WorkoutPlan")
    exercise_logs = relationship("ExerciseLog", back_populates="session", cascade="all, delete-orphan")


class ExerciseLog(Base):
    """
    Represents the actual performance (weight, sets, reps) of a specific exercise.
    """
    __tablename__ = "exercise_logs"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False)
    exercise_id = Column(Integer, ForeignKey("exercises.id", ondelete="SET NULL"), nullable=True)

    sets_completed = Column(Integer, nullable=False)
    reps_completed = Column(String, nullable=False)
    weight_kg = Column(Float, nullable=True)

    # Relationships
    session = relationship("WorkoutSession", back_populates="exercise_logs")
    exercise = relationship("Exercise")