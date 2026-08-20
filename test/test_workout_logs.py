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
    2. Trainer creates a workout plan with an exercise (incl. the live workout setup fields).
    3. Member completes the workout and logs ONE ROW PER SET.
    4. Member fetches their workout history and verifies every set came back.
    """
    transport = ASGITransport(app=app)

    trainer_email = f"trainer_{uuid.uuid4().hex[:6]}@test.com"
    member_email = f"member_{uuid.uuid4().hex[:6]}@test.com"
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
            app.dependency_overrides.pop(get_current_admin, None)

        res = await ac.post("/api/users/login", json={"email": trainer_email, "password": password})
        trainer_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # Member
        await ac.post("/api/users/",
                      json={"email": member_email, "password": password, "first_name": "M", "last_name": "M"})
        res = await ac.post("/api/users/login", json={"email": member_email, "password": password})
        member_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # --- 2. TRAINER CREATES A PLAN ---
        plan_payload = {
            "name": "Heavy Chest Day",
            "description": "Focus on bench press",
            "exercises": [
                {
                    "name": "Bench Press",
                    "sets": 3,
                    "reps": "8-10",
                    "rest_time_seconds": 90,
                    "recommended_weight_kg": 80.0,
                    "weight_step_kg": 4.5,
                    "instructions": "3 sec negatives"
                }
            ]
        }
        res_plan = await ac.post("/api/trainer/plans", json=plan_payload, headers=trainer_headers)
        assert res_plan.status_code == 200

        plan_id = res_plan.json()["id"]
        created_exercise = res_plan.json()["exercises"][0]
        exercise_id = created_exercise["id"]

        # The trainer setup must actually reach the database, otherwise the client app
        # silently falls back to the defaults and the "+" button steps by the wrong amount.
        assert created_exercise["recommended_weight_kg"] == 80.0
        assert created_exercise["weight_step_kg"] == 4.5
        assert created_exercise["instructions"] == "3 sec negatives"

        # --- 3. MEMBER LOGS A WORKOUT SESSION (one entry per set) ---
        log_payload = {
            "plan_id": plan_id,
            "notes": "Felt incredibly strong today!",
            "exercises": [
                {"exercise_id": exercise_id, "set_number": 1, "reps_completed": "10", "weight_kg": 80.0},
                {"exercise_id": exercise_id, "set_number": 2, "reps_completed": "10", "weight_kg": 85.5},
                {"exercise_id": exercise_id, "set_number": 3, "reps_completed": "8", "weight_kg": 75.0},
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

        # Verify the deeply nested exercise stats: three separate sets came back in order
        exercise_logs = history[0]["exercise_logs"]
        assert len(exercise_logs) == 3
        assert [log["set_number"] for log in exercise_logs] == [1, 2, 3]
        assert [log["weight_kg"] for log in exercise_logs] == [80.0, 85.5, 75.0]
        assert exercise_logs[0]["exercise"]["name"] == "Bench Press"

        # The personal record is the heaviest set of the exercise, not the last one
        assert max(log["weight_kg"] for log in exercise_logs) == 85.5