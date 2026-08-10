from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Table, Float, Boolean, Index
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

# A member can hide a plan their trainer assigned to them once they are done with it.
# We record the dismissal instead of deleting the plan: the plan belongs to the trainer,
# who keeps their copy either way, and hiding stays reversible for the member.
user_dismissed_plans = Table(
    "user_dismissed_plans",
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
    requires_weight = Column(Boolean, default=True, nullable=False)

    # --- TRAINER SETUP (the client should never have to calculate anything) ---
    # 1. The weight the trainer wants the client to start from. The app pre-fills it.
    recommended_weight_kg = Column(Float, nullable=True)
    # 2. How much one press of the "+" button adds. Every machine is different:
    #    free weights go by 2.5 kg, cable stacks by 4.5 or 6.8 kg, drop-pins by 2.25 kg.
    weight_step_kg = Column(Float, default=2.5, server_default="2.5", nullable=False)
    # 3. Form cues shown at the top of the exercise card (e.g. "3 sec negatives").
    instructions = Column(String, nullable=True)

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
    # order_by keeps the sets in the order they were performed, so the history
    # screen never has to sort them again.
    exercise_logs = relationship(
        "ExerciseLog",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ExerciseLog.exercise_id, ExerciseLog.set_number",
    )


class ExerciseLog(Base):
    """
    Represents the actual performance of ONE SINGLE SET of an exercise.

    A 3 set bench press produces 3 rows, not 1. That is what lets the app show a
    real set by set history ("60kg x 10, 60kg x 8, 55kg x 8") instead of a single
    averaged number, and it is why the personal record is the maximum weight_kg
    across all the rows of one exercise inside one session.
    """
    __tablename__ = "exercise_logs"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False)
    exercise_id = Column(Integer, ForeignKey("exercises.id", ondelete="SET NULL"), nullable=True)

    # Which set this row is, counting from 1.
    set_number = Column(Integer, nullable=False, server_default="1")
    reps_completed = Column(String, nullable=False)
    weight_kg = Column(Float, nullable=True)

    # Relationships
    session = relationship("WorkoutSession", back_populates="exercise_logs")
    exercise = relationship("Exercise")

    # Every read groups the rows by (session, exercise), and we now write roughly
    # three times as many rows, so give that pair its own index.
    __table_args__ = (
        Index("ix_exercise_logs_session_exercise", "session_id", "exercise_id"),
    )