import logging
import smtplib

import pytest

from app.core.config import settings
from app.services.email import frontend_link, render_template

TEMPLATE = "verify-email.html"


# ==========================================
# 1. LINK BUILDING
# ==========================================
def test_links_use_configured_frontend_url(monkeypatch):
    """
    Every link must come from settings.FRONTEND_URL.

    These used to be hardcoded to http://localhost:5173, which meant an email
    sent from a deployed server pointed at the recipient's own machine. That
    fails silently - the mail sends fine, the link just goes nowhere.
    """
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://gym.example")

    verify_url = frontend_link("/verify-email?token=abc")
    reset_url = frontend_link("/reset-password?token=abc")

    assert verify_url == "https://gym.example/verify-email?token=abc"
    assert reset_url == "https://gym.example/reset-password?token=abc"
    assert "localhost" not in verify_url
    assert "localhost" not in reset_url


def test_trailing_slash_does_not_double_up(monkeypatch):
    """A FRONTEND_URL ending in "/" must not produce "//verify-email"."""
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://gym.example/")

    assert frontend_link("/verify-email?token=abc") == "https://gym.example/verify-email?token=abc"


# ==========================================
# 2. TEMPLATE RENDERING
# ==========================================
def test_template_placeholders_are_filled():
    """
    All three placeholders are substituted and none survive into the sent mail.

    The literal "{{" check is the important half: a renamed placeholder would
    otherwise ship to a real member as raw template syntax.
    """
    html = render_template(TEMPLATE, {
        "name": "Dusan",
        "email": "dusan@example.com",
        "verificationUrl": "https://gym.example/verify-email?token=abc",
    })

    assert html is not None
    assert "Dusan" in html
    assert "dusan@example.com" in html
    assert "https://gym.example/verify-email?token=abc" in html
    assert "{{" not in html


def test_user_supplied_values_are_escaped():
    """
    first_name and email are user input, and {{email}} lands inside a mailto:
    href - an unescaped quote or angle bracket breaks straight out of the
    attribute it sits in.
    """
    html = render_template(TEMPLATE, {
        "name": '<script>alert("x")</script>',
        "email": 'quote"inject@example.com',
        "verificationUrl": "https://gym.example/verify-email?token=abc",
    })

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # The raw quote must not survive into the href either
    assert 'quote"inject' not in html
    assert "quote&quot;inject" in html


def test_placeholder_inside_user_input_is_not_expanded():
    """
    Substitution is a single pass, so a value can never be re-scanned.

    Chained .replace() calls would expand this name into the member's own
    verification link, because the name is inserted before the URL pass runs.
    """
    html = render_template(TEMPLATE, {
        "name": "{{verificationUrl}}",
        "email": "dusan@example.com",
        "verificationUrl": "https://gym.example/verify-email?token=secret",
    })

    # The greeting keeps the literal text the user typed...
    assert "{{verificationUrl}}" in html
    # ...and the real link appears only where the template actually asks for it:
    # the button href, the fallback href and the fallback link text.
    assert html.count("https://gym.example/verify-email?token=secret") == 3


def test_missing_template_returns_none_instead_of_raising():
    """
    A missing template must not take down registration. The caller falls back to
    the plain text body, which carries the same link.
    """
    assert render_template("this-template-does-not-exist.html", {}) is None


# ==========================================
# 3. PROVIDER SELECTION
# ==========================================
@pytest.mark.asyncio
async def test_testing_mode_never_reaches_a_live_provider(monkeypatch, capsys):
    """
    Regression: the provider used to be resolved once, at import time, into a
    module-level `email_provider`. conftest imports app.main - which reaches
    app.services.email - BEFORE it sets settings.TESTING = True, so the whole
    suite held a live ResendEmailProvider and every test that registered a user
    fired a real request at the Resend API. send_email swallows its own
    exceptions, so it never showed up as a failure.

    This has to go through send_verification_email rather than calling
    get_email_provider() directly: that function always read TESTING correctly,
    so a direct call passes even with the bug present. The send path is the only
    place the difference is observable.
    """
    from app.services.email import send_verification_email

    # A live key present AND TESTING on - the test flag has to win.
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_live_key_should_be_ignored")
    monkeypatch.setattr(settings, "TESTING", True)

    await send_verification_email("member@example.com", "tok999", "Ana")

    printed = capsys.readouterr().out
    assert "MOCK EMAIL SENT TO: member@example.com" in printed
    assert "Resend" not in printed


