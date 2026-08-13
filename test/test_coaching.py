import pytest
import uuid
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


# Temporarily override admin dependency just to hire the trainer
async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_coaching_request_flow(seed_subscription):
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
            app.dependency_overrides.pop(get_current_admin, None)
            # Login Trainer to get their JWT Token
        res_trainer_login = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_token = res_trainer_login.cookies["access_token"]
        trainer_headers = {"Cookie": f"access_token={trainer_token}"}

        # --- SETUP: CREATE MEMBER ---
        res_member_reg = await ac.post("/api/users/", json={
            "email": member_email, "password": password,
            "first_name": "Gym", "last_name": "Bro"
        })
        member_id = res_member_reg.json()["id"]

        # Login Member to get their JWT Token
        res_member_login = await ac.post("/api/users/login", json={"email": member_email, "password": password})
        member_token = res_member_login.cookies["access_token"]
        member_headers = {"Cookie": f"access_token={member_token}"}

        # --- SETUP: GIVE THE MEMBER A PLAN THAT INCLUDES A TRAINER ---
        # Without this the request below is refused with a 403. This test is about
        # the request/accept flow, not about the entitlement - that has its own
        # tests further down the file.
        await seed_subscription(member_id, includes_trainer=True)

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


@pytest.mark.asyncio
async def test_trainer_client_progress_access(seed_subscription):
    """
    INTEGRATION TEST (Trainer reads a client's progress):
    1. Two trainers and one member exist. Only trainer A is accepted by the member.
    2. The member logs a workout with three sets.
    3. Trainer A can read that history through /coaching/clients/{id}/progress.
    4. Trainer B, who was never accepted, gets 403 instead of someone else's data.
    """
    transport = ASGITransport(app=app)

    trainer_a_email = f"coach_a_{uuid.uuid4().hex[:6]}@test.com"
    trainer_b_email = f"coach_b_{uuid.uuid4().hex[:6]}@test.com"
    member_email = f"client_{uuid.uuid4().hex[:6]}@test.com"
    password = "testpassword123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # --- SETUP: TWO TRAINERS AND ONE MEMBER ---
        res_a = await ac.post("/api/users/", json={
            "email": trainer_a_email, "password": password,
            "first_name": "Accepted", "last_name": "Coach"
        })
        trainer_a_id = res_a.json()["id"]

        await ac.post("/api/users/", json={
            "email": trainer_b_email, "password": password,
            "first_name": "Nosy", "last_name": "Coach"
        })

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": trainer_a_email, "role_name": "trainer"})
            await ac.post("/api/admin/hr/hire", json={"email": trainer_b_email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.pop(get_current_admin, None)

        res_login_a = await ac.post("/api/users/login", json={"email": trainer_a_email, "password": password})
        trainer_a_headers = {"Cookie": f"access_token={res_login_a.cookies['access_token']}"}

        res_login_b = await ac.post("/api/users/login", json={"email": trainer_b_email, "password": password})
        trainer_b_headers = {"Cookie": f"access_token={res_login_b.cookies['access_token']}"}

        res_member = await ac.post("/api/users/", json={
            "email": member_email, "password": password,
            "first_name": "Tracked", "last_name": "Member"
        })
        member_id = res_member.json()["id"]

        res_login_member = await ac.post("/api/users/login", json={"email": member_email, "password": password})
        member_headers = {"Cookie": f"access_token={res_login_member.cookies['access_token']}"}

        # --- STEP 1: MEMBER LINKS UP WITH TRAINER A ONLY ---
        # Coaching needs a plan that includes personal training.
        await seed_subscription(member_id, includes_trainer=True)

        await ac.post(f"/api/coaching/request/{trainer_a_id}", headers=member_headers)

        res_pending = await ac.get("/api/coaching/requests", headers=trainer_a_headers)
        request_id = res_pending.json()[0]["id"]
        await ac.put(f"/api/coaching/requests/{request_id}", json={"status": "ACCEPTED"}, headers=trainer_a_headers)

        # --- STEP 2: MEMBER LOGS A WORKOUT ---
        plan_payload = {
            "name": "Leg Day",
            "description": "Squats",
            "exercises": [{"name": "Squat", "sets": 3, "reps": "5", "weight_step_kg": 2.5}]
        }
        res_plan = await ac.post("/api/trainer/plans", json=plan_payload, headers=trainer_a_headers)
        plan_id = res_plan.json()["id"]
        exercise_id = res_plan.json()["exercises"][0]["id"]

        await ac.post("/api/workouts/log-session", json={
            "plan_id": plan_id,
            "notes": "Heavy triple",
            "exercises": [
                {"exercise_id": exercise_id, "set_number": 1, "reps_completed": "5", "weight_kg": 100.0},
                {"exercise_id": exercise_id, "set_number": 2, "reps_completed": "5", "weight_kg": 110.0},
                {"exercise_id": exercise_id, "set_number": 3, "reps_completed": "3", "weight_kg": 105.0},
            ]
        }, headers=member_headers)

        # --- STEP 3: THE ACCEPTED TRAINER SEES THE FULL SET BREAKDOWN ---
        res_progress = await ac.get(f"/api/coaching/clients/{member_id}/progress", headers=trainer_a_headers)
        assert res_progress.status_code == 200

        sessions = res_progress.json()
        assert len(sessions) == 1

        logs = sessions[0]["exercise_logs"]
        assert len(logs) == 3
        assert max(log["weight_kg"] for log in logs) == 110.0
        assert logs[0]["exercise"]["name"] == "Squat"

        # --- STEP 4: A TRAINER WITHOUT AN ACCEPTED LINK IS BLOCKED ---
        res_forbidden = await ac.get(f"/api/coaching/clients/{member_id}/progress", headers=trainer_b_headers)
        assert res_forbidden.status_code == 403

# ==========================================
# THE PERSONAL-TRAINING ENTITLEMENT
# ==========================================
# Coaching used to be open to every member, including members holding no
# subscription at all - app/api/coaching.py contained no subscription check
# whatsoever. That is what made an expensive plan indistinguishable from the
# cheapest one, and these tests are what stop it drifting back.


async def setup_trainer_and_member(ac):
    """
    Registers a trainer and a member, hires the trainer, logs both in.

    Returns (trainer_id, member_id, member_headers, trainer_headers). Deliberately
    seeds NO subscription: each test below decides what plan the member holds,
    since that is the thing under test.
    """
    trainer_email = f"coach_{uuid.uuid4().hex[:6]}@test.com"
    member_email = f"member_{uuid.uuid4().hex[:6]}@test.com"
    password = "testpassword123"

    res_trainer = await ac.post("/api/users/", json={
        "email": trainer_email, "password": password,
        "first_name": "Perk", "last_name": "Coach"
    })
    trainer_id = res_trainer.json()["id"]

    try:
        app.dependency_overrides[get_current_admin] = override_get_current_admin
        await ac.post("/api/admin/hr/hire", json={"email": trainer_email, "role_name": "trainer"})
    finally:
        # Pop only this key. clear() would also wipe conftest's get_db override,
        # sending every later test at the real database.
        app.dependency_overrides.pop(get_current_admin, None)

    res_trainer_login = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
    trainer_headers = {"Cookie": f"access_token={res_trainer_login.cookies['access_token']}"}

    res_member = await ac.post("/api/users/", json={
        "email": member_email, "password": password,
        "first_name": "Gym", "last_name": "Member"
    })
    member_id = res_member.json()["id"]

    res_member_login = await ac.post("/api/users/login", json={"email": member_email, "password": password})
    member_headers = {"Cookie": f"access_token={res_member_login.cookies['access_token']}"}

    return trainer_id, member_id, member_headers, trainer_headers


@pytest.mark.asyncio
async def test_request_is_refused_without_the_trainer_perk(seed_subscription):
    """A paying member on a plan that does not include a trainer still gets a 403."""
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        trainer_id, member_id, member_headers, _ = await setup_trainer_and_member(ac)

        # An active, perfectly valid subscription - just not one that buys coaching.
        await seed_subscription(member_id, includes_trainer=False)

        res = await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)

        assert res.status_code == 403
        # The member is told what to DO about it, not just that they may not.
        assert "Upgrade your plan" in res.json()["detail"]


@pytest.mark.asyncio
async def test_request_is_refused_without_any_subscription():
    """
    No subscription row at all is refused the same way as the wrong plan.

    This is the case that used to slip through entirely: coaching never looked at
    subscriptions, so somebody who had never paid anything could link to a trainer.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        trainer_id, _, member_headers, _ = await setup_trainer_and_member(ac)

        res = await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)

        assert res.status_code == 403


@pytest.mark.asyncio
async def test_request_is_refused_once_the_subscription_has_expired(seed_subscription):
    """
    An expired pass grants nothing, even if the plan it points at includes a trainer.

    days_left is negative here, so the row sits in the past while is_active is still
    1 - exactly the state a member is in between expiry and the hourly sweep in
    app/services/scheduler.py.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        trainer_id, member_id, member_headers, _ = await setup_trainer_and_member(ac)

        await seed_subscription(member_id, includes_trainer=True, days_left=-1)

        res = await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)

        assert res.status_code == 403


