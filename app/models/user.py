from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Table
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base

# 1. ASSOCIATION TABLE
# This table connects Users and Roles (Many-to-Many relationship)
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


# 2. ROLE MODEL
# This table stores all available roles in the system (e.g., admin, worker, member)
class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)


# 3. USER MODEL
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # Gym specific fields
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)

    # We REMOVED the old 'role' string column here!

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # RELATIONSHIPS
    # The cascade is NOT optional here, and its absence used to make deleting any
    # subscribed user fail outright. Without it SQLAlchemy's default is to
    # DE-ASSOCIATE the children instead of removing them - it emits
    # `UPDATE user_subscriptions SET user_id = NULL`, which the NOT NULL column
    # rejects, so the whole DELETE came back as a 500. The ON DELETE CASCADE on the
    # foreign key never got a chance, because the ORM nullifies before the database
    # is ever asked. Deleting via the ORM (rather than passive_deletes=True) also
    # keeps SQLite and Postgres behaving identically: SQLite does not enforce
    # foreign keys unless the PRAGMA is on, so leaning on the database here would
    # make the tests prove something different from what production does.
    subscriptions = relationship("UserSubscription", backref="user", cascade="all, delete-orphan")
    # Connects User to Role via the user_roles table
    roles = relationship("Role", secondary=user_roles, lazy="selectin")
    # Coaching relationships (Trainer-Client links)
    clients_linked = relationship("TrainerClientLink", foreign_keys="TrainerClientLink.trainer_id",
                                  back_populates="trainer", cascade="all, delete-orphan")
    trainers_linked = relationship("TrainerClientLink", foreign_keys="TrainerClientLink.client_id",
                                   back_populates="client", cascade="all, delete-orphan")

    # Connects User (Trainer) to the workout plans they create
    # FIX: Added foreign_keys to resolve ambiguity with private client plans
    created_plans = relationship("WorkoutPlan", foreign_keys="WorkoutPlan.trainer_id", back_populates="trainer",
                                 cascade="all, delete-orphan")
    # Connects a standard User (Member) to the workout plans they are following
    saved_plans = relationship("WorkoutPlan", secondary="user_saved_plans", back_populates="saved_by_users",
                               lazy="selectin")
    # NEW: Historical logs of completed workout sessions by the user
    workout_sessions = relationship("WorkoutSession", back_populates="user", cascade="all, delete-orphan")

    # NEW: One-to-One profile (bio, goals, avatar).
    # Same lazy="selectin" trick as roles above, so the profile is always loaded
    # in one extra batched query instead of N+1 (and never blows up on async).
    profile = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# 4. USER PROFILE MODEL
# Kept in its own table so login/auth queries stay light and the bio text
# doesn't get dragged along on every single user lookup.
class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)

    # unique=True is what makes this One-to-One instead of One-to-Many
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Trainers sell themselves here, members describe themselves
    bio = Column(Text, nullable=True)
    # Comma separated ("Lose weight, Build muscle").
    # For trainers the frontend shows this same field as "Specialties".
    fitness_goals = Column(String, nullable=True)
    profile_picture_url = Column(String, nullable=True)

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="profile")
