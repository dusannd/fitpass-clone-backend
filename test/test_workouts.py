import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_private_workout_plans_security_and_visibility():
    """
    INTEGRATION TEST (Private Workout Plans):
    Verifies that a trainer can only create private plans for active clients,
    and that private plans are hidden from public endpoints but visible to the client.
    """
    transport = ASGITransport(app=app)

    # Generate unique emails
    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@gym.com"
    client_a_email = f"client_a_{uuid.uuid4().hex[:6]}@gym.com"
    client_b_email = f"client_b_{uuid.uuid4().hex[:6]}@gym.com"
    password = "testpassword123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # --- 1. SETUP: REGISTER USERS ---
        # Create Trainer
        res = await ac.post("/api/users/",
                            json={"email": trainer_email, "password": password, "first_name": "T", "last_name": "T"})
        trainer_id = res.json()["id"]

        # Hire Trainer securely
        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": trainer_email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.clear()

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # Create Client A
        res = await ac.post("/api/users/",
                            json={"email": client_a_email, "password": password, "first_name": "A", "last_name": "A"})
        client_a_id = res.json()["id"]
        res = await ac.post("/api/users/login", json={"email": client_a_email, "password": password})
        client_a_headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # Create Client B (Will remain unaffiliated with the trainer)
        res = await ac.post("/api/users/",
                            json={"email": client_b_email, "password": password, "first_name": "B", "last_name": "B"})
        client_b_id = res.json()["id"]

        # --- 2. LINK TRAINER AND CLIENT A ---
        # Client A requests coaching
        await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_a_headers)

        # Trainer fetches requests and accepts Client A
        res_reqs = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_reqs.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

        # --- 3. TEST SECURITY: TRAINER TRIES TO ASSIGN PLAN TO CLIENT B (UNAUTHORIZED) ---
        private_plan_payload_for_b = {
            "name": "Secret Plan B",
            "client_id": client_b_id,
            "exercises": []
        }
        res_fail = await ac.post("/api/trainer/plans", json=private_plan_payload_for_b, headers=trainer_headers)

        # Assert the system blocks it with 403 Forbidden
        assert res_fail.status_code == 403
        assert "officially accepted clients" in res_fail.json()["detail"]

        # --- 4. TEST SUCCESS: TRAINER ASSIGNS PLAN TO CLIENT A ---
        private_plan_payload_for_a = {
            "name": "Secret Plan A",
            "client_id": client_a_id,
            "exercises": [{"name": "Pushup", "sets": 3, "reps": "10"}]
        }
        res_success = await ac.post("/api/trainer/plans", json=private_plan_payload_for_a, headers=trainer_headers)

        assert res_success.status_code == 200
        assert res_success.json()["client_id"] == client_a_id

        # --- 5. TEST VISIBILITY: PUBLIC PLANS SHOULD NOT SHOW PRIVATE PLANS ---
        res_public = await ac.get(f"/api/workouts/trainers/{trainer_id}/plans", headers=client_a_headers)
        assert res_public.status_code == 200

        # Should be empty because the trainer only created 1 private plan, no public ones
        assert len(res_public.json()) == 0

        # --- 6. TEST VISIBILITY: CLIENT A FETCHES THEIR PRIVATE PLANS ---
        res_private = await ac.get("/api/workouts/my-private-plans", headers=client_a_headers)
        assert res_private.status_code == 200

        private_plans_list = res_private.json()
        assert len(private_plans_list) == 1
        assert private_plans_list[0]["name"] == "Secret Plan A"
        assert private_plans_list[0]["exercises"][0]["name"] == "Pushup"