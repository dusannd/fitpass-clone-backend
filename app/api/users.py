from fastapi import (APIRouter, Depends, HTTPException, status, Request, BackgroundTasks,
Query, Response, UploadFile, File)
from fastapi.responses import JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.api.dependencies import get_current_admin, get_current_user_id
from starlette.concurrency import run_in_threadpool
import jwt
import logging
import stripe

from app.core.rate_limit import limiter
# We now import the Role model as well
from app.models.user import User, Role, UserProfile
from app.models.subscription import UserSubscription
from app.core.database import get_db
from app.schemas.user import (UserCreate, UserResponse, UserLogin, Token,
PasswordResetRequest, PasswordResetConfirm, ResendVerificationRequest,
UserProfileUpdate, UserProfileResponse)
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.config import settings
from app.services.email import create_action_token, send_verification_email, send_password_reset_email
from app.services.recaptcha import verify_recaptcha
from app.services.storage import save_avatar, delete_avatar


logger = logging.getLogger(__name__)

# Set here as well as in payments.py. It is the same value written to the same
# global, but a billing call must not depend on another router happening to have
# been imported first - import order is not something this module controls.
stripe.api_key = settings.STRIPE_API_KEY

router = APIRouter()


@router.post("/", response_model=UserResponse)
async def create_user(
    user: UserCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # 0. SECURITY: Honeypot Check (Bot trap)
    if user.extra_info:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"code": "HONEYPOT_TRIGGERED", "message": "Invalid request."}
        )

    # ---> NEW: 0.5 SECURITY: reCAPTCHA Verification <---
    await verify_recaptcha(user.recaptcha_token)

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
        is_verified=True if (user.email.endswith("@test.com") and getattr(settings, "TESTING", False)) else False
    )

    # 4. ASSIGN DEFAULT ROLE
    role_result = await db.execute(select(Role).where(Role.name == "member"))
    default_role = role_result.scalars().first()

    if not default_role:
        default_role = Role(name="member", description="Standard gym member")
        db.add(default_role)
        await db.commit()
        await db.refresh(default_role)

    new_user.roles.append(default_role)

    # 5. NEW: Attach the optional profile (bio / fitness goals) from the form.
    # The cascade on User.profile inserts this in the same transaction as the user.
    if user.profile:
        new_user.profile = UserProfile(
            bio=user.profile.bio,
            fitness_goals=user.profile.fitness_goals,
        )

    # 6. Save to database
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    stmt = (
        select(User)
        .options(
            selectinload(User.roles),
            selectinload(User.subscriptions),
            selectinload(User.profile)
        )
        .where(User.id == new_user.id)
    )
    result = await db.execute(stmt)
    created_user = result.scalars().first()

    # 7. SEND EMAIL IN THE BACKGROUND
    verification_token = create_action_token(created_user.email, "verify_email")

    # <--- 2. USE BACKGROUND_TASKS.ADD_TASK INSTEAD OF AWAIT --->
    # The server will return the response immediately and run this function in the background.
    # The first name is read here and passed as a plain string. add_task evaluates its
    # arguments immediately, so nothing hands the ORM object itself to the background
    # task - by the time that runs, the session is closed and the instance detached.
    background_tasks.add_task(
        send_verification_email, created_user.email, verification_token, created_user.first_name
    )

    return created_user

