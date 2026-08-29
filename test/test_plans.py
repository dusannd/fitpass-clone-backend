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


@pytest.mark.parametrize("field", [
    "name", "price", "duration_days", "tier",
    # The perk flags are NOT NULL columns too. A null here is the more tempting
    # mistake, because it reads like "turn this perk off" - sending false is how
    # that is done.
    "includes_trainer", "includes_group_classes",
    "has_sauna_access", "has_towel_service", "allows_guest",
])
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


# ==========================================
# PLAN PERKS
# ==========================================
# What a membership actually includes, as opposed to how its card is styled.
# tier is decoration; these five are the reason an expensive plan is worth more.

PERKS = [
    "includes_trainer",
    "includes_group_classes",
    "has_sauna_access",
    "has_towel_service",
    "allows_guest",
]


@pytest.mark.asyncio
async def test_perks_round_trip(admin_client):
    """
    Perks ticked at creation come back on the response AND survive a re-read.

    The re-read is the point: an endpoint that echoed the request body back would
    pass the first assertion while having stored nothing.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Loaded {uuid.uuid4().hex[:6]}",
            "price": 4500,
            "duration_days": 30,
            "tier": "VIP",
            **{perk: True for perk in PERKS},
        })

        assert res.status_code == 200
        assert all(res.json()[perk] is True for perk in PERKS)
        plan_id = res.json()["id"]

        res_all = await ac.get("/api/subscriptions/plans/all")
        stored = next(p for p in res_all.json() if p["id"] == plan_id)
        assert all(stored[perk] is True for perk in PERKS)


@pytest.mark.asyncio
async def test_perks_default_to_false(admin_client):
    """
    A plan created without any perks reads back as false, never null.

    Null is what would happen with a column added nullable and unbackfilled - the
    mistake is_active on this same table already made, and the reason PlanResponse
    still carries a validator for it.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Bare {uuid.uuid4().hex[:6]}",
            "price": 1500,
            "duration_days": 30,
        })

        assert res.status_code == 200
        for perk in PERKS:
            assert res.json()[perk] is False, f"{perk} should default to False"


@pytest.mark.asyncio
async def test_perk_can_be_toggled_off(admin_client):
    """
    Turning a perk off is a real edit the admin panel makes, and false must not be
    mistaken for "not supplied" on the way through exclude_unset.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Downgrade {uuid.uuid4().hex[:6]}",
            "description": "Keep me",
            "price": 4500,
            "duration_days": 30,
            "tier": "VIP",
            "includes_trainer": True,
            "has_sauna_access": True,
        })
        plan_id = res.json()["id"]

        res_put = await ac.put(
            f"/api/subscriptions/plans/{plan_id}", json={"includes_trainer": False}
        )

        assert res_put.status_code == 200
        assert res_put.json()["includes_trainer"] is False
        # The perk that was not mentioned is left exactly as it was.
        assert res_put.json()["has_sauna_access"] is True
        assert res_put.json()["description"] == "Keep me"

        # And it really is on the row, not just in the reply.
        res_all = await ac.get("/api/subscriptions/plans/all")
        stored = next(p for p in res_all.json() if p["id"] == plan_id)
        assert stored["includes_trainer"] is False
        assert stored["has_sauna_access"] is True


@pytest.mark.asyncio
async def test_perks_reach_the_public_pricing_endpoint(admin_client):
    """
    GET /plans is what the member pricing cards read, and it is a different query
    from the admin listing - the perks have to be on that response too or the
    cards have nothing to tick.
    """
    async with admin_client as ac:
        res = await ac.post("/api/subscriptions/plans", json={
            "name": f"Public {uuid.uuid4().hex[:6]}",
            "price": 4500,
            "duration_days": 30,
            "tier": "VIP",
            "includes_trainer": True,
            "allows_guest": True,
        })
        plan_id = res.json()["id"]

        res_public = await ac.get("/api/subscriptions/plans")

        assert res_public.status_code == 200
        listed = next(p for p in res_public.json() if p["id"] == plan_id)
        assert listed["includes_trainer"] is True
        assert listed["allows_guest"] is True
        assert listed["has_towel_service"] is False

@pytest.mark.asyncio
async def test_plans_come_back_cheapest_first(admin_client):
    """
    Both plan listings order by price.

    The pricing page renders the list in the order it arrives, so without an
    ORDER BY the cards came out in whatever sequence the database returned - which
    is how a 10000 VIP ended up sitting to the left of a 3000 Standard.

    The three plans below are created deliberately OUT of price order, because
    SQLite hands rows back in insertion order when nothing sorts them. That is what
    makes this a real test rather than one that passes for free: revert the
    order_by and the returned prices are 9000, 1000, 5000.

    Other tests share this database, so the assertions filter down to just these
    three plans by id rather than reading the whole list.
    """
    async with admin_client as ac:
        marker = uuid.uuid4().hex[:6]
        created = {}

        for label, price in (("expensive", 9000), ("cheap", 1000), ("middle", 5000)):
            res = await ac.post("/api/subscriptions/plans", json={
                "name": f"{label} {marker}",
                "price": price,
                "duration_days": 30,
                "tier": "Standard",
            })
            assert res.status_code == 200
            created[res.json()["id"]] = price

        # --- 1. THE PUBLIC PRICING ENDPOINT ---
        res_public = await ac.get("/api/subscriptions/plans")
        assert res_public.status_code == 200

        public_prices = [p["price"] for p in res_public.json() if p["id"] in created]
        assert public_prices == [1000, 5000, 9000]

        # --- 2. THE ADMIN LISTING ---
        res_all = await ac.get("/api/subscriptions/plans/all")
        assert res_all.status_code == 200

        admin_prices = [p["price"] for p in res_all.json() if p["id"] in created]
        assert admin_prices == [1000, 5000, 9000]

        # --- 3. THE WHOLE LIST IS ORDERED, NOT JUST OUR SLICE ---
        # A sort that only happened to work for three fresh rows would not help the
        # real page, which renders every active plan.
        all_public_prices = [p["price"] for p in res_public.json()]
        assert all_public_prices == sorted(all_public_prices)

