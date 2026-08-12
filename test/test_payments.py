import pytest
import stripe
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.api.dependencies import get_current_user_id

PORTAL_URL = "/api/payments/customer-portal"

# User ids that no other test module touches. The in-memory database is shared
# across the whole session, so a test that needs "a user with no subscription"
# has to pick an id nobody else has seeded a row for.
USER_WITHOUT_SUB = 900_101
USER_WITH_MANUAL_SUB = 900_102
USER_WITH_STRIPE_SUB = 900_103
USER_WHEN_STRIPE_IS_DOWN = 900_104


@pytest.fixture
def client_as():
    """
    Builds an AsyncClient authenticated as an arbitrary user id.

    Note the teardown POPS only its own key. dependency_overrides.clear() would
    also wipe the get_db override registered in conftest, and every test after
    that would write to the real database.
    """
    def _client_as(user_id: int):
        app.dependency_overrides[get_current_user_id] = lambda: user_id
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _client_as
    app.dependency_overrides.pop(get_current_user_id, None)


class FakeStripe:
    """
    Stands in for the two Stripe calls the endpoint makes.

    It records what it was handed so the tests can assert on the plumbing -
    without that, a hardcoded URL in the endpoint would pass just as happily.
    Both fakes are plain sync functions because the endpoint runs them through
    run_in_threadpool, exactly like the real SDK.
    """

    def __init__(self, customer_id="cus_fake_987", url="https://billing.stripe.com/session/fake"):
        self.customer_id = customer_id
        self.url = url
        self.retrieve_calls = []
        self.create_kwargs = None

    def retrieve(self, subscription_id, *args, **kwargs):
        self.retrieve_calls.append(subscription_id)
        # The endpoint only reads .customer off this object.
        return type("FakeSub", (), {"customer": self.customer_id})()

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return type("FakeSession", (), {"url": self.url})()

    def install(self, monkeypatch):
        monkeypatch.setattr(stripe.Subscription, "retrieve", self.retrieve)
        monkeypatch.setattr(stripe.billing_portal.Session, "create", self.create)
        return self


@pytest.fixture
def fake_stripe(monkeypatch):
    """
    Patches both Stripe entry points for the whole test.

    Every test in this module installs it, including the ones that expect a 400
    before Stripe is ever reached - that way a regression which starts calling
    Stripe too early fails on the "was not called" assertion instead of quietly
    opening a socket to the live API.
    """
    return FakeStripe().install(monkeypatch)


@pytest.mark.asyncio
async def test_portal_requires_active_subscription(client_as, fake_stripe):
    """No subscription means there is nothing to manage - and no reason to call Stripe."""
    async with client_as(USER_WITHOUT_SUB) as ac:
        res = await ac.post(PORTAL_URL)

    assert res.status_code == 400
    assert "active subscription" in res.json()["detail"]
    assert fake_stripe.retrieve_calls == []
    assert fake_stripe.create_kwargs is None


@pytest.mark.asyncio
async def test_portal_rejects_manual_subscription(client_as, fake_stripe, seed_subscription):
    """
    A pass activated at the desk has no Stripe subscription behind it, so the
    portal cannot exist for it. We must refuse before touching the Stripe API.
    """
    await seed_subscription(user_id=USER_WITH_MANUAL_SUB, stripe_subscription_id=None)

    async with client_as(USER_WITH_MANUAL_SUB) as ac:
        res = await ac.post(PORTAL_URL)

    assert res.status_code == 400
    assert "wasn't created through Stripe" in res.json()["detail"]
    assert fake_stripe.retrieve_calls == []


@pytest.mark.asyncio
async def test_portal_returns_session_url(client_as, fake_stripe, seed_subscription):
    """
    The happy path, asserted on the plumbing rather than just the status code:
    the stored subscription id reaches Stripe, the customer id that comes back is
    the one the session is created for, and the return_url points at our page.
    """
    await seed_subscription(
        user_id=USER_WITH_STRIPE_SUB, stripe_subscription_id="sub_live_555"
    )

    async with client_as(USER_WITH_STRIPE_SUB) as ac:
        res = await ac.post(PORTAL_URL)

    assert res.status_code == 200
    assert res.json() == {"url": fake_stripe.url}

    # The id we stored is the id we looked up
    assert fake_stripe.retrieve_calls == ["sub_live_555"]

    # ...and the customer Stripe handed back is the one the session belongs to.
    assert fake_stripe.create_kwargs["customer"] == fake_stripe.customer_id
    assert fake_stripe.create_kwargs["return_url"] == f"{settings.FRONTEND_URL}/subscriptions"


@pytest.mark.asyncio
async def test_portal_surfaces_stripe_failure_as_502(
    client_as, fake_stripe, seed_subscription, monkeypatch
):
    """
    Stripe being unreachable is an upstream failure, not a bug in our code.
    It has to come back as a 502 - a 500 here would put every Stripe outage on
    our own error budget.
    """
    await seed_subscription(
        user_id=USER_WHEN_STRIPE_IS_DOWN, stripe_subscription_id="sub_live_666"
    )

    def exploding_retrieve(*args, **kwargs):
        raise stripe.StripeError("Stripe is having a bad day")

    monkeypatch.setattr(stripe.Subscription, "retrieve", exploding_retrieve)

    async with client_as(USER_WHEN_STRIPE_IS_DOWN) as ac:
        res = await ac.post(PORTAL_URL)

    assert res.status_code == 502
    assert "Stripe" in res.json()["detail"]


@pytest.mark.asyncio
async def test_portal_requires_authentication(fake_stripe):
    """Without the auth cookie the route must never reach the database at all."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(PORTAL_URL)

    assert res.status_code == 401
    assert fake_stripe.retrieve_calls == []
