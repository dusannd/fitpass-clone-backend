import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.access import get_current_worker
from app.core.redis_client import redis_db
from app.core.security import create_qr_token


# The turnstile's own RequireRole("worker") instance - access.py builds its own, so
# overriding the one in worker.py would not reach /access/scan.
async def override_get_current_worker():
    return 1


@pytest.mark.asyncio
async def test_a_refused_code_can_be_scanned_again(seed_subscription):
    """
    The code is burned the moment it is scanned, before the door checks anything.
    A code the door then REFUSED used to stay burned, so the member's retry - once
    the problem was sorted - was answered with "Screenshots are strictly forbidden".
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        email = f"door_{uuid.uuid4().hex[:6]}@test.com"
        res = await ac.post("/api/users/", json={
            "email": email, "password": "password123", "first_name": "D", "last_name": "D",
        })
        member_id = res.json()["id"]

        # The database is fresh every run, so user ids start over - but Redis is the
        # real one and still holds "user_status:<id>" from the LAST run, where this
        # same id walked in. Without this the first scan is a passback refusal.
        await redis_db.delete(f"user_status:{member_id}")

        res = await ac.post("/api/users/login", json={"email": email, "password": "password123"})
        member_headers = {"Cookie": f"access_token={res.cookies['access_token']}"}

        # A pass that is valid only at one location.
        tag = uuid.uuid4().hex[:6]
        await seed_subscription(member_id, location_names=[f"Scan Test {tag}"])
        res = await ac.get("/api/subscriptions/locations", headers=member_headers)
        allowed = next(loc["id"] for loc in res.json() if f"Scan Test {tag}" in loc["name"])
        elsewhere = max(loc["id"] for loc in res.json()) + 1000

        token = create_qr_token(user_id=member_id, action_type="ENTRY")

        try:
            app.dependency_overrides[get_current_worker] = override_get_current_worker

            # 1. Wrong location: refused.
            res = await ac.post("/api/access/scan", json={
                "qr_token": token, "location_id": elsewhere, "scan_type": "ENTRY",
            })
            assert res.status_code == 403
            assert "location" in res.json()["detail"]

            # 2. The SAME code at the right door: it was never used, so it opens.
            res = await ac.post("/api/access/scan", json={
                "qr_token": token, "location_id": allowed, "scan_type": "ENTRY",
            })
            assert res.status_code == 200, res.text
            assert res.json()["access_granted"] is True

            # 3. A code that DID open the door stays burned - replay is still refused.
            res = await ac.post("/api/access/scan", json={
                "qr_token": token, "location_id": allowed, "scan_type": "ENTRY",
            })
            assert res.status_code == 403
            assert "already used" in res.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_current_worker, None)
