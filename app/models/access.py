from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class EntryLog(Base):
    """
    Logs every attempt to enter or exit the gym.
    Implements Anti-Passback tracking via the 'action_type' column.
    """
    __tablename__ = "entry_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    worker_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    location_id = Column(Integer, ForeignKey("gym_locations.id", ondelete="SET NULL"), nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    access_granted = Column(Boolean, nullable=False)

    # NEW: Defines if this was an ENTRY attempt or an EXIT attempt
    action_type = Column(String, default="ENTRY", nullable=False)

    reason = Column(String, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="entry_logs")
    worker = relationship("User", foreign_keys=[worker_id])
    location = relationship("GymLocation")