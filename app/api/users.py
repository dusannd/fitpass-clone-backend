from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

# We now import the Role model as well
from app.models.user import User, Role
from app.core.database import get_db
from app.schemas.user import UserCreate, UserResponse, UserLogin, Token
from app.core.security import get_password_hash, verify_password, create_access_token
from app.api.dependencies import RequireRole

router = APIRouter()


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
        last_name=user.last_name
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

    return new_user


@router.get("/", response_model=list[UserResponse])
async def get_all_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return users


@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    # 1. Fetch user from the database by email
    result = await db.execute(select(User).where(User.email == user_credentials.email))
    user = result.scalars().first()

    # 2. Check if user exists & verify password
    if not user or not verify_password(user_credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Credentials"
        )

    # 3. EXTRACT ROLES
    # Extract just the role names into a flat list, e.g., ["member", "admin"]
    role_names = [role.name for role in user.roles]

    # 4. Generate the JWT access token (packing the list of roles into the token)
    access_token = create_access_token(data={"sub": str(user.id), "roles": role_names})

    # 5. Return the token to the client
    return {"access_token": access_token, "token_type": "bearer"}

"""
# --- TEMPORARY BACKDOOR FOR DEVELOPMENT ---
from sqlalchemy.orm import selectinload

@router.post("/setup-superuser/{user_id}")
async def setup_superuser(user_id: int, db: AsyncSession = Depends(get_db)):
  
    TEMPORARY ENDPOINT: Gives a user 'admin' and 'worker' privileges.
    Used only for initial setup.

    # 1. Fetch the user and eagerly load their current roles
    result = await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 2. Define the roles we want to grant
    super_roles = ["admin", "worker"]

    for role_name in super_roles:
        # Check if role exists in DB, create if not
        role_result = await db.execute(select(Role).where(Role.name == role_name))
        role_obj = role_result.scalars().first()

        if not role_obj:
            role_obj = Role(name=role_name, description=f"{role_name.capitalize()} privileges")
            db.add(role_obj)
            await db.commit()
            await db.refresh(role_obj)

        # Assign role to user if they don't already have it
        if not any(r.name == role_name for r in user.roles):
            user.roles.append(role_obj)

    # 3. Save the updated user to DB
    db.add(user)
    await db.commit()

    # Extract all role names to show in the response
    current_roles = [r.name for r in user.roles]

    return {
        "message": f"Success! User {user.email} is now a superuser.",
        "current_roles": current_roles
    }

"""

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