@router.get("/", response_model=list[UserResponse])
async def get_all_users(
    skip: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=100, description="Max 100 records per page"),
    db: AsyncSession = Depends(get_db),
    admin_id: int = Depends(get_current_admin)
):
    """
    Admin Dashboard route: Fetch paginated users along with their roles and subscriptions.
    """
    # Fetch users, applying limit and offset for pagination
    stmt = (
        select(User)
        .options(
            selectinload(User.roles),
            selectinload(User.subscriptions),
            selectinload(User.profile)  # NEW: eager load so we don't fire N+1 queries
        )
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    users = result.scalars().all()
    return users

@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    user_credentials: UserLogin,
    db: AsyncSession = Depends(get_db)
):

    # 0. SECURITY: Honeypot Check (Bot trap)
    if user_credentials.extra_info:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"code": "HONEYPOT_TRIGGERED", "message": "Invalid request."}
        )

    # ---> NEW: 0.5 SECURITY: reCAPTCHA Verification <---
    await verify_recaptcha(user_credentials.recaptcha_token)

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
    # Extract roles
    role_names = [role.name for role in user.roles]

    # Generate the JWT access token
    access_token = create_access_token(data={"sub": str(user.id), "roles": role_names})

    # --- NEW: SET HTTP-ONLY COOKIE ---
    # We calculate the max_age in seconds
    max_age_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,  # Prevents XSS attacks (JS cannot read it)
        secure=not settings.TESTING,  # True in production (HTTPS required)
        samesite="lax",  # CSRF protection
        max_age=max_age_seconds,
        expires=max_age_seconds,
    )

    # We no longer need to return the token in the JSON body
    return {"message": "Successfully logged in"}


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Vraća profil trenutno ulogovanog korisnika na osnovu JWT tokena.
    """
    stmt = (
        select(User)
        .options(
            selectinload(User.roles),
            selectinload(User.subscriptions),
            selectinload(User.profile)  # NEW
        )
        .where(User.id == current_user_id)
    )
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


async def get_or_create_profile(db: AsyncSession, user_id: int) -> UserProfile:
    """
    Helper: loads the user together with their profile in one round trip.
    Accounts made before profiles existed don't have a row yet, so we build one
    instead of throwing a 404 at them.
    """
    stmt = (
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.profile is None:
        user.profile = UserProfile(user_id=user.id)

    return user.profile


@router.put("/me/profile", response_model=UserProfileResponse)
async def update_my_profile(
        payload: UserProfileUpdate,
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Logged in user updates their own profile text (bio + fitness goals).
    The profile picture has its own endpoint below.
    """
    # 1. Grab the profile (created on the fly for older accounts)
    profile = await get_or_create_profile(db, current_user_id)

    # 2. Only touch the keys the client actually sent (partial update).
    # exclude_unset keeps "field not sent" different from "field sent as null".
    changes = payload.model_dump(exclude_unset=True)

    for field, value in changes.items():
        # Empty textareas come back as "" from the frontend, store them as NULL
        if isinstance(value, str):
            value = value.strip() or None
        setattr(profile, field, value)

    # 3. Save
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return profile


