import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


# 1. MOCKING (Dependency Override)
# This securely forces the FastAPI dependency to return an admin ID during tests ONLY.
# It simulates an authenticated Admin without needing to generate a real JWT token.
async def override_get_current_admin():
    return 1  # Simulate Admin User ID 1




@pytest.mark.asyncio
async def test_hr_hiring_and_firing_flow():
    """
    INTEGRATION TEST: Verifies the entire workflow of creating a standard user,
    promoting them to a 'trainer' via the HR panel, and demoting them back.
    """
    transport = ASGITransport(app=app)

    # Use UUID to generate a unique random email for every test run
    # preventing "Email already registered" 400 errors.
    random_email = f"testuser_{uuid.uuid4().hex[:6]}@gym.com"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # STEP 1: Register a new standard user
        user_data = {
            "email": random_email,
            "password": "strongpassword123",
            "first_name": "Test",
            "last_name": "Worker"
        }
        res_register = await ac.post("/api/users/", json=user_data)
        assert res_register.status_code == 200, "User was not created successfully"

        # Safely wrap admin actions in try...finally so overrides don't leak
        try:
            # Temporarily make ourselves admin
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            # STEP 2: Admin hires this user as a 'trainer'
            hire_payload = {"email": random_email, "role_name": "trainer"}
            res_hire = await ac.post("/api/admin/hr/hire", json=hire_payload)
            assert res_hire.status_code == 200
            assert "successfully hired" in res_hire.json()["message"]

            # Verify system blocks duplicate hiring
            res_hire_again = await ac.post("/api/admin/hr/hire", json=hire_payload)
            assert res_hire_again.status_code == 400

            # STEP 3: Admin fires the trainer (revokes role)
            fire_payload = {"email": random_email, "role_name": "trainer"}
            res_fire = await ac.post("/api/admin/hr/fire", json=fire_payload)
            assert res_fire.status_code == 200

            # STEP 4: Ensure system blocks removal of the foundational 'member' role
            fire_member_payload = {"email": random_email, "role_name": "member"}
            res_fire_member = await ac.post("/api/admin/hr/fire", json=fire_member_payload)
            assert res_fire_member.status_code == 400

        finally:
            # CRITICAL: Always clear overrides so they don't break other tests!
            app.dependency_overrides.clear()