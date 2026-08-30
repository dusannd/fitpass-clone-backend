import jwt
import logging
import re
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


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

# Only these four are ever substituted. Anything else that looks like a
# placeholder is left alone rather than silently becoming an empty string.
# It is an allowlist, so a template using a placeholder that is missing here
# fails LOUDLY - the raw {{...}} is printed on the page - rather than quietly
# emailing somebody a button that goes nowhere.
_PLACEHOLDER_PATTERN = re.compile(r"\{\{(name|email|verificationUrl|resetUrl)\}\}")


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
# SMTP PROVIDER (Gmail and anything else speaking SMTP)
# ==========================================
# A hung connection here pins a thread from the asyncio threadpool for as long as
# it lasts, because _send_sync runs through asyncio.to_thread. Without a timeout
# that is forever.
SMTP_TIMEOUT_SECONDS = 10

# The port Gmail (and most providers) serve implicit TLS on. Everything else is
# assumed to be the STARTTLS style, which is what 587 does.
IMPLICIT_TLS_PORT = 465


class SMTPEmailProvider(EmailProvider):
    def _smtp_password(self) -> str:
        """
        The App Password with its display formatting removed.

        Google shows the App Password as four groups of four ("abcd efgh ijkl
        mnop"). Pasted into .env straight off that screen it keeps the spaces,
        smtplib sends them verbatim, and Gmail answers "535 Username and Password
        not accepted" - which reads exactly like a wrong password and sends you
        looking in the wrong place. App passwords never legitimately contain
        whitespace, so stripping it can only ever be the right reading.
        """
        return "".join((settings.SMTP_PASS or "").split())

    def _send_sync(self, to_email: str, subject: str, text_body: str, html_body: str | None):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject

        # The sender is the authenticated account, NOT settings.EMAIL_FROM.
        # Gmail refuses to relay a From header for an address it does not own, so
        # an EMAIL_FROM that drifts from the mailbox we logged in as would bounce
        # every message. Deriving it from SMTP_USER means the two cannot disagree.
        msg["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.SMTP_USER))
        msg["To"] = to_email

        # ORDER MATTERS. In a multipart/alternative message the client picks the
        # LAST part it can render, so the plain text fallback has to be attached
        # first and the HTML second - the other way round every modern client
        # would show the plain text version.
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        # Port 465 is TLS from the first byte, so there is no plaintext greeting
        # to upgrade - calling starttls() on it just blocks until the timeout.
        # Port 587 is the opposite: it starts in the clear and must be upgraded.
        if settings.SMTP_PORT == IMPLICIT_TLS_PORT:
            server_cls, needs_starttls = smtplib.SMTP_SSL, False
        else:
            server_cls, needs_starttls = smtplib.SMTP, True

        with server_cls(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS
        ) as server:
            if needs_starttls:
                server.starttls()
            server.login(settings.SMTP_USER, self._smtp_password())
            server.send_message(msg)

    async def send_email(self, to_email: str, subject: str, text_body: str, html_body: str | None = None):
        try:
            await asyncio.to_thread(self._send_sync, to_email, subject, text_body, html_body)
        except Exception:
            # Only the recipient goes into the message. The traceback carries the
            # server's own reply - a 535 or a 534 says precisely what is wrong -
            # and no SMTP error echoes the credentials back.
            logger.exception("SMTP send to %s failed", to_email)
            return

        # The success notice sits OUTSIDE the try, and carries no emoji.
        #
        # Both details were paid for: this line used to be a print with a "check
        # mark" inside the try block. Windows falls back to cp1252 whenever stdout
        # is redirected rather than a console, so printing that character raised
        # UnicodeEncodeError - which the except above then caught and reported as a
        # failed send. The mail had already been accepted by the server. A log that
        # lies about delivery is worse than no log at all.
        logger.info("Email sent via SMTP to %s", to_email)


class MockEmailProvider(EmailProvider):
    async def send_email(self, to_email: str, subject: str, text_body: str, html_body: str | None = None):
        print("\n" + "=" * 60)
        print(f" MOCK EMAIL SENT TO: {to_email}")
        print(f"Subject: {subject}")
        # The HTML part is thousands of characters of table markup - noting thatgi
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
    provider, and every test that registered a user fired a real request at the
    mail service. It looked harmless only because send_email swallows its own
    exceptions. Keep the TESTING check first, and keep this resolved per call.

    Constructing a provider is just an object allocation - nothing opens a
    connection until send_email runs - so there is nothing to cache here.
    """
    if settings.TESTING:
        return MockEmailProvider()

    # SMTP is the only real transport. All three parts are required: a host with
    # no credentials would authenticate as nobody, and Gmail rejects that session
    # - a mail outage that surfaces as "nothing happens".
    if settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASS:
        return SMTPEmailProvider()

    print("⚠️ WARNING: No email credentials found. Using MockEmailProvider.")
    return MockEmailProvider()


def describe_email_provider() -> str:
    """
    One line naming the provider that is actually live, for the startup log.

    "Am I actually sending mail" is the first question when a verification link
    never arrives, and the factory above answers it silently - blank out any one
    of the three SMTP settings and every send becomes a no-op without a word.

    Carries no credentials: the host, the port and the sending address only.
    """
    provider = type(get_email_provider()).__name__

    if provider == "SMTPEmailProvider":
        return f"{provider} ({settings.SMTP_HOST}:{settings.SMTP_PORT}, as {settings.SMTP_USER})"
    return f"{provider} (nothing is actually sent)"


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
    """
    Sends the branded password reset email.

    No `name` parameter, unlike the verification mail above: this is queued from
    /forgot-password, which answers with the same generic 200 whether or not the
    address exists. reset-password.html greets the address rather than the person
    for that reason - and because render_template fills an unknown placeholder
    with an empty string, so a {{name}} nobody passes would render as a blank gap.
    """
    reset_url = frontend_link(f"/reset-password?token={token}")
    subject = "Password Reset Request"

    # The plain text version still carries the link on its own, so a missing or
    # unreadable template downgrades the mail instead of breaking the reset.
    text_body = (
        "We received a request to reset your password. Click below to securely change it:\n\n"
        f"{reset_url}\n\n"
        "This link expires in 24 hours.\n"
        "If you didn't ask for a password reset, you can ignore this email."
    )

    html_body = render_template("reset-password.html", {
        "email": email,
        "resetUrl": reset_url,
    })

    await get_email_provider().send_email(
        to_email=email, subject=subject, text_body=text_body, html_body=html_body
    )
