from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Table
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

    # We REMOVED the old 'role' string column here!

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # RELATIONSHIPS
    subscriptions = relationship("UserSubscription", backref="user")

    # NEW RELATIONSHIP: Connects User to Role via the user_roles table
    roles = relationship("Role", secondary=user_roles, lazy="selectin")