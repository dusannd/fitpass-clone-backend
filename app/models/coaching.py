from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
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

    # Relationships
    trainer = relationship("User", foreign_keys=[trainer_id], back_populates="clients_linked")
    client = relationship("User", foreign_keys=[client_id], back_populates="trainers_linked")


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