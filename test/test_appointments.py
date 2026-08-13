import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_appointment_scheduling_flow(backdate_appointment, seed_subscription):
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
            app.dependency_overrides.pop(get_current_admin, None)

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # Client
        res = await ac.post("/api/users/",
                            json={"email": client_email, "password": password, "first_name": "C", "last_name": "C"})
        client_id = res.json()["id"]
        res = await ac.post("/api/users/login", json={"email": client_email, "password": password})
        client_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # --- 2. LINK TRAINER AND CLIENT ---
        # Coaching needs a plan that includes personal training, so the client is
        # given one first. This test is about the appointment rules, not the perk.
        await seed_subscription(client_id, includes_trainer=True)

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

        # --- 5. TEST: TRAINER SEES THE APPOINTMENT ---
        res_schedule = await ac.get("/api/coaching/appointments/trainer", headers=trainer_headers)
        assert len(res_schedule.json()) == 1

        # --- 6. TEST FAIL: CANNOT COMPLETE A SESSION THAT HASN'T STARTED ---
        # The appointment above is tomorrow, so closing it out now must be refused.
        update_payload = {
            "status": "COMPLETED",
            "notes": "Great leg day, hit a new PR!"
        }
        res_early = await ac.put(f"/api/coaching/appointments/{appointment_id}", json=update_payload,
                                 headers=trainer_headers)

        assert res_early.status_code == 400
        assert "hasn't happened yet" in res_early.json()["detail"]

        # --- 7. TEST SUCCESS: COMPLETING A SESSION THAT IS OVER ---
        # Backdate the row, because the API (correctly) won't let us book the past.
        await backdate_appointment(appointment_id)

        res_update = await ac.put(f"/api/coaching/appointments/{appointment_id}", json=update_payload,
                                  headers=trainer_headers)

        assert res_update.status_code == 200
        assert res_update.json()["status"] == "COMPLETED"
        assert res_update.json()["notes"] == "Great leg day, hit a new PR!"


@pytest.mark.asyncio
async def test_cancelling_a_future_session_is_allowed(seed_subscription):
    """
    The COMPLETED guard must not leak into CANCELLED: cancelling a session that
    hasn't happened yet is the normal, expected case.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    client_email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    password = "password123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # --- 1. SETUP: TRAINER + CLIENT, LINKED ---
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

        # Coaching needs a plan that includes personal training.
        await seed_subscription(client_id, includes_trainer=True)

        await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
        res_reqs = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_reqs.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

        # --- 2. BOOK A FUTURE SESSION ---
        start_time = datetime.now(timezone.utc) + timedelta(days=3)
        res_appt = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": start_time.isoformat(),
            "end_time": (start_time + timedelta(hours=1)).isoformat()
        }, headers=client_headers)
        assert res_appt.status_code == 200
        appointment_id = res_appt.json()["id"]

        # --- 3. CANCELLING IT IS FINE, EVEN THOUGH IT IS IN THE FUTURE ---
        res_cancel = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                                  json={"status": "CANCELLED", "notes": "Client is travelling."},
                                  headers=trainer_headers)

        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_status_change_preserves_existing_notes(backdate_appointment, seed_subscription):
    """
    Feedback is shown to the member as "Trainer's Note", so a later status change
    must not erase it. Omitting the key leaves it; an explicit null clears it.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    client_email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    password = "password123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # --- 1. SETUP: TRAINER + CLIENT, LINKED ---
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

        # Coaching needs a plan that includes personal training.
        await seed_subscription(client_id, includes_trainer=True)

        await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
        res_reqs = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_reqs.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

        # --- 2. BOOK, BACKDATE, THEN COMPLETE WITH FEEDBACK ---
        start_time = datetime.now(timezone.utc) + timedelta(days=1)
        res_appt = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": start_time.isoformat(),
            "end_time": (start_time + timedelta(hours=1)).isoformat()
        }, headers=client_headers)
        appointment_id = res_appt.json()["id"]

        await backdate_appointment(appointment_id)

        res_done = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                                json={"status": "COMPLETED", "notes": "Squat form much improved."},
                                headers=trainer_headers)
        assert res_done.json()["notes"] == "Squat form much improved."

        # --- 3. A STATUS CHANGE WITH NO NOTES KEY MUST LEAVE THEM ALONE ---
        res_keep = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                                json={"status": "CANCELLED"},
                                headers=trainer_headers)

        assert res_keep.status_code == 200
        assert res_keep.json()["status"] == "CANCELLED"
        assert res_keep.json()["notes"] == "Squat form much improved."

        # --- 4. AN EXPLICIT NULL IS A DELIBERATE ERASE, SO IT STILL WORKS ---
        res_clear = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                                 json={"status": "CANCELLED", "notes": None},
                                 headers=trainer_headers)

        assert res_clear.status_code == 200
        assert res_clear.json()["notes"] is None


@pytest.mark.asyncio
async def test_booking_horizon_is_enforced(seed_subscription):
    """
    A member must not be able to park a slot years out. 60 days is the cap, so a
    booking 61 days ahead is rejected while one inside the window succeeds.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    client_email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    password = "password123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # --- 1. SETUP: TRAINER + CLIENT, LINKED ---
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

        # Coaching needs a plan that includes personal training.
        await seed_subscription(client_id, includes_trainer=True)

        await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
        res_reqs = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_reqs.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)

        # --- 2. TOO FAR OUT: REJECTED ---
        too_far = datetime.now(timezone.utc) + timedelta(days=61)
        res_far = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": too_far.isoformat(),
            "end_time": (too_far + timedelta(hours=1)).isoformat()
        }, headers=client_headers)

        assert res_far.status_code == 400
        assert "60 days" in res_far.json()["detail"]

        # --- 3. JUST INSIDE THE WINDOW: ACCEPTED ---
        inside = datetime.now(timezone.utc) + timedelta(days=59)
        res_ok = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": inside.isoformat(),
            "end_time": (inside + timedelta(hours=1)).isoformat()
        }, headers=client_headers)

        assert res_ok.status_code == 200