import jwt
import re
import smtplib
import asyncio
import resend
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
from pathlib import Path

from app.core.config import settings

# Configure Resend API Key
if settings.RESEND_API_KEY:
    resend.api_key = settings.RESEND_API_KEY


def create_action_token(email: str, action: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    to_encode = {"sub": email, "type": action, "exp": expire}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ==========================================
# LINKS
# ==========================================
def frontend_link(path: str) -> str:
    """
    Builds a link back into the SPA from settings.FRONTEND_URL.

    Every link used to be hardcoded to http://localhost:5173, which meant that
    every verification email sent from a deployed server pointed at the
    recipient's own machine.

    The trailing slash is trimmed because a FRONTEND_URL of "https://gym.rs/"
    would otherwise produce "https://gym.rs//verify-email" - which works on most
    servers but looks broken to whoever reads the email.
    """
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


# ==========================================
# HTML TEMPLATES
# ==========================================
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# Only these three are ever substituted. Anything else that looks like a
# placeholder is left alone rather than silently becoming an empty string.
_PLACEHOLDER_PATTERN = re.compile(r"\{\{(name|email|verificationUrl)\}\}")


@lru_cache(maxsize=None)
def load_template(filename: str) -> str | None:
    """
    Reads a template off disk, once per process.

    Cached because verification emails go out from a background task: without
    this, every single signup would hit the filesystem again for a file that
    never changes while the server is running.

    Returns None instead of raising when the file is missing. A broken template
    must not take down registration - the caller falls back to the plain text
    body, which still carries the link.
    """
    try:
        return (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
    except OSError as e:
        print(f"⚠️ WARNING: Could not read email template {filename}: {e}")
        return None


def render_template(filename: str, values: dict[str, str]) -> str | None:
    """
    Fills a template's placeholders with escaped values.

    Two things worth knowing:

    1. Every value is HTML-escaped. `name` and `email` come straight from user
       input, so an unescaped quote or angle bracket would break out of the
       attribute it sits in - {{email}} is rendered inside a mailto: href.
    2. The substitution is a SINGLE pass over the template, not one .replace()
       per key. Chained replaces would re-scan text that was just inserted, so
       a user whose first name is literally "{{verificationUrl}}" would have
       their own verification link pasted into the greeting.
    """
    template = load_template(filename)
    if template is None:
        return None

    return _PLACEHOLDER_PATTERN.sub(lambda m: escape(values.get(m.group(1), "")), template)


class EmailProvider(ABC):
    @abstractmethod
    async def send_email(self, to_email: str, subject: str, text_body: str, html_body: str | None = None):
        pass


# ==========================================
# NEW: RESEND PROVIDER
# ==========================================
class ResendEmailProvider(EmailProvider):
    """
    Implementation using the modern Resend API.
    """

    def _send_sync(self, to_email: str, subject: str, text_body: str, html_body: str | None):
        params = {
            "from": settings.EMAIL_FROM,  # Must be onboarding@resend.dev unless you own a domain
            "to": [to_email],
            "subject": subject,
            # Always send the text part too: it is what plain-text clients and
            # spam filters read, and it keeps the link reachable if the HTML
            # fails to render.
            "text": text_body,
        }
        if html_body:
            params["html"] = html_body

        return resend.Emails.send(params)

    async def send_email(self, to_email: str, subject: str, text_body: str, html_body: str | None = None):
        try:
            # Wrap the synchronous Resend SDK call in a background thread
            await asyncio.to_thread(self._send_sync, to_email, subject, text_body, html_body)
            print(f"✅ Real email successfully sent via Resend to: {to_email}")
        except Exception as e:
            print(f"❌ Failed to send email via Resend to {to_email}. Error: {e}")


# ==========================================
# OLD: SMTP & MOCK PROVIDERS (Still here if needed)
# ==========================================
class SMTPEmailProvider(EmailProvider):
    def _send_sync(self, to_email: str, subject: str, text_body: str, html_body: str | None):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = to_email

        # ORDER MATTERS. In a multipart/alternative message the client picks the
        # LAST part it can render, so the plain text fallback has to be attached
        # first and the HTML second - the other way round every modern client
        # would show the plain text version.
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.send_message(msg)

    async def send_email(self, to_email: str, subject: str, text_body: str, html_body: str | None = None):
        try:
            await asyncio.to_thread(self._send_sync, to_email, subject, text_body, html_body)
            print(f"✅ Real email successfully sent via SMTP to: {to_email}")
        except Exception as e:
            print(f"❌ Failed to send email via SMTP to {to_email}. Error: {e}")


class MockEmailProvider(EmailProvider):
    async def send_email(self, to_email: str, subject: str, text_body: str, html_body: str | None = None):
        print("\n" + "=" * 60)
        print(f"📧 MOCK EMAIL SENT TO: {to_email}")
        print(f"Subject: {subject}")
        # The HTML part is thousands of characters of table markup - noting that
        # it is attached is far more readable in a test log than dumping it.
        print(f"HTML part: {'attached' if html_body else 'none'}")
        print(f"Body:\n{text_body}")
        print("=" * 60 + "\n")


# ==========================================
# FACTORY: DECIDE WHICH ONE TO USE
# ==========================================
def get_email_provider() -> EmailProvider:
    """
    Picks a provider from whatever credentials are configured.

    Called per send rather than once at import. That used to be a module-level
    `email_provider = get_email_provider()`, which resolved while this module was
    being imported - and test/conftest.py imports app.main (which reaches here)
    BEFORE it sets settings.TESTING = True. The whole suite therefore held a live
    ResendEmailProvider, and every test that registered a user fired a real HTTP
    request at the Resend API. It looked harmless only because send_email swallows
    its own exceptions.

    Constructing a provider is just an object allocation - nothing opens a
    connection until send_email runs - so there is nothing to cache here.
    """
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


async def send_verification_email(email: str, token: str, name: str | None = None):
    """
    Sends the branded verification email.

    `name` is optional and last on purpose: this function is queued through
    BackgroundTasks, and the existing add_task(...) calls pass only the email
    and the token. A required parameter here would break those at runtime -
    inside a background task, where the error surfaces long after the endpoint
    has already returned 200.
    """
    verify_url = frontend_link(f"/verify-email?token={token}")
    subject = "Verify your FitPass Clone Account"

    # The plain text version is the fallback, not an afterthought: it goes out
    # with every message and carries the same link.
    text_body = (
        f"Hi {name or 'there'},\n\n"
        "Welcome to FitPass! Please click the link below to verify your email:\n\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours.\n"
        "If you didn't create an account, you can ignore this email."
    )

    html_body = render_template("verify-email.html", {
        "name": name or "there",
        "email": email,
        "verificationUrl": verify_url,
    })

    await get_email_provider().send_email(
        to_email=email, subject=subject, text_body=text_body, html_body=html_body
    )


async def send_password_reset_email(email: str, token: str):
    reset_url = frontend_link(f"/reset-password?token={token}")
    subject = "Password Reset Request"
    text_body = f"We received a request to reset your password. Click below:\n\n{reset_url}"
    await get_email_provider().send_email(to_email=email, subject=subject, text_body=text_body)
