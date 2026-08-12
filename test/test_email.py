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
