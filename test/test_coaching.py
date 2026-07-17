import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


# Temporarily override admin dependency just to hire the trainer
async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_coaching_request_flow():
    """
    INTEGRATION TEST (End-to-End):
    1. Register User A and promote to Trainer.
    2. Register User B as a standard Member.
    3. User B sends a coaching request to User A.
    4. User A views pending requests and accepts User B.
    5. User A views their active client list.
    """
    transport = ASGITransport(app=app)

    # Generate unique emails for this test run
    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    member_email = f"member_{uuid.uuid4().hex[:6]}@test.com"
    password = "testpassword123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # --- SETUP: CREATE TRAINER ---
        res_trainer_reg = await ac.post("/api/users/", json={
            "email": trainer_email, "password": password,
            "first_name": "Pro", "last_name": "Trainer"
        })
        trainer_id = res_trainer_reg.json()["id"]

        # Promote User A to trainer (Safely handling overrides)
        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": trainer_email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.clear()
            # Login Trainer to get their JWT Token
        res_trainer_login = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_token = res_trainer_login.json()["access_token"]
        trainer_headers = {"Authorization": f"Bearer {trainer_token}"}

        # --- SETUP: CREATE MEMBER ---
        res_member_reg = await ac.post("/api/users/", json={
            "email": member_email, "password": password,
            "first_name": "Gym", "last_name": "Bro"
        })
        member_id = res_member_reg.json()["id"]

        # Login Member to get their JWT Token
        res_member_login = await ac.post("/api/users/login", json={"email": member_email, "password": password})
        member_token = res_member_login.json()["access_token"]
        member_headers = {"Authorization": f"Bearer {member_token}"}

        # --- STEP 1: MEMBER SENDS COACHING REQUEST ---
        res_request = await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)
        assert res_request.status_code == 200
        assert res_request.json()["status"] == "success"

        # Ensure duplicate requests are blocked
        res_duplicate = await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)
        assert res_duplicate.status_code == 400

        # --- STEP 2: TRAINER VIEWS PENDING REQUESTS ---
        res_pending = await ac.get("/api/coaching/requests", headers=trainer_headers)
        assert res_pending.status_code == 200
        pending_list = res_pending.json()
        assert len(pending_list) == 1

        request_id = pending_list[0]["id"]
        assert pending_list[0]["client_id"] == member_id
        assert pending_list[0]["status"] == "PENDING"

        # --- STEP 3: TRAINER ACCEPTS THE REQUEST ---
        res_accept = await ac.put(
            f"/api/coaching/requests/{request_id}",
            json={"status": "ACCEPTED"},
            headers=trainer_headers
        )
        assert res_accept.status_code == 200

        # --- STEP 4: TRAINER VIEWS ACCEPTED CLIENTS ---
        res_clients = await ac.get("/api/coaching/clients", headers=trainer_headers)
        assert res_clients.status_code == 200
        clients_list = res_clients.json()

        # Verify the member is now officially in the trainer's client list
        assert len(clients_list) == 1
        assert clients_list[0]["client_id"] == member_id
        assert clients_list[0]["status"] == "ACCEPTED"
        assert clients_list[0]["client"]["first_name"] == "Gym"  # Checking nested relationship