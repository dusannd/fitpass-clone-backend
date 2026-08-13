import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_private_workout_plans_security_and_visibility(seed_subscription):
    """
    INTEGRATION TEST (Private Workout Plans):
    Verifies that a trainer can only create private plans for active clients,
    and that private plans are hidden from public endpoints but visible to the client.
    """
    transport = ASGITransport(app=app)

    # Generate unique emails
    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    client_a_email = f"client_a_{uuid.uuid4().hex[:6]}@test.com"
    client_b_email = f"client_b_{uuid.uuid4().hex[:6]}@test.com"
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
            app.dependency_overrides.pop(get_current_admin, None)

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # Create Client A
        res = await ac.post("/api/users/",
                            json={"email": client_a_email, "password": password, "first_name": "A", "last_name": "A"})
        client_a_id = res.json()["id"]
        res = await ac.post("/api/users/login", json={"email": client_a_email, "password": password})
        client_a_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # Create Client B (Will remain unaffiliated with the trainer)
        res = await ac.post("/api/users/",
                            json={"email": client_b_email, "password": password, "first_name": "B", "last_name": "B"})
        client_b_id = res.json()["id"]

        # --- 2. LINK TRAINER AND CLIENT A ---
        # Coaching needs a plan that includes personal training. Client B is left
        # without one on purpose - they stay unaffiliated either way.
        await seed_subscription(client_a_id, includes_trainer=True)

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


@pytest.mark.asyncio
async def test_member_can_remove_plans_from_their_library(seed_subscription):
    """
    INTEGRATION TEST (Removing plans):
    A member can unfollow a public plan they saved, and hide a plan their trainer
    assigned - without either action touching the trainer's plan itself.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    client_email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    outsider_email = f"outsider_{uuid.uuid4().hex[:6]}@test.com"
    password = "testpassword123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # --- 1. SETUP: A TRAINER, THEIR CLIENT, AND AN UNRELATED MEMBER ---
        res = await ac.post("/api/users/",
                            json={"email": trainer_email, "password": password, "first_name": "T", "last_name": "T"})
        trainer_id = res.json()["id"]

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": trainer_email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.pop(get_current_admin, None)

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        res = await ac.post("/api/users/",
                            json={"email": client_email, "password": password, "first_name": "C", "last_name": "C"})
        client_id = res.json()["id"]
        res = await ac.post("/api/users/login", json={"email": client_email, "password": password})
        client_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        await ac.post("/api/users/",
                      json={"email": outsider_email, "password": password, "first_name": "O", "last_name": "O"})
        res = await ac.post("/api/users/login", json={"email": outsider_email, "password": password})
        outsider_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # Link the trainer and the client so private plans are allowed. That needs a
        # plan including personal training first.
        await seed_subscription(client_id, includes_trainer=True)

        await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
        res_reqs = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_reqs.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

        # --- 2. UNFOLLOWING A SAVED PUBLIC PLAN ---
        res = await ac.post("/api/trainer/plans", json={
            "name": "Public Push Day",
            "exercises": [{"name": "Bench Press", "sets": 3, "reps": "8"}]
        }, headers=trainer_headers)
        public_plan_id = res.json()["id"]

        await ac.post(f"/api/workouts/{public_plan_id}/follow", headers=client_headers)
        res_saved = await ac.get("/api/workouts/my-plans", headers=client_headers)
        assert len(res_saved.json()) == 1

        res_unfollow = await ac.delete(f"/api/workouts/{public_plan_id}/follow", headers=client_headers)
        assert res_unfollow.status_code == 200

        res_saved = await ac.get("/api/workouts/my-plans", headers=client_headers)
        assert len(res_saved.json()) == 0

        # Unfollowing something you no longer follow is a clean 400, not a crash
        res_again = await ac.delete(f"/api/workouts/{public_plan_id}/follow", headers=client_headers)
        assert res_again.status_code == 400

        # The trainer's plan survived: it is still in the marketplace
        res_public = await ac.get(f"/api/workouts/trainers/{trainer_id}/plans", headers=client_headers)
        assert len(res_public.json()) == 1

        # --- 3. HIDING AN ASSIGNED PRIVATE PLAN ---
        res = await ac.post("/api/trainer/plans", json={
            "name": "Your Personal Plan",
            "client_id": client_id,
            "exercises": [{"name": "Squat", "sets": 3, "reps": "5"}]
        }, headers=trainer_headers)
        private_plan_id = res.json()["id"]

        res_private = await ac.get("/api/workouts/my-private-plans", headers=client_headers)
        assert len(res_private.json()) == 1

        res_dismiss = await ac.post(f"/api/workouts/{private_plan_id}/dismiss", headers=client_headers)
        assert res_dismiss.status_code == 200

        res_private = await ac.get("/api/workouts/my-private-plans", headers=client_headers)
        assert len(res_private.json()) == 0

        # Dismissing twice must stay a no-op instead of tripping the primary key
        res_twice = await ac.post(f"/api/workouts/{private_plan_id}/dismiss", headers=client_headers)
        assert res_twice.status_code == 200

        # The trainer still has the plan - hiding is the member's view only
        res_trainer_plans = await ac.get("/api/trainer/plans", headers=trainer_headers)
        assert any(p["id"] == private_plan_id for p in res_trainer_plans.json())

        # --- 4. UNDO ---
        res_restore = await ac.delete(f"/api/workouts/{private_plan_id}/dismiss", headers=client_headers)
        assert res_restore.status_code == 200

        res_private = await ac.get("/api/workouts/my-private-plans", headers=client_headers)
        assert len(res_private.json()) == 1

        # --- 5. SECURITY: YOU CANNOT HIDE SOMEBODY ELSE'S ASSIGNED PLAN ---
        res_forbidden = await ac.post(f"/api/workouts/{private_plan_id}/dismiss", headers=outsider_headers)
        assert res_forbidden.status_code == 403

        # And the owner's view is unaffected by that attempt
        res_private = await ac.get("/api/workouts/my-private-plans", headers=client_headers)
        assert len(res_private.json()) == 1