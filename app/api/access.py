import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_qr_token
from app.core.redis_client import redis_db
from app.core.websockets import ws_manager

from app.models.access import EntryLog
from app.models.subscription import UserSubscription, SubscriptionPlan
from app.schemas.access import QRTokenResponse, ScanRequest, ScanResponse, GenerateQRRequest
from app.api.dependencies import RequireRole

router = APIRouter()

# Bouncers
get_current_worker = RequireRole("worker")
get_current_member = RequireRole("member")


@router.post("/generate", response_model=QRTokenResponse)
async def generate_qr_code(
        request: GenerateQRRequest,
        user_id: int = Depends(get_current_member)
):
    """
    User requests a QR code. They must specify if they intend to ENTER or EXIT.
    """
    if request.action_type not in ["ENTRY", "EXIT"]:
        raise HTTPException(status_code=400, detail="Invalid action type. Must be ENTRY or EXIT.")

    token = create_qr_token(user_id=user_id, action_type=request.action_type)
    return {"qr_token": token, "expires_in_seconds": 60}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Secure WebSocket endpoint. Reads the HTTP-Only cookie automatically sent by the browser.
    """
    try:
        # Extract token securely from the cookie
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
    Turnstile scanner endpoint. Validates QR, checks Anti-Passback via Redis, logs it, and triggers WebSockets.
    """
    now = datetime.now(timezone.utc)

    # 1. DECODE & VALIDATE JWT QR TOKEN
    try:
        decoded_token = jwt.decode(payload.qr_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(decoded_token.get("sub"))
        token_type = decoded_token.get("type")
        action_type = decoded_token.get("action_type")

        if token_type != "qr_access":
            raise HTTPException(status_code=400, detail="Invalid token type used.")

        # Intent validation
        if action_type != payload.scan_type:
            await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                 f"Mismatched intent: Tried {action_type} at {payload.scan_type} turnstile.")
            raise HTTPException(status_code=400, detail="Turnstile mismatch.")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="QR Code expired. Generate a new one.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid QR Code.")

    # 2. REDIS ANTI-PASSBACK
    redis_key = f"user_status:{user_id}"
    current_status = await redis_db.get(redis_key) or "OUTSIDE"

    if action_type == "ENTRY" and current_status == "INSIDE":
        await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                             "Anti-Passback Violation: Already inside.")
        raise HTTPException(status_code=403, detail="Anti-Passback Error: You are already inside the gym.")

    if action_type == "EXIT" and current_status == "OUTSIDE":
        await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                             "Anti-Passback Violation: Not checked in.")
        raise HTTPException(status_code=403, detail="Anti-Passback Error: You cannot exit if you haven't entered.")

    # 3. CHECK SUBSCRIPTION VALIDITY (ONLY FOR ENTRY)
    if action_type == "ENTRY":
        stmt = (
            select(UserSubscription)
            .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
            .where(
                and_(
                    UserSubscription.user_id == user_id,
                    UserSubscription.is_active == 1,
                    UserSubscription.end_date > now
                )
            )
        )
        result = await db.execute(stmt)
        active_sub = result.scalars().first()

        if not active_sub:
            await log_and_notify(db, user_id, worker_id, payload.location_id, False, action_type,
                                 "No active subscription found.")
            raise HTTPException(status_code=403, detail="Access Denied: No active subscription.")

    # 4. SUCCESS: UPDATE REDIS STATE & LOG
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