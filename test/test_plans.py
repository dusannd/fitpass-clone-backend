import pytest
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
# The plan routes build their OWN RequireRole("admin") instance, so that is the
# object the override has to key off - not the one in app.api.dependencies.
from app.api.subscriptions import get_current_admin as plans_require_admin


async def override_plans_admin():
    return 1


@pytest.fixture
def admin_client():
    """
    An AsyncClient that passes the admin gate on /subscriptions/plans.

    Note it POPS only its own key on the way out. Calling
    dependency_overrides.clear() would also wipe the get_db override registered in
    conftest, which would send every later test at the real database.
    """
    app.dependency_overrides[plans_require_admin] = override_plans_admin
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.pop(plans_require_admin, None)


@pytest.mark.asyncio
async def test_plan_tier_round_trips(admin_client):
    """
    A tier chosen at creation comes back on the response and survives a re-read,
    so the member pricing page has real data to style from.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Platinum {uuid.uuid4().hex[:6]}",
            "description": "The expensive one",
            "price": 9000,
            "duration_days": 30,
            "tier": "VIP"
        })

        assert res.status_code == 200
        assert res.json()["tier"] == "VIP"
        plan_id = res.json()["id"]

        # Re-read through the admin listing to prove it was persisted, not echoed
        res_all = await ac.get("/api/subscriptions/plans/all")
        stored = next(p for p in res_all.json() if p["id"] == plan_id)
        assert stored["tier"] == "VIP"


@pytest.mark.asyncio
async def test_plan_tier_defaults_to_standard(admin_client):
    """A plan created without a tier is Standard, so old clients keep working."""
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Basic {uuid.uuid4().hex[:6]}",
            "price": 1000,
            "duration_days": 30
        })

        assert res.status_code == 200
        assert res.json()["tier"] == "Standard"


@pytest.mark.asyncio
async def test_invalid_tier_is_rejected(admin_client):
    """The Literal on PlanCreate must reject anything outside the three tiers."""
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Bogus {uuid.uuid4().hex[:6]}",
            "price": 1000,
            "duration_days": 30,
            "tier": "Ultra"
        })

        assert res.status_code == 422


@pytest.mark.parametrize("field", ["name", "price", "duration_days", "tier"])
@pytest.mark.asyncio
async def test_explicit_null_is_rejected(admin_client, field):
    """
    A partial update must refuse an explicit null on the fields that can't hold one.

    Without this, the null reached setattr and became a 500: an IntegrityError for
    name/price/tier, and for duration_days a row that then broke every later READ
    of the plan (PlanResponse.duration_days is a plain int).
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Nullable {uuid.uuid4().hex[:6]}",
            "price": 1500,
            "duration_days": 30,
            "tier": "Pro"
        })
        plan_id = res.json()["id"]

        res_null = await ac.put(f"/api/subscriptions/plans/{plan_id}", json={field: None})
        assert res_null.status_code == 422

        # The plan must be untouched and still readable afterwards
        res_all = await ac.get("/api/subscriptions/plans/all")
        assert res_all.status_code == 200
        stored = next(p for p in res_all.json() if p["id"] == plan_id)
        assert stored["tier"] == "Pro"
        assert stored["duration_days"] == 30


@pytest.mark.asyncio
async def test_description_can_still_be_cleared(admin_client):
    """
    description IS nullable and the admin form sends null to clear it, so the
    null-rejection above must not have caught it too.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Described {uuid.uuid4().hex[:6]}",
            "description": "Clear me",
            "price": 1500,
            "duration_days": 30
        })
        plan_id = res.json()["id"]

        res_clear = await ac.put(f"/api/subscriptions/plans/{plan_id}", json={"description": None})

        assert res_clear.status_code == 200
        assert res_clear.json()["description"] is None


@pytest.mark.asyncio
async def test_tier_can_be_updated(admin_client):
    """
    The admin panel promotes/demotes a plan through PUT, so tier has to be part of
    the partial update - and the untouched fields must stay untouched.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Starter {uuid.uuid4().hex[:6]}",
            "description": "Keep me",
            "price": 2000,
            "duration_days": 30,
            "tier": "Standard"
        })
        plan_id = res.json()["id"]

        res_put = await ac.put(f"/api/subscriptions/plans/{plan_id}", json={"tier": "Pro"})

        assert res_put.status_code == 200
        assert res_put.json()["tier"] == "Pro"
        # exclude_unset means an omitted field is left alone, not nulled
        assert res_put.json()["description"] == "Keep me"
        assert res_put.json()["price"] == 2000
