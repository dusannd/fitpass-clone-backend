import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_appointment_scheduling_flow(backdate_appointment, seed_subscription, gym_slot):
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
        start_time = gym_slot(1)
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
async def test_cancelling_a_future_session_is_allowed(seed_subscription, gym_slot):
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
        start_time = gym_slot(3)
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
async def test_status_change_preserves_existing_notes(backdate_appointment, seed_subscription, gym_slot):
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
        start_time = gym_slot(1)
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

        # --- 3. AN UPDATE WITH NO NOTES KEY MUST LEAVE THEM ALONE ---
        # Same status again: a closed session can no longer change status, but the
        # trainer can still resend it to edit the feedback.
        res_keep = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                                json={"status": "COMPLETED"},
                                headers=trainer_headers)

        assert res_keep.status_code == 200
        assert res_keep.json()["status"] == "COMPLETED"
        assert res_keep.json()["notes"] == "Squat form much improved."

        # --- 4. AN EXPLICIT NULL IS A DELIBERATE ERASE, SO IT STILL WORKS ---
        res_clear = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                                 json={"status": "COMPLETED", "notes": None},
                                 headers=trainer_headers)

        assert res_clear.status_code == 200
        assert res_clear.json()["notes"] is None


@pytest.mark.asyncio
async def test_booking_horizon_is_enforced(seed_subscription, gym_slot):
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
        too_far = gym_slot(61)
        res_far = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": too_far.isoformat(),
            "end_time": (too_far + timedelta(hours=1)).isoformat()
        }, headers=client_headers)

        assert res_far.status_code == 400
        assert "60 days" in res_far.json()["detail"]

        # --- 3. JUST INSIDE THE WINDOW: ACCEPTED ---
        inside = gym_slot(59)
        res_ok = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": inside.isoformat(),
            "end_time": (inside + timedelta(hours=1)).isoformat()
        }, headers=client_headers)

        assert res_ok.status_code == 200


# ==========================================
# BOOKING RULES + MEMBER CANCELLATION
# ==========================================

PASSWORD = "password123"


async def make_trainer(ac: AsyncClient) -> tuple[int, dict]:
    """Registers a user, hires them as a trainer, and returns (id, cookie headers)."""
    email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    res = await ac.post("/api/users/", json={"email": email, "password": PASSWORD, "first_name": "T", "last_name": "T"})
    trainer_id = res.json()["id"]
    try:
        app.dependency_overrides[get_current_admin] = override_get_current_admin
        await ac.post("/api/admin/hr/hire", json={"email": email, "role_name": "trainer"})
    finally:
        app.dependency_overrides.pop(get_current_admin, None)
    res = await ac.post("/api/users/login", json={"email": email, "password": PASSWORD})
    return trainer_id, {"Cookie": f"access_token={res.cookies['access_token']}"}


async def make_member(ac: AsyncClient, seed_subscription) -> tuple[int, dict]:
    """Registers a member whose plan includes personal training."""
    email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    res = await ac.post("/api/users/", json={"email": email, "password": PASSWORD, "first_name": "C", "last_name": "C"})
    client_id = res.json()["id"]
    await seed_subscription(client_id, includes_trainer=True)
    res = await ac.post("/api/users/login", json={"email": email, "password": PASSWORD})
    return client_id, {"Cookie": f"access_token={res.cookies['access_token']}"}


async def link(ac: AsyncClient, trainer: tuple[int, dict], client_headers: dict) -> None:
    """Member requests the trainer, trainer accepts."""
    trainer_id, trainer_headers = trainer
    await ac.post(f"/api/coaching/request/{trainer_id}", headers=client_headers)
    res = await ac.get("/api/coaching/requests", headers=trainer_headers)
    request_id = res.json()[0]["id"]
    await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_headers)


async def book(ac: AsyncClient, trainer_id: int, client_headers: dict, start: datetime, hours: float = 1):
    return await ac.post("/api/coaching/appointments", json={
        "trainer_id": trainer_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=hours)).isoformat(),
    }, headers=client_headers)