@pytest.mark.asyncio
async def test_request_is_allowed_with_the_trainer_perk(seed_subscription):
    """The same request succeeds the moment the plan includes personal training."""
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        trainer_id, member_id, member_headers, _ = await setup_trainer_and_member(ac)

        await seed_subscription(member_id, includes_trainer=True)

        res = await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)

        assert res.status_code == 200
        assert res.json()["status"] == "success"


@pytest.mark.asyncio
async def test_booking_is_refused_after_the_perk_goes_away(seed_subscription, expire_subscription):
    """
    The gate is on booking too, not only on the request.

    Without this a member links to a trainer while on a plan that includes one,
    drops to the cheapest plan, and keeps booking sessions forever. The link is
    deliberately left intact - nobody's trainer is torn away - they simply cannot
    book anything new.
    """
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        trainer_id, member_id, member_headers, trainer_headers = await setup_trainer_and_member(ac)

        # 1. Link up while the plan still includes a trainer.
        sub_id = await seed_subscription(member_id, includes_trainer=True)

        await ac.post(f"/api/coaching/request/{trainer_id}", headers=member_headers)
        res_pending = await ac.get("/api/coaching/requests", headers=trainer_headers)
        request_id = res_pending.json()[0]["id"]
        res_accept = await ac.put(
            f"/api/coaching/requests/{request_id}",
            json={"status": "ACCEPTED"},
            headers=trainer_headers
        )
        assert res_accept.status_code == 200

        # 2. The pass lapses. Same member, same ACCEPTED link, no entitlement.
        await expire_subscription(sub_id)

        start_time = datetime.now(timezone.utc) + timedelta(days=3)
        res_booking = await ac.post("/api/coaching/appointments", json={
            "trainer_id": trainer_id,
            "start_time": start_time.isoformat(),
            "end_time": (start_time + timedelta(hours=1)).isoformat(),
        }, headers=member_headers)

        assert res_booking.status_code == 403
        assert "Upgrade your plan" in res_booking.json()["detail"]

        # 3. The link itself is untouched - the trainer still has this client.
        res_clients = await ac.get("/api/coaching/clients", headers=trainer_headers)
        assert any(c["client_id"] == member_id for c in res_clients.json())
