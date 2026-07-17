import jwt
from datetime import datetime, timedelta, timezone
from app.core.config import settings


def create_action_token(email: str, action: str) -> str:
    """
    Generates a specific JWT token for email actions (verification or password reset).
    Valid for 24 hours.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    # 'action' can be "verify_email" or "reset_password"
    to_encode = {"sub": email, "type": action, "exp": expire}

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def send_verification_email(email: str, token: str):
    """
    Simulates sending an account verification email.
    In production, replace this print statement with smtplib, SendGrid, or AWS SES.
    """
    verify_url = f"http://localhost:8000/api/users/verify-email?token={token}"

    print("\n" + "=" * 60)
    print(f"📧 EMAIL MOCK SENT TO: {email}")
    print(f"Subject: Verify your Gym App Account")
    print(f"Body: Welcome! Please click the link below to verify your email:")
    print(f"👉 {verify_url}")
    print("=" * 60 + "\n")


async def send_password_reset_email(email: str, token: str):
    """
    Simulates sending a password reset email.
    """
    # This URL usually points to the Frontend React/Vue application
    reset_url = f"http://localhost:3000/reset-password?token={token}"

    print("\n" + "=" * 60)
    print(f"📧 EMAIL MOCK SENT TO: {email}")
    print(f"Subject: Password Reset Request")
    print(f"Body: We received a request to reset your password. Click below:")
    print(f"👉 {reset_url}")
    print("=" * 60 + "\n")