import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, desc
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_qr_token
from app.core.redis_client import redis_db
from app.core.websockets import ws_manager

from app.models.access import EntryLog
from app.models.subscription import UserSubscription, SubscriptionPlan, SubscriptionRule, plan_locations
from app.schemas.access import QRTokenResponse, ScanRequest, ScanResponse, GenerateQRRequest
from app.api.dependencies import RequireRole

router = APIRouter()

# Bouncers
get_current_worker = RequireRole("worker")
get_current_member = RequireRole("member")


async def resolve_user_status(db: AsyncSession, user_id: int, status_key: str) -> str:
    """
    Returns the user's current physical status from Redis. If Redis has no
    record of it (cold cache, eviction, restart), reconstructs the status from
    the user's last successful EntryLog and re-seeds Redis with it so future
    lookups stay cheap.
    """
    cached_status = await redis_db.get(status_key)
    if cached_status is not None:
        return cached_status

    stmt = (
        select(EntryLog)
        .where(
            and_(
                EntryLog.user_id == user_id,
                EntryLog.access_granted == True
            )
        )
        .order_by(desc(EntryLog.timestamp))
        .limit(1)
    )
    result = await db.execute(stmt)
    last_log = result.scalars().first()

    resolved_status = "INSIDE" if last_log and last_log.action_type == "ENTRY" else "OUTSIDE"
    await redis_db.set(status_key, resolved_status)
    return resolved_status


@router.post("/generate", response_model=QRTokenResponse)
async def generate_qr_code(
        request: GenerateQRRequest,
        db: AsyncSession = Depends(get_db),
        user_id: int = Depends(get_current_member)
):
    """
    Generates a secure QR token.
    Includes Rate Limiting and pre-emptive Anti-Passback checks.
    """
    if request.action_type not in ["ENTRY", "EXIT"]:
        raise HTTPException(status_code=400, detail="Invalid action type. Must be ENTRY or EXIT.")

    # 1. RATE LIMITING: Prevent spamming (1 QR per 30 seconds)
    rate_limit_key = f"qr_rate_limit:{user_id}"
    is_allowed = await redis_db.set(rate_limit_key, "1", ex=30, nx=True)

    if not is_allowed:
        ttl = await redis_db.ttl(rate_limit_key)
        ttl = max(1, ttl)  # Ensure TTL doesn't return negative
        raise HTTPException(status_code=429, detail=f"Please wait {ttl} seconds before generating a new code.")

    # 2. ANTI-PASSBACK PRE-CHECK: Prevent illogical generation
    status_key = f"user_status:{user_id}"
    current_status = await resolve_user_status(db, user_id, status_key)

    if request.action_type == "ENTRY" and current_status == "INSIDE":
        await redis_db.delete(rate_limit_key)  # Free the rate limit so they can select EXIT
        raise HTTPException(status_code=400, detail="You are already INSIDE. Please select Check Out.")

    if request.action_type == "EXIT" and current_status == "OUTSIDE":
        await redis_db.delete(rate_limit_key)  # Free the rate limit
        raise HTTPException(status_code=400, detail="You are currently OUTSIDE. Please select Check In.")

    # 3. GENERATE TOKEN
    token = create_qr_token(user_id=user_id, action_type=request.action_type)
    return {"qr_token": token, "expires_in_seconds": 300}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Secure WebSocket endpoint. Reads the HTTP-Only cookie automatically sent by the browser.
    """
    try:
        token = websocket.cookies.get("access_token")
        if not token:
            await websocket.close(code=1008, reason="Missing authentication cookie")
            return

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(payload.get("sub"))

        await ws_manager.connect(websocket, user_id)

        while True:
            await websocket.receive_text()

    except jwt.ExpiredSignatureError:
        await websocket.close(code=1008, reason="Token expired")
    except jwt.InvalidTokenError:
        await websocket.close(code=1008, reason="Invalid token")
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id)
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))


@router.post("/scan", response_model=ScanResponse)
async def scan_qr_code(
        payload: ScanRequest,
        db: AsyncSession = Depends(get_db),
        worker_id: int = Depends(get_current_worker)
):
    """
    Turnstile scanner endpoint. Validates QR, checks Anti-Passback, burns the JTI, and logs it.
    """
    now = datetime.now(timezone.utc)

    # 1. DECODE & EXTRACT METADATA
    try:
        decoded_token = jwt.decode(payload.qr_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(decoded_token.get("sub"))
        token_type = decoded_token.get("type")
        action_type = decoded_token.get("action_type")
        jti = decoded_token.get("jti")  # Get Unique Token ID

        if token_type != "qr_access" or not jti:
            raise HTTPException(status_code=400, detail="Invalid token structure.")

        # Intent validation
        if action_type != payload.scan_type:
            await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                 f"Intent mismatch: Scanned {action_type} at {payload.scan_type} turnstile.")
            raise HTTPException(status_code=400, detail="Turnstile mismatch.")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="QR Code expired. Generate a new one.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid QR Code.")

    # 2. ATOMIC SCREENSHOT/REPLAY PREVENTION
    # Attempts to save the JTI to Redis. If it already exists, nx=True returns False/None.
    jti_key = f"consumed_jti:{jti}"
    is_first_scan = await redis_db.set(jti_key, "1", ex=300, nx=True)

    if not is_first_scan:
        await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                             "Replay Attack: Token already consumed.")
        raise HTTPException(status_code=403, detail="Code already used. Screenshots are strictly forbidden.")

    # 3. REDIS ANTI-PASSBACK
    redis_key = f"user_status:{user_id}"
    current_status = await resolve_user_status(db, user_id, redis_key)

    if action_type == "ENTRY" and current_status == "INSIDE":
        await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                             "Anti-Passback Violation: Already inside.")
        raise HTTPException(status_code=403, detail="Passback Error: You are already inside the gym.")

    if action_type == "EXIT" and current_status == "OUTSIDE":
        await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                             "Anti-Passback Violation: Not checked in.")
        raise HTTPException(status_code=403, detail="Passback Error: You cannot exit if you haven't entered.")

    # 4. CHECK SUBSCRIPTION VALIDITY (ONLY FOR ENTRY)
    if action_type == "ENTRY":
        stmt = (
            select(UserSubscription)
            .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
            .outerjoin(plan_locations, plan_locations.c.plan_id == SubscriptionPlan.id)
            .outerjoin(SubscriptionRule, SubscriptionRule.plan_id == SubscriptionPlan.id)
            .options(
                selectinload(UserSubscription.plan).selectinload(SubscriptionPlan.locations),
                selectinload(UserSubscription.plan).selectinload(SubscriptionPlan.rule),
            )
            .where(
                and_(
                    UserSubscription.user_id == user_id,
                    UserSubscription.is_active == 1,
                    UserSubscription.end_date > now
                )
            )
            .distinct()
            # Newest pass first, with id as the tiebreaker. This used to take
            # .first() off an unordered query, so WHICH plan's locations and hours
            # were applied at the door was whatever the database happened to return.
            # payments.py refuses a second concurrent subscription, so it should
            # never matter - but now that plans genuinely grant different things,
            # an arbitrary pick is a door that opens on some scans and not others.
            .order_by(UserSubscription.end_date.desc(), UserSubscription.id.desc())
        )
        result = await db.execute(stmt)
        active_sub = result.scalars().first()

        if not active_sub:
            await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                 "No active subscription found.")
            raise HTTPException(status_code=403, detail="Access Denied: No active subscription.")

        # 4a. LOCATION CHECK: Does this plan grant access to the scanned gym?
        allowed_location_ids = [loc.id for loc in active_sub.plan.locations]
        if payload.location_id not in allowed_location_ids:
            await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                 "Access Denied: Your plan does not include this location.")
            raise HTTPException(status_code=403, detail="Access Denied: Your plan does not include this location.")

        # 4b. TIME/DAY RULE CHECK: Plans without a rule are unrestricted (24/7, every day)
        rule = active_sub.plan.rule
        if rule:
            current_time = now.time()
            current_weekday = now.weekday()  # 0=Monday ... 6=Sunday, matches allowed_days convention

            if rule.allowed_days:
                allowed_days_list = [int(d) for d in rule.allowed_days.split(",") if d.strip() != ""]
                if current_weekday not in allowed_days_list:
                    await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                         "Access Denied: Outside allowed days for your plan.")
                    raise HTTPException(status_code=403, detail="Access Denied: Your plan does not allow access on this day.")

            if rule.allowed_time_start and rule.allowed_time_end:
                if not (rule.allowed_time_start <= current_time <= rule.allowed_time_end):
                    await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                         "Access Denied: Outside allowed time window for your plan.")
                    raise HTTPException(status_code=403, detail="Access Denied: Your plan does not allow access at this time.")

    # 5. SUCCESS: UPDATE REDIS STATE & LOG
    new_status = "INSIDE" if action_type == "ENTRY" else "OUTSIDE"
    await redis_db.set(redis_key, new_status)

    await log_and_notify(db, user_id, worker_id, payload.location_id, True, action_type, "Access Granted.")

    return {
        "access_granted": True,
        "message": f"Successfully processed {action_type}.",
        "user_id": user_id,
        "action_type": action_type
    }


async def log_and_notify(db: AsyncSession, user_id: int, worker_id: int, location_id: int, granted: bool,
                         action_type: str, reason: str):
    # Log to DB
    entry_log = EntryLog(
        user_id=user_id,
        worker_id=worker_id,
        location_id=location_id,
        access_granted=granted,
        action_type=action_type,
        reason=reason
    )
    db.add(entry_log)
    await db.commit()

    # Trigger WebSocket
    await ws_manager.send_personal_message(
        message={
            "type": "ACCESS_EVENT",
            "access_granted": granted,
            "action_type": action_type,
            "reason": reason
        },
        user_id=user_id
    )


@router.get("/my-status")
async def get_my_status(
        db: AsyncSession = Depends(get_db),
        user_id: int = Depends(get_current_member)
):
    """
    Returns the user's current physical status (INSIDE/OUTSIDE)
    and the timestamp of their entry if they are currently inside.
    """
    # 1. Check the current state in Redis
    status_key = f"user_status:{user_id}"
    current_status = await redis_db.get(status_key) or "OUTSIDE"

    entered_at = None

    # 2. If the user is inside, find the exact entry timestamp from the database
    #    so the frontend can calculate the session duration.
    if current_status == "INSIDE":
        stmt = (
            select(EntryLog)
            .where(
                and_(
                    EntryLog.user_id == user_id,
                    EntryLog.action_type == "ENTRY",
                    EntryLog.access_granted == True
                )
            )
            .order_by(desc(EntryLog.timestamp))
            .limit(1)
        )
        result = await db.execute(stmt)
        last_entry = result.scalars().first()

        if last_entry:
            entered_at = last_entry.timestamp

    return {
        "status": current_status,
        "entered_at": entered_at
    }