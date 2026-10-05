# app/core/security.py
import uuid
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
import jwt

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# The "type" claim every login cookie carries. QR, verify-email and reset tokens are
# signed with the same key and carry the same "sub", so this claim is the only thing
# that tells a session apart from them. dependencies.decode_session_token checks it.
SESSION_TOKEN_TYPE = "session"


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": SESSION_TOKEN_TYPE})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def create_qr_token(user_id: int, action_type: str) -> str:
    """
    Creates a 5-minute short-lived JWT token for the QR code.
    CRITICAL: Embeds a unique 'jti' (JWT ID) to prevent replay/screenshot attacks.
    """
    # Expiration is exactly 5 minutes (300 seconds)
    expire = datetime.now(timezone.utc) + timedelta(seconds=300)

    to_encode = {
        "sub": str(user_id),
        "type": "qr_access",
        "action_type": action_type,
        "jti": str(uuid.uuid4()),  # UNIQUE IDENTIFIER FOR THIS SPECIFIC QR
        "exp": expire
    }

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt