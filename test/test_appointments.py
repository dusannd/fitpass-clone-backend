import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_appointment_scheduling_flow():
    """
    INTEGRATION TEST (Appointments):
    1. Register Trainer and Client.
    2. Link them (Client requests, Trainer accepts).
    3. Client schedules an appointment.
    4. Trainer views it and marks it as COMPLETED with notes.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    client_email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    password = "password123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # --- 1. SETUP USERS & ROLES ---
        # Trainer
        res = await ac.post("/api/users/",
                            json={"email": trainer_email, "password": password, "first_name": "T", "last_name": "T"})
        trainer_id = res.json()["id"]

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": trainer_email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.clear()

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # Client
        res = await ac.post("/api/users/",
                            json={"email": client_email, "password": password, "first_name": "C", "last_name": "C"})
        client_id = res.json()["id"]
        res = await ac.post("/api/users/login", json={"email": client_email, "password": password})
        client_headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # --- 2. LINK TRAINER AND CLIENT ---
        await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
        res_reqs = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_reqs.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

        # --- 3. TEST FAIL: UNAUTHORIZED APPOINTMENT ---
        # Try to schedule with a fake trainer ID
        start_time = datetime.now(timezone.utc) + timedelta(days=1)
        end_time = start_time + timedelta(hours=1)

        payload = {
            "trainer_id": 9999,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }
        res_fail = await ac.post("/api/coaching/appointments", json=payload, headers=client_headers)
        assert res_fail.status_code == 403

        # --- 4. TEST SUCCESS: CLIENT SCHEDULES APPOINTMENT ---
        payload["trainer_id"] = trainer_id
        res_success = await ac.post("/api/coaching/appointments", json=payload, headers=client_headers)

        assert res_success.status_code == 200
        appointment_id = res_success.json()["id"]
        assert res_success.json()["status"] == "SCHEDULED"

        # --- 5. TEST: TRAINER VIEWS AND COMPLETES APPOINTMENT ---
        # Trainer gets schedule
        res_schedule = await ac.get("/api/coaching/appointments/trainer", headers=trainer_headers)
        assert len(res_schedule.json()) == 1

        # Trainer updates status
        update_payload = {
            "status": "COMPLETED",
            "notes": "Great leg day, hit a new PR!"
        }
        res_update = await ac.put(f"/api/coaching/appointments/{appointment_id}", json=update_payload,
                                  headers=trainer_headers)

        assert res_update.status_code == 200
        assert res_update.json()["status"] == "COMPLETED"
        assert res_update.json()["notes"] == "Great leg day, hit a new PR!"