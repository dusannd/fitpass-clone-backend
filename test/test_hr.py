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
            # CRITICAL: Drop ONLY the admin override! clear() would also wipe the
            # get_db override from conftest, and then every test after this one
            # would write into the REAL database instead of the test one.
            app.dependency_overrides.pop(get_current_admin, None)


@pytest.mark.asyncio
async def test_staff_list_contains_only_staff():
    """
    REGRESSION TEST: GET /admin/hr/staff must return people by their ROLE, not by
    where they happen to sit in the users table.

    The panel used to read GET /users/ (which caps at 50 rows) and filter in the
    browser, so a trainer registered after the first 50 users disappeared from the
    HR list entirely. Filtering server-side is what this test locks in: a plain
    member must never appear, and a promoted user must appear the moment they are
    hired - and drop off the moment they are fired.
    """
    transport = ASGITransport(app=app)

    # Two fresh accounts: one stays a plain member, one gets promoted.
    member_email = f"plain_{uuid.uuid4().hex[:6]}@gym.com"
    staff_email = f"staff_{uuid.uuid4().hex[:6]}@gym.com"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for email, last_name in ((member_email, "Member"), (staff_email, "Staff")):
            res = await ac.post("/api/users/", json={
                "email": email,
                "password": "strongpassword123",
                "first_name": "Test",
                "last_name": last_name,
            })
            assert res.status_code == 200, f"Could not register {email}"

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin

            # STEP 1: Promote the second account to trainer
            hire_payload = {"email": staff_email, "role_name": "trainer"}
            assert (await ac.post("/api/admin/hr/hire", json=hire_payload)).status_code == 200

            # STEP 2: The trainer is listed, the plain member is not
            res_staff = await ac.get("/api/admin/hr/staff")
            assert res_staff.status_code == 200

            staff = res_staff.json()
            emails = [row["email"] for row in staff]

            assert staff_email in emails, "A hired trainer is missing from the staff list"
            assert member_email not in emails, "A plain member leaked into the staff list"

            # STEP 3: Nobody in the list is there without a staff role. This is the
            # real guard - a query that forgot its filter would still pass STEP 2.
            for row in staff:
                role_names = {r["name"] for r in row["roles"]}
                assert role_names, f"{row['email']} came back with no roles at all"
                assert role_names & {"admin", "worker", "trainer"}, \
                    f"{row['email']} is in the staff list holding only {role_names}"

            # STEP 4: Each person appears exactly once, even holding member+trainer
            assert emails.count(staff_email) == 1, "Staff member duplicated by the role join"

            # STEP 5: Firing them takes them straight back off the list
            fire_payload = {"email": staff_email, "role_name": "trainer"}
            assert (await ac.post("/api/admin/hr/fire", json=fire_payload)).status_code == 200

            res_after = await ac.get("/api/admin/hr/staff")
            assert staff_email not in [row["email"] for row in res_after.json()], \
                "A fired trainer is still listed as staff"

        finally:
            app.dependency_overrides.pop(get_current_admin, None)