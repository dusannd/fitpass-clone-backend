from fastapi import APIRouter, Depends, HTTPException, status, Request

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import jwt

from app.core.rate_limit import limiter
# We now import the Role model as well
from app.models.user import User, Role
from app.core.database import get_db
from app.schemas.user import (UserCreate, UserResponse, UserLogin, Token,
PasswordResetRequest, PasswordResetConfirm)
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.config import settings
from app.api.dependencies import RequireRole
from app.services.email import create_action_token, send_verification_email, send_password_reset_email

router = APIRouter()

# BOUNCER
get_current_admin = RequireRole("admin")


@router.post("/", response_model=UserResponse)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    # 1. Check if user with this email already exists
    result = await db.execute(select(User).where(User.email == user.email))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2. Hash the password
    hashed_password = get_password_hash(user.password)

    # 3. Create new user instance
    new_user = User(
        email=user.email,
        password_hash=hashed_password,
        first_name=user.first_name,
        last_name=user.last_name,
        # SECURE FIX: Auto-verify only if we are actively running Pytest!
        is_verified=True if (user.email.endswith("@test.com") and getattr(settings, "TESTING", False)) else False
    )

    # 4. ASSIGN DEFAULT ROLE
    # Ensure the 'member' role exists in the database. If not, create it dynamically.
    role_result = await db.execute(select(Role).where(Role.name == "member"))
    default_role = role_result.scalars().first()

    if not default_role:
        default_role = Role(name="member", description="Standard gym member")
        db.add(default_role)
        await db.commit()
        await db.refresh(default_role)

    # Add the role to the user's list of roles
    # (SQLAlchemy automatically handles the user_roles Many-to-Many association table)
    new_user.roles.append(default_role)

    # 5. Save to database
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # FIX for async sqlalchemy (MissingGreenlet Error):
    # We must explicitly reload the user with all necessary relationships (roles, subscriptions)
    # before returning it to Pydantic for serialization.
    stmt = (
        select(User)
        .options(selectinload(User.roles), selectinload(User.subscriptions))
        .where(User.id == new_user.id)
    )
    result = await db.execute(stmt)
    created_user = result.scalars().first()

    verification_token = create_action_token(created_user.email, "verify_email")
    await send_verification_email(created_user.email, verification_token)

    return created_user


@router.get("/", response_model=list[UserResponse])
async def get_all_users(
    db: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin) # <--- ZAKLJUČANO ZA ADMINA
):
    """
    Admin Dashboard route: Fetch all users along with their roles and subscriptions.
    """
    # Fetch users, and join their roles AND their subscriptions in one fast query
    stmt = select(User).options(
        selectinload(User.roles),
        selectinload(User.subscriptions) # Puni listu pretplata za front-end
    )
    result = await db.execute(stmt)
    users = result.scalars().all()
    return users

@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
async def login(
    request: Request,
    user_credentials: UserLogin,
    db: AsyncSession = Depends(get_db)
):

    # 1. Fetch user from the database by email
    result = await db.execute(select(User).where(User.email == user_credentials.email))
    user = result.scalars().first()

    # 2. Check if user exists & verify password
    if not user or not verify_password(user_credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Credentials"
        )

    # Block unverified users from logging in, UNLESS we are running automated Pytests.
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please check your inbox."
        )

    # 3. EXTRACT ROLES
    # Extract just the role names into a flat list, e.g., ["member", "admin"]
    role_names = [role.name for role in user.roles]

    # 4. Generate the JWT access token (packing the list of roles into the token)
    access_token = create_access_token(data={"sub": str(user.id), "roles": role_names})

    # 5. Return the token to the client
    return {"access_token": access_token, "token_type": "bearer"}


get_current_admin = RequireRole("admin")


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
        user_id: int,
        db: AsyncSession = Depends(get_db),
        admin_id: int = Depends(get_current_admin)
):
    """
    Admin deletes a user from the system.
    Demonstrates graceful error handling.
    """
    # 1. Attempt to find the user in the database
    result = await db.execute(select(User).where(User.id == user_id))
    user_to_delete = result.scalars().first()

    # 2. GRACEFUL HANDLING: If user doesn't exist, throw a clean 404 error instead of 500
    if not user_to_delete:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    # 3. If exists, proceed with deletion
    # (Cascade delete handles logs and subscriptions automatically due to DB foreign keys)
    await db.delete(user_to_delete)
    await db.commit()

    # HTTP_204_NO_CONTENT means successful execution, but no JSON body is returned
    return None


# ==========================================
# EMAIL VERIFICATION & PASSWORD RESET
# ==========================================

@router.get("/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    """
    Public Route: User clicks the link in their email to verify their account.
    """
    try:
        # Decode the token
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email = payload.get("sub")
        token_type = payload.get("type")

        if token_type != "verify_email" or not email:
            raise HTTPException(status_code=400, detail="Invalid token scope")

        # Find user and update status
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.is_verified:
            return {"message": "Email is already verified. You can log in."}

        user.is_verified = True
        db.add(user)
        await db.commit()

        return {"status": "success", "message": "Email successfully verified!"}

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Verification link expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid verification link")


@router.post("/forgot-password")
async def forgot_password(request: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    """
    Public Route: User requests a password reset link.
    """
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalars().first()

    # SECURITY BEST PRACTICE: Always return a generic success message
    # even if the email doesn't exist, to prevent hackers from "email guessing"
    if user:
        reset_token = create_action_token(user.email, "reset_password")
        await send_password_reset_email(user.email, reset_token)

    return {"message": "If that email is registered, a password reset link has been sent."}


@router.post("/reset-password")
async def reset_password(payload: PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    """
    Public Route: User submits their new password along with the secure token.
    """
    try:
        decoded = jwt.decode(payload.token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email = decoded.get("sub")
        token_type = decoded.get("type")

        if token_type != "reset_password" or not email:
            raise HTTPException(status_code=400, detail="Invalid token scope")

        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Hash and save the new password
        user.password_hash = get_password_hash(payload.new_password)
        db.add(user)
        await db.commit()

        return {"status": "success", "message": "Password successfully reset!"}

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Reset link expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid reset link")



