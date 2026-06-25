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

    # NOVO: Ako je radnik ručno pustio korisnika (bez QR koda), ovde piše ID tog radnika.
    # Ako je korisnik ušao sam preko QR skenera, ovo ostaje NULL.
    worker_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    access_granted = Column(Boolean, nullable=False)  # True if door opened, False if rejected
    reason = Column(String, nullable=True)  # E.g., "Subscription expired", "Success", "Manual Override"

    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="entry_logs")
    worker = relationship("User", foreign_keys=[worker_id])