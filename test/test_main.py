import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_health_check():
    """
    Test 1: Check if the application starts properly and the health endpoint works.
    """
    # Use ASGITransport which is the new standard for testing FastAPI with httpx
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Gym API is running"}


@pytest.mark.asyncio
async def test_admin_analytics_unauthorized():
    """
    Test 2: Ensure that hitting an admin endpoint WITHOUT a token
    correctly blocks the user.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/admin/analytics/today")

    # We expect the API to securely block access (401 Unauthorized or 403 Forbidden)
    assert response.status_code in [401, 403]