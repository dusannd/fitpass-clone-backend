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