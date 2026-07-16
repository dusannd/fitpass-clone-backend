import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin


# Temporary admin override for hiring the trainer
async def override_get_current_admin():
    return 1


@pytest.mark.asyncio
async def test_workout_logging_flow():
    """
    INTEGRATION TEST (Workout Progress Tracking):
    1. Register Trainer and Member.
    2. Trainer creates a workout plan with an exercise.
    3. Member completes the workout and logs their actual performance (weight, sets).
    4. Member fetches their workout history and verifies the data.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@gym.com"
    member_email = f"member_{uuid.uuid4().hex[:6]}@gym.com"
    password = "password123"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # --- 1. SETUP: REGISTER USERS ---
        # Trainer
        res = await ac.post("/api/users/",
                            json={"email": trainer_email, "password": password, "first_name": "T", "last_name": "T"})

        try:
            app.dependency_overrides[get_current_admin] = override_get_current_admin
            await ac.post("/api/admin/hr/hire", json={"email": trainer_email, "role_name": "trainer"})
        finally:
            app.dependency_overrides.clear()

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # Member
        await ac.post("/api/users/",
                      json={"email": member_email, "password": password, "first_name": "M", "last_name": "M"})
        res = await ac.post("/api/users/login", json={"email": member_email, "password": password})
        member_headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # --- 2. TRAINER CREATES A PLAN ---
        plan_payload = {
            "name": "Heavy Chest Day",
            "description": "Focus on bench press",
            "exercises": [
                {"name": "Bench Press", "sets": 3, "reps": "8-10", "rest_time_seconds": 90}
            ]
        }
        res_plan = await ac.post("/api/trainer/plans", json=plan_payload, headers=trainer_headers)
        assert res_plan.status_code == 200

        plan_id = res_plan.json()["id"]
        exercise_id = res_plan.json()["exercises"][0]["id"]

        # --- 3. MEMBER LOGS A WORKOUT SESSION ---
        log_payload = {
            "plan_id": plan_id,
            "notes": "Felt incredibly strong today!",
            "exercises": [
                {
                    "exercise_id": exercise_id,
                    "sets_completed": 3,
                    "reps_completed": "10",
                    "weight_kg": 85.5  # Lifted 85.5 kg!
                }
            ]
        }
        res_log = await ac.post("/api/workouts/log-session", json=log_payload, headers=member_headers)

        assert res_log.status_code == 200
        session_id = res_log.json()["id"]
        assert res_log.json()["notes"] == "Felt incredibly strong today!"

        # --- 4. MEMBER FETCHES WORKOUT HISTORY ---
        res_history = await ac.get("/api/workouts/history", headers=member_headers)
        assert res_history.status_code == 200

        history = res_history.json()

        # Verify the session is in the history
        assert len(history) == 1
        assert history[0]["id"] == session_id

        # Verify the deeply nested exercise stats
        exercise_logs = history[0]["exercise_logs"]
        assert len(exercise_logs) == 1
        assert exercise_logs[0]["weight_kg"] == 85.5
        assert exercise_logs[0]["exercise"]["name"] == "Bench Press"