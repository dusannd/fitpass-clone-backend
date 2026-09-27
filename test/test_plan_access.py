import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin

PASSWORD = "password123"


async def override_get_current_admin():
    return 1


async def register(ac: AsyncClient, prefix: str) -> tuple[int, dict]:
    email = f"{prefix}_{uuid.uuid4().hex[:6]}@test.com"
    res = await ac.post("/api/users/", json={"email": email, "password": PASSWORD, "first_name": "X", "last_name": "Y"})
    user_id = res.json()["id"]
    if prefix == "trainer":
        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.pop(get_current_admin, None)
    res = await ac.post("/api/users/login", json={"email": email, "password": PASSWORD})
    return user_id, {"Cookie": f"access_token={res.cookies['access_token']}"}


async def private_plan_setup(ac: AsyncClient, seed_subscription):
    """
    A trainer, their client, a private plan built for that client, and a stranger.

    Returns (plan, client_headers, stranger_headers).
    """
    trainer_id, trainer_headers = await register(ac, "trainer")
    client_id, client_headers = await register(ac, "client")
    _, stranger_headers = await register(ac, "stranger")

    await seed_subscription(client_id, includes_trainer=True)
    await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
    res = await ac.get("/api/coaching/requests", headers=trainer_headers)
    link_id = next(r["id"] for r in res.json() if r["client_id"] == client_id)
    await ac.put(f"/api/coaching/requests/{link_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

    res = await ac.post("/api/trainer/plans", json={
        "name": "Private rehab plan",
        "description": "Only for this client",
        "client_id": client_id,
        "exercises": [{"name": "Secret Rehab Move", "sets": 3, "reps": "12", "instructions": "Knee protocol"}],
    }, headers=trainer_headers)
    assert res.status_code == 200, res.text
    return res.json(), client_headers, stranger_headers


@pytest.mark.asyncio
async def test_a_private_plan_cannot_be_followed_by_someone_else(seed_subscription):
    """Following by id handed another member's private plan over through /my-plans."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        plan, client_headers, stranger_headers = await private_plan_setup(ac, seed_subscription)

        res = await ac.post(f"/api/workouts/{plan['id']}/follow", headers=stranger_headers)
        assert res.status_code == 404

        res = await ac.get("/api/workouts/my-plans", headers=stranger_headers)
        assert all(p["id"] != plan["id"] for p in res.json())

        # The client it was made for already has it - no duplicate "saved" copy.
        res = await ac.post(f"/api/workouts/{plan['id']}/follow", headers=client_headers)
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_logging_cannot_read_or_reach_other_plans(seed_subscription):
    """
    log-session checked nothing, and its response embeds each set's exercise - so it
    doubled as a way to read exercises out of other members' private plans.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        plan, client_headers, stranger_headers = await private_plan_setup(ac, seed_subscription)
        exercise_id = plan["exercises"][0]["id"]

        # A stranger logging against someone else's private plan.
        res = await ac.post("/api/workouts/log-session", json={
            "plan_id": plan["id"], "notes": "",
            "exercises": [{"exercise_id": exercise_id, "set_number": 1, "reps_completed": "12", "weight_kg": None}],
        }, headers=stranger_headers)
        assert res.status_code == 404
        assert "Secret Rehab Move" not in res.text

        # A public plan of their own choosing, with that private exercise smuggled in.
        _, trainer_headers = await register(ac, "trainer")
        res = await ac.post("/api/trainer/plans", json={
            "name": "Public plan", "description": "",
            "exercises": [{"name": "Squat", "sets": 3, "reps": "5"}],
        }, headers=trainer_headers)
        public_plan_id = res.json()["id"]

        res = await ac.post("/api/workouts/log-session", json={
            "plan_id": public_plan_id, "notes": "",
            "exercises": [{"exercise_id": exercise_id, "set_number": 1, "reps_completed": "12", "weight_kg": None}],
        }, headers=stranger_headers)
        assert res.status_code == 400
        assert "Secret Rehab Move" not in res.text

        # The client it belongs to logs it normally.
        res = await ac.post("/api/workouts/log-session", json={
            "plan_id": plan["id"], "notes": "",
            "exercises": [{"exercise_id": exercise_id, "set_number": 1, "reps_completed": "12", "weight_kg": None}],
        }, headers=client_headers)
        assert res.status_code == 200, res.text

        # An empty session is not a session.
        res = await ac.post("/api/workouts/log-session", json={
            "plan_id": plan["id"], "notes": "", "exercises": [],
        }, headers=client_headers)
        assert res.status_code == 400
