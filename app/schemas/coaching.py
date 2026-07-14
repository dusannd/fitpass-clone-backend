from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class CoachingUser(BaseModel):
    """
    A lightweight schema for representing a user (Trainer or Client)
    in coaching requests. It purposefully excludes heavy nested relationships
    like 'subscriptions' or 'roles' to optimize database performance.
    """
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: str

    class Config:
        from_attributes = True



class CoachingRequestUpdate(BaseModel):
    """
    Schema for a trainer to accept or reject a client's request.
    Expected values: 'ACCEPTED' or 'REJECTED'
    """
    status: str


class TrainerClientLinkResponse(BaseModel):
    """
    Schema for returning the connection details between a trainer and a client.
    """
    id: int
    trainer_id: int
    client_id: int
    status: str
    created_at: datetime

    # Koristimo našu laganu šemu umesto teškog UserResponse
    client: Optional[CoachingUser] = None
    trainer: Optional[CoachingUser] = None

    class Config:
        from_attributes = True


# --- APPOINTMENT SCHEMAS ---

class AppointmentCreate(BaseModel):
    """
    Schema for a client to request a new training session.
    """
    trainer_id: int
    start_time: datetime
    end_time: datetime

class AppointmentUpdate(BaseModel):
    """
    Schema for a trainer to update the status of an appointment.
    """
    status: str  # Expected: 'COMPLETED' or 'CANCELLED'
    notes: Optional[str] = None

class AppointmentResponse(BaseModel):
    """
    Schema for returning appointment details.
    """
    id: int
    trainer_id: int
    client_id: int
    start_time: datetime
    end_time: datetime
    status: str
    notes: Optional[str]

    # Include lightweight info so the frontend knows who the appointment is with
    trainer: Optional[CoachingUser] = None
    client: Optional[CoachingUser] = None

    class Config:
        from_attributes = True