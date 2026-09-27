from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.config import settings
from app.core.database import Base


class TrainerClientLink(Base):
    """
    Links a trainer and a client.
    Status can be: PENDING (Request sent by client), ACCEPTED (Approved by trainer), REJECTED (Denied).
    """
    __tablename__ = "trainer_client_links"

    id = Column(Integer, primary_key=True, index=True)
    trainer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Allowed states: "PENDING", "ACCEPTED", "REJECTED"
    status = Column(String, default="PENDING", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # A rejected member may ask again, but not endlessly: this counts rejections in a
    # row, and once it reaches MAX_CONSECUTIVE_REJECTIONS the member has to wait
    # REJECTION_COOLDOWN_DAYS from the last one. An accept resets it.
    rejection_count = Column(Integer, nullable=False, default=0, server_default="0")
    # When the trainer last answered - the cooldown is counted from here.
    responded_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    trainer = relationship("User", foreign_keys=[trainer_id], back_populates="clients_linked")
    client = relationship("User", foreign_keys=[client_id], back_populates="trainers_linked")

    @property
    def retry_after(self) -> datetime | None:
        """
        When a banned member may ask this trainer again, or None if nothing stops them.

        A property on the model (not a column) so the rule lives in one place and
        TrainerClientLinkResponse can simply read it off the row for the frontend.
        """
        if self.status != "REJECTED" or self.rejection_count < settings.MAX_CONSECUTIVE_REJECTIONS:
            return None
        if self.responded_at is None:
            return None

        # SQLite hands back naive datetimes, Postgres aware ones - stamp UTC first.
        responded = self.responded_at
        if responded.tzinfo is None:
            responded = responded.replace(tzinfo=timezone.utc)

        until = responded + timedelta(days=settings.REJECTION_COOLDOWN_DAYS)
        return until if until > datetime.now(timezone.utc) else None


class Appointment(Base):
    """
    Represents a scheduled 1-on-1 private training session.
    """
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    trainer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)

    # Allowed states: "SCHEDULED", "COMPLETED", "CANCELLED"
    status = Column(String, default="SCHEDULED", nullable=False)

    # Trainer can leave feedback or notes after the session is completed
    notes = Column(String, nullable=True)

    # Relationships
    trainer = relationship("User", foreign_keys=[trainer_id])
    client = relationship("User", foreign_keys=[client_id])