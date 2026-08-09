import jwt
import smtplib
import asyncio
import resend  
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from app.core.config import settings

# Configure Resend API Key
if settings.RESEND_API_KEY:
    resend.api_key = settings.RESEND_API_KEY


def create_action_token(email: str, action: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    to_encode = {"sub": email, "type": action, "exp": expire}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


class EmailProvider(ABC):
    @abstractmethod
    async def send_email(self, to_email: str, subject: str, body: str, html: bool = False):
        pass


# ==========================================
# NEW: RESEND PROVIDER
# ==========================================
class ResendEmailProvider(EmailProvider):
    """
    Implementation using the modern Resend API.
    """

    def _send_sync(self, to_email: str, subject: str, body: str, html: bool):
        params = {
            "from": settings.EMAIL_FROM,  # Must be onboarding@resend.dev unless you own a domain
            "to": [to_email],
            "subject": subject,
        }
        if html:
            params["html"] = body
        else:
            params["text"] = body

        return resend.Emails.send(params)

    async def send_email(self, to_email: str, subject: str, body: str, html: bool = False):
        try:
            # Wrap the synchronous Resend SDK call in a background thread
            response = await asyncio.to_thread(self._send_sync, to_email, subject, body, html)
            print(f"✅ Real email successfully sent via Resend to: {to_email}")
        except Exception as e:
            print(f"❌ Failed to send email via Resend to {to_email}. Error: {e}")


# ==========================================
# OLD: SMTP & MOCK PROVIDERS (Still here if needed)
# ==========================================
class SMTPEmailProvider(EmailProvider):
    def _send_sync(self, to_email: str, subject: str, body: str, html: bool):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = to_email
        mime_type = "html" if html else "plain"
        msg.attach(MIMEText(body, mime_type))
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.send_message(msg)

    async def send_email(self, to_email: str, subject: str, body: str, html: bool = False):
        try:
            await asyncio.to_thread(self._send_sync, to_email, subject, body, html)
            print(f"✅ Real email successfully sent via SMTP to: {to_email}")
        except Exception as e:
            print(f"❌ Failed to send email via SMTP to {to_email}. Error: {e}")


class MockEmailProvider(EmailProvider):
    async def send_email(self, to_email: str, subject: str, body: str, html: bool = False):
        print("\n" + "=" * 60)
        print(f"📧 MOCK EMAIL SENT TO: {to_email}")
        print(f"Subject: {subject}")
        print(f"Body:\n{body}")
        print("=" * 60 + "\n")


# ==========================================
# FACTORY: DECIDE WHICH ONE TO USE
# ==========================================
def get_email_provider() -> EmailProvider:
    if settings.TESTING:
        return MockEmailProvider()

    # Priority 1: Resend
    if settings.RESEND_API_KEY:
        return ResendEmailProvider()

    # Priority 2: SMTP
    if settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASS:
        return SMTPEmailProvider()

    print("⚠️ WARNING: No email credentials found. Using MockEmailProvider.")
    return MockEmailProvider()


email_provider = get_email_provider()


async def send_verification_email(email: str, token: str):
    verify_url = f"http://localhost:5173/verify-email?token={token}"
    subject = "Verify your FitPass Clone Account"
    body = f"Welcome to FitPass! Please click the link below to verify your email:\n\n{verify_url}"
    await email_provider.send_email(to_email=email, subject=subject, body=body, html=False)


async def send_password_reset_email(email: str, token: str):
    reset_url = f"http://localhost:5173/reset-password?token={token}"
    subject = "Password Reset Request"
    body = f"We received a request to reset your password. Click below:\n\n{reset_url}"
    await email_provider.send_email(to_email=email, subject=subject, body=body, html=False)