@pytest.mark.asyncio
async def test_a_closed_session_cannot_change_status(backdate_appointment, seed_subscription, gym_slot):
    """COMPLETED and CANCELLED are final - a finished session must not become CANCELLED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        trainer = await make_trainer(ac)
        _, client_headers = await make_member(ac, seed_subscription)
        await link(ac, trainer, client_headers)

        appointment_id = (await book(ac, trainer[0], client_headers, gym_slot(2))).json()["id"]
        await backdate_appointment(appointment_id)

        res = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                           json={"status": "COMPLETED"}, headers=trainer[1])
        assert res.status_code == 200

        res = await ac.put(f"/api/coaching/appointments/{appointment_id}",
                           json={"status": "CANCELLED"}, headers=trainer[1])
        assert res.status_code == 409
        assert "already completed" in res.json()["detail"]


@pytest.mark.asyncio
async def test_member_cannot_double_book_across_trainers(seed_subscription, gym_slot):
    """Only the trainer's calendar used to be checked, so the same hour could be booked twice."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await make_trainer(ac)
        second = await make_trainer(ac)
        _, client_headers = await make_member(ac, seed_subscription)
        await link(ac, first, client_headers)
        await link(ac, second, client_headers)

        start = gym_slot(4, hour=10)
        assert (await book(ac, first[0], client_headers, start)).status_code == 200

        # Half an hour into the first session, with the other trainer.
        res = await book(ac, second[0], client_headers, start + timedelta(minutes=30))
        assert res.status_code == 409
        assert "You already have" in res.json()["detail"]

        # Right after it is fine - touching ends do not overlap.
        assert (await book(ac, second[0], client_headers, start + timedelta(hours=1))).status_code == 200


@pytest.mark.asyncio
async def test_booking_must_fit_opening_hours(seed_subscription, gym_slot):
    """06:00-23:00 on the gym's clock: before opening and past closing are refused."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        trainer = await make_trainer(ac)
        _, client_headers = await make_member(ac, seed_subscription)
        await link(ac, trainer, client_headers)

        too_early = await book(ac, trainer[0], client_headers, gym_slot(5, hour=5))
        assert too_early.status_code == 400
        assert "between 06:00 and 23:00" in too_early.json()["detail"]

        # Starts at 22:30, ends 23:30 - past closing.
        too_late = await book(ac, trainer[0], client_headers, gym_slot(5, hour=22) + timedelta(minutes=30))
        assert too_late.status_code == 400

        # The edges themselves are allowed.
        assert (await book(ac, trainer[0], client_headers, gym_slot(5, hour=6))).status_code == 200
        assert (await book(ac, trainer[0], client_headers, gym_slot(5, hour=22))).status_code == 200


@pytest.mark.asyncio
async def test_member_can_cancel_outside_the_cutoff_only(reschedule_appointment, backdate_appointment,
                                                         seed_subscription, gym_slot):
    """A member cancels up to 12h before. Inside that, a stranger's, or a closed one is refused."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        trainer = await make_trainer(ac)
        _, client_headers = await make_member(ac, seed_subscription)
        _, stranger_headers = await make_member(ac, seed_subscription)
        await link(ac, trainer, client_headers)

        now = datetime.now(timezone.utc)
        early = (await book(ac, trainer[0], client_headers, gym_slot(6, hour=9))).json()["id"]
        late = (await book(ac, trainer[0], client_headers, gym_slot(6, hour=12))).json()["id"]
        done = (await book(ac, trainer[0], client_headers, gym_slot(6, hour=15))).json()["id"]

        # A stranger gets 404, so appointment ids cannot be probed.
        res = await ac.post(f"/api/coaching/appointments/{early}/cancel", headers=stranger_headers)
        assert res.status_code == 404

        # 13 hours out: allowed.
        await reschedule_appointment(early, now + timedelta(hours=13))
        res = await ac.post(f"/api/coaching/appointments/{early}/cancel", headers=client_headers)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "CANCELLED"
        assert res.json()["trainer"]["id"] == trainer[0]

        # 11 hours out: only the trainer can cancel now.
        await reschedule_appointment(late, now + timedelta(hours=11))
        res = await ac.post(f"/api/coaching/appointments/{late}/cancel", headers=client_headers)
        assert res.status_code == 400
        assert "ask your trainer" in res.json()["detail"]

        # Already completed: nothing to cancel.
        await backdate_appointment(done)
        await ac.put(f"/api/coaching/appointments/{done}", json={"status": "COMPLETED"}, headers=trainer[1])
        await reschedule_appointment(done, now + timedelta(days=2))
        res = await ac.post(f"/api/coaching/appointments/{done}/cancel", headers=client_headers)
        assert res.status_code == 409