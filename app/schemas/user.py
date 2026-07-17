from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any

from app.schemas.subscription import UserSubscriptionResponse

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
    is_verified: bool
    
    # We replaced 'role: str' with a list of roles!
    roles: List[RoleResponse] = []

    # Admin frontend needs to see active subscriptions easily
    subscriptions: List[UserSubscriptionResponse] = []

    class Config:
        from_attributes = True


# --- SCHEMA FOR LOGIN ---
class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str

# --- HR PANEL SCHEMAS ---
class RoleManageRequest(BaseModel):
    """
    Schema for assigning or revoking roles via the Admin HR panel.
    """
    email: EmailStr
    role_name: str


# --- PASSWORD RESET SCHEMAS ---
class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str