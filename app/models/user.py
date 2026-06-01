from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base
from sqlalchemy.orm import relationship


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    # Gym specific fields
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)  # Can enter the gym?
    role = Column(String, default="member")  # Roles: member, admin, trainer

    # Audit timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship to user subscriptions
    subscriptions = relationship("UserSubscription", backref="user")