# ==========================================
# 4. THE WHOLE MESSAGE
# ==========================================
@pytest.mark.asyncio
async def test_send_verification_email_works_without_a_name(monkeypatch, capsys):
    """
    The name argument is optional and last on purpose.

    This function is queued through BackgroundTasks, so a signature change that
    made the name required would fail AFTER the endpoint had already returned
    200 - somewhere nothing surfaces it to the user.
    """
    from app.services.email import send_verification_email

    monkeypatch.setattr(settings, "FRONTEND_URL", "https://gym.example")

    # Two positional args only, exactly how the old add_task calls invoked it.
    await send_verification_email("member@example.com", "tok123")

    # settings.TESTING is True via conftest, so this went to MockEmailProvider
    # and was printed rather than sent anywhere.
    printed = capsys.readouterr().out
    assert "https://gym.example/verify-email?token=tok123" in printed
    assert "Hi there," in printed
    assert "HTML part: attached" in printed


# ==========================================
# 5. SMTP TRANSPORT
# ==========================================
APP_PASSWORD_AS_GOOGLE_SHOWS_IT = "abcd efgh ijkl mnop"
APP_PASSWORD_JOINED = "abcdefghijklmnop"


class FakeSMTP:
    """
    Stands in for smtplib.SMTP / smtplib.SMTP_SSL and records what was asked of it.

    Every instance appends itself to `created`, so a test can see WHICH class was
    constructed - that is the whole difference between the port 587 and the port
    465 paths, and it is invisible from the message alone.

    `login_error` makes the connection fail the way a bad App Password does,
    without needing a network.
    """

    created: list["FakeSMTP"] = []
    login_error: Exception | None = None

    def __init__(self, host, port, timeout=None, **kwargs):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args = None
        self.message = None
        FakeSMTP.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, *args, **kwargs):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)
        if FakeSMTP.login_error is not None:
            raise FakeSMTP.login_error

    def send_message(self, msg):
        self.message = msg


class FakeSMTP_SSL(FakeSMTP):
    """Same recorder, distinguishable by class - 465 must reach this one."""


@pytest.fixture
def smtp(monkeypatch):
    """
    Wires a Gmail-shaped configuration onto a recording transport.

    SMTPEmailProvider is used directly rather than through get_email_provider(),
    so settings.TESTING (True via conftest) never diverts this to the mock.
    """
    FakeSMTP.created = []
    FakeSMTP.login_error = None

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP_SSL)

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", "gym@gmail.com")
    monkeypatch.setattr(settings, "SMTP_PASS", APP_PASSWORD_AS_GOOGLE_SHOWS_IT)
    monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "FitPass")

    return FakeSMTP


async def send_one() -> FakeSMTP:
    """Sends a message through the provider and hands back the connection it used."""
    from app.services.email import SMTPEmailProvider

    await SMTPEmailProvider().send_email(
        to_email="member@example.com",
        subject="Verify your FitPass Clone Account",
        text_body="plain text fallback",
        html_body="<p>html body</p>",
    )
    assert FakeSMTP.created, "no connection was opened at all"
    return FakeSMTP.created[-1]


@pytest.mark.asyncio
async def test_app_password_spaces_are_stripped(smtp):
    """
    Google displays the App Password in four groups of four, and it is copied out
    of that screen with the spaces still in it.

    smtplib would send them verbatim and Gmail answers 535 "Username and Password
    not accepted" - which reads like a wrong password, so the spaces are the last
    thing anyone suspects.
    """
    connection = await send_one()

    assert connection.login_args == ("gym@gmail.com", APP_PASSWORD_JOINED)


