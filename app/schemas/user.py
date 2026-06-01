from pydantic import BaseModel, EmailStr
from typing import Optional

# What we expect from the client when creating a user
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str

# What we return to the client (NOTICE: NO PASSWORD HERE!)
class UserResponse(BaseModel):
    id: int
    email: EmailStr
    first_name: Optional[str]
    last_name: Optional[str]
    is_active: bool
    role: str

    # This tells Pydantic to read data even if it's a SQLAlchemy model
    class Config:
        from_attributes = True


# Schema for the login request body
class UserLogin(BaseModel):
    email: EmailStr
    password: str

# Schema for the login response (the token)
class Token(BaseModel):
    access_token: str
    token_type: str