from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class EntryLog(Base):
    """
    Logs every attempt to enter the gym (successful or failed).
    Great for analytics (crowd heatmaps, average gym time, etc.).
    """
    __tablename__ = "entry_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    access_granted = Column(Boolean, nullable=False)  # True if door opened, False if rejected
    reason = Column(String, nullable=True)  # E.g., "Subscription expired", "Success"

    # Relationship
    user = relationship("User", backref="entry_logs")