@pytest.mark.asyncio
async def test_from_header_is_the_authenticated_account(smtp, monkeypatch):
    """
    Gmail refuses to relay a From header for an address it does not own.

    EMAIL_FROM defaults to Resend's onboarding@resend.dev, so honouring it here
    would have every message rejected the moment the account switched to Gmail.
    """
    monkeypatch.setattr(settings, "EMAIL_FROM", "onboarding@resend.dev")

    connection = await send_one()

    assert connection.message["From"] == "FitPass <gym@gmail.com>"
    assert "resend.dev" not in connection.message["From"]


@pytest.mark.asyncio
async def test_port_587_uses_starttls(smtp):
    """587 opens in the clear and has to be upgraded before login."""
    connection = await send_one()

    assert type(connection) is FakeSMTP
    assert connection.started_tls is True


@pytest.mark.asyncio
async def test_port_465_uses_implicit_tls(smtp, monkeypatch):
    """
    465 is TLS from the first byte, so there is no plaintext greeting to upgrade.

    Calling starttls() on it does not error - it waits for a response that never
    comes, and the send simply hangs until the timeout.
    """
    monkeypatch.setattr(settings, "SMTP_PORT", 465)

    connection = await send_one()

    assert type(connection) is FakeSMTP_SSL
    assert connection.started_tls is False


@pytest.mark.asyncio
async def test_connection_has_a_timeout(smtp):
    """
    _send_sync runs through asyncio.to_thread, so a connection with no timeout
    holds a threadpool worker for as long as the server stays silent - which,
    with no timeout, is forever.
    """
    connection = await send_one()

    assert connection.timeout is not None
    assert connection.timeout > 0


@pytest.mark.asyncio
async def test_text_part_precedes_html_part(smtp):
    """
    A multipart/alternative client renders the LAST part it understands, so the
    plain text has to come first. Reversed, every modern client shows the plain
    text version and the branded template is never seen.
    """
    connection = await send_one()

    subtypes = [part.get_content_subtype() for part in connection.message.get_payload()]
    assert subtypes == ["plain", "html"]


@pytest.mark.asyncio
async def test_send_failure_is_logged_and_never_raises(smtp, caplog):
    """
    A rejected login must surface in the log and stop there.

    This runs inside a BackgroundTask: /register answered 200 long ago, so raising
    would only produce an unhandled error in a background thread with nobody to
    report it to. Swallowing it silently is the other failure - that is how the
    current setup delivers nothing and says nothing.
    """
    FakeSMTP.login_error = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Username and Password not accepted"
    )

    with caplog.at_level(logging.ERROR, logger="app.services.email"):
        await send_one()  # must not raise

    assert "SMTP send to member@example.com failed" in caplog.text
    # The server's own reply is the useful half - it says WHY.
    assert "535" in caplog.text
    # The password must never reach the log, in either form.
    assert APP_PASSWORD_JOINED not in caplog.text
    assert APP_PASSWORD_AS_GOOGLE_SHOWS_IT not in caplog.text
    # A rejected send must not also announce success.
    assert "Email sent via SMTP" not in caplog.text


@pytest.mark.asyncio
async def test_successful_send_is_never_reported_as_a_failure(smtp, caplog):
    """
    The success notice must sit OUTSIDE the try, and must stay ASCII.

    Regression, found by actually sending one: the line used to be a print with a
    check mark emoji, inside the try. Windows falls back to cp1252 whenever stdout
    is redirected rather than a console, so that character raised
    UnicodeEncodeError - and the except caught it and logged "SMTP send failed"
    for a message the server had already accepted.

    A log that reports a delivered email as lost sends you hunting through Google
    account settings for a problem that does not exist.
    """
    with caplog.at_level(logging.INFO, logger="app.services.email"):
        connection = await send_one()

    # The message really did reach the transport...
    assert connection.message is not None
    # ...and nothing anywhere claimed otherwise.
    assert "Email sent via SMTP to member@example.com" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
