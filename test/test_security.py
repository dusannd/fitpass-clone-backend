import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app


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