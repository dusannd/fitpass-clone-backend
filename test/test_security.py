import pytest
import uuid
from fastapi import BackgroundTasks
from httpx import AsyncClient, ASGITransport

from app.main import app

FORGOT_PASSWORD_URL = "/api/users/forgot-password"


async def register_user(ac: AsyncClient) -> str:
    """
    Creates a real account and returns its email.

    The uuid suffix matters: the in-memory database is shared across the whole
    session, so a fixed address would collide with whatever another module
    registered earlier.
    """
    email = f"reset_{uuid.uuid4().hex[:8]}@example.com"
    res = await ac.post("/api/users/", json={
        "email": email,
        "password": "correcthorsebattery",
        "first_name": "Reset",
        "last_name": "Tester",
    })
    assert res.status_code in (200, 201), res.text
    return email


@pytest.mark.asyncio
async def test_honeypot_blocks_bot_registration():
    """
    Test that the Register endpoint successfully catches and blocks
    requests where the hidden 'extra_info' honeypot field is filled.
    """
    transport = ASGITransport(app=app)

    bot_payload = {
        "email": f"bot_{uuid.uuid4().hex[:6]}@spam.com",
        "password": "botpassword123",
        "first_name": "Spam",
        "last_name": "Bot",
        "extra_info": "I am a bot filling out all fields!"  # <--- The trap is triggered
    }

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/users/", json=bot_payload)

    # Verify the request was blocked with 400 Bad Request
    assert response.status_code == 400

    # Verify the specific JSON error response requirements
    data = response.json()
    assert data["code"] == "HONEYPOT_TRIGGERED"
    assert data["message"] == "Invalid request."


# ==========================================
# PASSWORD RESET: EMAIL ENUMERATION
# ==========================================
@pytest.mark.asyncio
async def test_forgot_password_queues_email_instead_of_awaiting_it(monkeypatch):
    """
    Regression: /forgot-password used to `await send_password_reset_email(...)`.

    The response body is deliberately generic so nobody can tell a registered
    address from an unregistered one - but in production that await is a live
    HTTPS round trip to Resend. A known address answered in a few hundred
    milliseconds and an unknown one in a few, so the whole protection could be
    defeated with a stopwatch and a wordlist.

    Queueing the send is what makes both branches return at the same speed, so
    that is what this pins. Note it asserts the mail was QUEUED, not how long
    the call took: httpx runs the ASGI app to completion and Starlette executes
    background tasks inside that call, so a wall-clock assertion here would
    measure the same elapsed time before and after the fix - it would pass for
    free and prove nothing.
    """
    queued: list[str] = []
    original_add_task = BackgroundTasks.add_task

    def spy_add_task(self, func, *args, **kwargs):
        queued.append(getattr(func, "__name__", repr(func)))
        return original_add_task(self, func, *args, **kwargs)

    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = await register_user(ac)
        queued.clear()  # drop the verification mail that registration queued

        res = await ac.post(FORGOT_PASSWORD_URL, json={"email": email})

    assert res.status_code == 200
    assert "send_password_reset_email" in queued


@pytest.mark.asyncio
async def test_forgot_password_response_is_identical_for_unknown_email():
    """
    Status code and body must be byte-for-byte the same either way, or the
    timing fix above is pointless - the response itself would give it away.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        registered = await register_user(ac)

        known = await ac.post(FORGOT_PASSWORD_URL, json={"email": registered})
        unknown = await ac.post(FORGOT_PASSWORD_URL, json={
            "email": f"ghost_{uuid.uuid4().hex[:8]}@example.com"
        })

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


@pytest.mark.asyncio
async def test_forgot_password_only_mails_registered_users(monkeypatch):
    """
    The refactor must not have broken the feature itself: a real account still
    gets its link, a made-up address still gets nothing.

    Patches the name in app.api.users, not in app.services.email - users.py
    imports the function directly, so it holds its own reference and patching
    the source module would miss it entirely.
    """
    sent_to: list[str] = []

    async def fake_send(email: str, token: str):
        sent_to.append(email)

    monkeypatch.setattr("app.api.users.send_password_reset_email", fake_send)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        registered = await register_user(ac)

        await ac.post(FORGOT_PASSWORD_URL, json={"email": registered})
        assert sent_to == [registered]

        await ac.post(FORGOT_PASSWORD_URL, json={
            "email": f"ghost_{uuid.uuid4().hex[:8]}@example.com"
        })
        # Still just the one - the unknown address queued nothing.
        assert sent_to == [registered]


@pytest.mark.asyncio
async def test_honeypot_blocks_bot_login():
    """
    Test that the Login endpoint blocks requests with the honeypot field filled.
    """
    transport = ASGITransport(app=app)

    bot_login_payload = {
        "email": "legituser@gmail.com",
        "password": "legitpassword",
        "extra_info": "http://spam-link-in-hidden-field.com"  # <--- The trap is triggered
    }

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/users/login", json=bot_login_payload)

    # Verify the request was blocked with 400 Bad Request
    assert response.status_code == 400

    # Verify the specific JSON error response requirements
    data = response.json()
    assert data["code"] == "HONEYPOT_TRIGGERED"
    assert data["message"] == "Invalid request."