@router.post("/me/avatar", response_model=UserProfileResponse)
@limiter.limit("10/hour")
async def upload_my_avatar(
        request: Request,  # <--- Required by slowapi for rate limiting (tracks IP)
        response: Response,  # <--- Required by slowapi to write the X-RateLimit headers
        file: UploadFile = File(...),
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Logged in user uploads a new profile picture.
    Rate limited, since image processing is the most expensive thing we do here.
    """
    # 1. Grab the profile so we know which old file to clean up
    profile = await get_or_create_profile(db, current_user_id)

    # 2. Validate + resize + store. This raises a clean 400/413 if the file is
    # not a real image or is too big, and deletes the previous picture for us.
    new_url = await save_avatar(file, old_url=profile.profile_picture_url)

    # 3. Point the profile at the new file
    profile.profile_picture_url = new_url
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return profile


@router.delete("/me/avatar", response_model=UserProfileResponse)
async def delete_my_avatar(
        db: AsyncSession = Depends(get_db),
        current_user_id: int = Depends(get_current_user_id),
):
    """
    Logged in user removes their profile picture and falls back to the initials.
    """
    profile = await get_or_create_profile(db, current_user_id)

    # Wipe the file from disk first, then the reference in the DB
    delete_avatar(profile.profile_picture_url)
    profile.profile_picture_url = None

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return profile


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

    # 3. Cancel any live Stripe subscription BEFORE the row disappears.
    # Deleting the account only removes OUR records - Stripe knows nothing about
    # our database and keeps charging the card. Worse, the cascade below destroys
    # the stripe_subscription_id in the same transaction, so after this point
    # there is nothing left to look the subscription up by.
    #
    # Queried directly instead of walking user_to_delete.subscriptions on purpose:
    # that relationship is a plain (non-selectin) one, so touching it here would
    # trigger a lazy load. A separate SELECT sidesteps that entirely.
    result = await db.execute(
        select(UserSubscription.stripe_subscription_id).where(
            UserSubscription.user_id == user_id,
            UserSubscription.is_active == 1,
            UserSubscription.stripe_subscription_id.is_not(None),
        )
    )

    # A member can hold more than one active row - an upgrade leaves the previous
    # subscription behind - so cancel every distinct id rather than the first one
    # found. Half-fixing this is worse than not fixing it, because it looks fixed.
    for stripe_sub_id in set(result.scalars().all()):
        try:
            # run_in_threadpool because the Stripe SDK is synchronous `requests`
            # underneath; calling it bare in an async route blocks the event loop
            # for the whole round trip.
            await run_in_threadpool(stripe.Subscription.cancel, stripe_sub_id)
        except stripe.StripeError:
            # Deliberately non-fatal: an admin has to be able to delete an account
            # while Stripe is down - a GDPR erasure request does not wait for
            # someone else's outage. Logged at ERROR rather than warning, with the
            # id spelled out, because the row is about to be deleted and this line
            # becomes the ONLY record of a subscription that is still billing and
            # now has to be cancelled by hand in the Stripe dashboard.
            logger.error(
                "Failed to cancel Stripe subscription %s while deleting user %s. "
                "It is STILL BILLING and must be cancelled manually.",
                stripe_sub_id, user_id, exc_info=True,
            )

    # 4. Now remove the user. The related rows go with it through the ORM cascade
    # configured on User.subscriptions and its sibling relationships.
    await db.delete(user_to_delete)
    await db.commit()

    # HTTP_204_NO_CONTENT means successful execution, but no JSON body is returned
    return None


# ==========================================
# EMAIL VERIFICATION & PASSWORD RESET
# ==========================================


@router.post("/resend-verification")
@limiter.limit("1/15minutes")
async def resend_verification(
    request: Request, # <--- Required by slowapi for rate limiting (tracks IP)
    response: Response, # <--- Required by slowapi to write the X-RateLimit headers
    payload: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Public Route: Allows a user to request a new email verification link.
    Rate limited to 1 request per 15 minutes to prevent email spam.
    """
    # 1. Look up the user by email
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalars().first()

    # 2. Security Check: We use a generic response to prevent email enumeration.
    # We only trigger the email if the user exists AND they are NOT verified yet.
    if user and not user.is_verified:
        verification_token = create_action_token(user.email, "verify_email")
        # Send the email in the background so the endpoint returns instantly.
        # The name goes as a plain string, not the ORM object - see /register above.
        background_tasks.add_task(
            send_verification_email, user.email, verification_token, user.first_name
        )

    # 3. Always return a 200 OK with the same message, regardless of whether the email exists.
    return {
        "message": "If this email is registered and unverified, a new verification link has been sent."
    }

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
@limiter.limit("3/15minutes")
async def forgot_password(
    request: Request, # <--- Required by slowapi for rate limiting (tracks IP)
    response: Response, # <--- Required by slowapi to write the X-RateLimit headers
    payload: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Public Route: User requests a password reset link.
    Rate limited to 3 requests per 15 minutes - without one, the enumeration
    below can simply be run as a wordlist at full speed.
    """
    # 0.5 SECURITY: reCAPTCHA Verification.
    # Runs before the lookup so both branches below still cost the same - checking
    # it later would reintroduce the timing difference step 2 exists to remove.
    await verify_recaptcha(payload.recaptcha_token)

    # 1. Look up the user by email
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalars().first()

    # 2. Security: the response never says whether the email exists. That only
    # holds if the RESPONSE TIME says nothing either - this used to await the
    # send, which in production is a live HTTPS round trip to Resend. A known
    # address took a few hundred milliseconds, an unknown one a few, and the
    # generic message below could be defeated with a stopwatch.
    # Queueing the mail makes both branches return at the same speed.
    if user:
        reset_token = create_action_token(user.email, "reset_password")
        background_tasks.add_task(send_password_reset_email, user.email, reset_token)

    # 3. Always the same body, whether or not the account exists.
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



# --- NEW: ADD LOGOUT ENDPOINT ---
@router.post("/logout")
async def logout(response: Response):
    """
    Clears the httpOnly cookie to log the user out.
    """
    response.delete_cookie("access_token", httponly=True, samesite="lax")
    return {"message": "Successfully logged out"}