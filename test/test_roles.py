import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin

# A trainer-only route that answers 200 with an empty list for a fresh trainer.
TRAINER_ROUTE = "/api/coaching/requests"


async def override_get_current_admin():
    return 1


async def register_and_login(ac: AsyncClient) -> tuple[str, dict]:
    """Creates a member and returns their email and the login cookie."""
    email = f"roles_{uuid.uuid4().hex[:8]}@test.com"
    password = "password123"
    res = await ac.post("/api/users/", json={
        "email": email, "password": password, "first_name": "R", "last_name": "R",
    })
    assert res.status_code in (200, 201), res.text

    res = await ac.post("/api/users/login", json={"email": email, "password": password})
    return email, {"Cookie": f"access_token={res.cookies['access_token']}"}


@pytest.mark.asyncio
async def test_granted_role_works_without_logging_in_again():
    """
    A role an admin grants must work on the session the person already has.

    The token's "roles" claim is frozen at login. RequireRole used to demand the role
    in that claim as well as in the database, so a freshly hired trainer got 403 on
    every trainer route until they logged out and back in.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        email, headers = await register_and_login(ac)

        # Plain member for now: the trainer route is refused.
        res = await ac.get(TRAINER_ROUTE, headers=headers)
        assert res.status_code == 403

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            res = await ac.post("/api/admin/hr/hire", json={"email": email, "role_name": "trainer"})
            assert res.status_code == 200, res.text
        finally:
            app.dependency_overrides.pop(get_current_admin, None)

        # SAME cookie, issued before the hire.
        res = await ac.get(TRAINER_ROUTE, headers=headers)
        assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_revoked_role_stops_working_at_once():
    """The other direction must keep working: a revoked role is refused immediately."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        email, _ = await register_and_login(ac)

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": email, "role_name": "trainer"})

            # Log in AFTER the hire, so the token itself carries "trainer".
            res = await ac.post("/api/users/login", json={"email": email, "password": "password123"})
            headers = {"Cookie": f"access_token={res.cookies['access_token']}"}
            assert (await ac.get(TRAINER_ROUTE, headers=headers)).status_code == 200

            res = await ac.post("/api/admin/hr/fire", json={"email": email, "role_name": "trainer"})
            assert res.status_code == 200, res.text
        finally:
            app.dependency_overrides.pop(get_current_admin, None)

        res = await ac.get(TRAINER_ROUTE, headers=headers)
        assert res.status_code == 403
