from pydantic import BaseModel, EmailStr, ConfigDict, Field
from typing import Optional, List, Any

from app.schemas.subscription import UserSubscriptionResponse

# --- SCHEMA FOR ROLES ---
class RoleResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


# --- SCHEMAS FOR THE USER PROFILE ---
class UserProfileBase(BaseModel):
    """
    The text fields a user types in themselves.
    """
    # Max lengths so nobody can dump a novel into our database
    bio: Optional[str] = Field(default=None, max_length=2000)
    fitness_goals: Optional[str] = Field(default=None, max_length=255)


class UserProfileUpdate(UserProfileBase):
    """
    Schema for PUT /api/users/me/profile.
    Everything is optional: fields you don't send stay as they are,
    fields you send as null get cleared.

    Note: profile_picture_url is NOT here on purpose. The picture is only ever
    set by POST /me/avatar, so the client can't point it at a random path
    and leave us with orphaned files on disk.
    """
    pass


class UserProfileResponse(UserProfileBase):
    id: int
    user_id: int
    # Read only for the client, we fill this in from the upload endpoint
    profile_picture_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# --- SCHEMA FOR USERS ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    # HONEYPOT FIELD: Real users will send None. Bots will fill this out.
    extra_info: Optional[str] = None
    # NEW: reCAPTCHA token from the frontend
    recaptcha_token: Optional[str] = None
    # NEW: Optional profile data (bio / goals) sent from the register form
    profile: Optional[UserProfileBase] = None


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    first_name: Optional[str]
    last_name: Optional[str]
    is_active: bool
    is_verified: Optional[bool] = False
    # We replaced 'role: str' with a list of roles!
    roles: List[RoleResponse] = []

    # Admin frontend needs to see active subscriptions easily
    subscriptions: List[UserSubscriptionResponse] = []

    # NEW: Null for old accounts, or for users who skipped this on sign-up
    profile: Optional[UserProfileResponse] = None

    model_config = ConfigDict(from_attributes=True)

# --- SCHEMA FOR LOGIN ---
class UserLogin(BaseModel):
    email: EmailStr
    password: str
    # HONEYPOT FIELD: Used to catch bot login brute-force attempts.
    extra_info: Optional[str] = None
    # NEW: reCAPTCHA token from the frontend
    recaptcha_token: Optional[str] = None

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


# --- EMAIL VERIFICATION SCHEMAS ---
class ResendVerificationRequest(BaseModel):
    """
    Schema for requesting a new verification email.
    """
    email: EmailStr