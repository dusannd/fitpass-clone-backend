from pydantic import BaseModel, EmailStr
from typing import Optional, List


# --- SCHEMA FOR ROLES ---
class RoleResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


# --- SCHEMA FOR USERS ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    first_name: Optional[str]
    last_name: Optional[str]
    is_active: bool

    # We replaced 'role: str' with a list of roles!
    roles: List[RoleResponse] = []

    class Config:
        from_attributes = True


# --- SCHEMA FOR LOGIN ---
class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str