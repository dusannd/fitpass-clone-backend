from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
import jwt


from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """
    Creates a JWT access token containing user data (like user ID and role).
    """
    to_encode = data.copy()

    # Calculate expiration time using Pydantic settings
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})

    # Generate the encoded JWT string using our secret key and algorithm from settings
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_qr_token(user_id: int) -> str:
    """
    Creates a very short-lived JWT token specifically for the QR code.
    Expires in 1 minute to prevent QR code screenshot sharing.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=1)
    to_encode = {"sub": str(user_id), "type": "qr_access", "exp": expire}

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt