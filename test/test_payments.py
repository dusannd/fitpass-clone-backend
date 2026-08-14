import pytest
import stripe
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.api.dependencies import get_current_user_id

# ==========================================
# 1. THE CUSTOMER PORTAL
# ==========================================

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


# ==========================================
# 2. THE WEBHOOK
# ==========================================
# None of this was covered before: stripe.Webhook.construct_event was never
# mocked anywhere in the suite, so every branch of the handler that decides
# whether somebody keeps gym access was running unverified.

WEBHOOK_URL = "/api/payments/webhook"

USER_WHOSE_CARD_FAILS = 900_105
USER_ON_THE_NESTED_SHAPE = 900_106
USER_WHOSE_RETRY_CLEARS = 900_107
USER_PAYING_FOR_THE_FIRST_TIME = 900_108


def stripe_invoice(**fields):
    """
    Builds a real stripe.Invoice out of plain data.

    It has to be an actual StripeObject, not a dict: the handler reads the invoice
    with getattr, and stripe-python 15.x StripeObjects are NOT dict subclasses -
    they have no .get and a plain dict has no attributes. A dict fixture here
    would fail for reasons that have nothing to do with the code under test.
    """
    return stripe.Invoice.construct_from(fields, "sk_test_fake")


def invoice_line(metadata=None, period_end=None):
    """One entry for invoice.lines.data - our checkout only ever creates one."""
    line = {}
    if metadata is not None:
        line["metadata"] = metadata
    if period_end is not None:
        line["period"] = {"end": period_end}
    return line


@pytest.fixture
def post_webhook(monkeypatch):
    """
    Posts a Stripe event at the webhook with signature verification stubbed out.

    Verification is bypassed rather than faked with a real signature: the secret
    would have to be pinned in the test, and what is under test here is what the
    handler DOES with an event, not Stripe's HMAC. One test below patches this
    stub to raise instead, which is what covers the rejection path.

    The outer envelope stays a plain dict - the handler only ever reaches into it
    with event['type'] and event['data']['object'] - while the invoice inside is a
    genuine StripeObject.
    """
    async def _post(event_type: str, invoice_fields: dict):
        event = {
            "type": event_type,
            "data": {"object": stripe_invoice(**invoice_fields)},
        }
        monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: event)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            return await ac.post(
                WEBHOOK_URL,
                content=b"{}",
                headers={"Stripe-Signature": "t=1,v1=irrelevant"},
            )

    return _post


@pytest.mark.asyncio
async def test_failed_payment_revokes_access(
    post_webhook, seed_subscription, subscription_by_stripe_id
):
    """
    The hole this closes: nothing reacted to invoice.payment_failed at all, so a
    member whose card stopped working kept walking through the turnstile until
    Stripe finally gave up weeks later.
    """
    await seed_subscription(
        user_id=USER_WHOSE_CARD_FAILS, stripe_subscription_id="sub_card_declined"
    )
    assert (await subscription_by_stripe_id("sub_card_declined"))["is_active"] == 1

    res = await post_webhook("invoice.payment_failed", {
        "id": "in_declined",
        "subscription": "sub_card_declined",
    })

    assert res.status_code == 200
    assert (await subscription_by_stripe_id("sub_card_declined"))["is_active"] == 0


@pytest.mark.asyncio
async def test_failed_payment_reads_the_nested_subscription_shape(
    post_webhook, seed_subscription, subscription_by_stripe_id
):
    """
    Newer Stripe API versions moved the subscription id off the top level and down
    into parent.subscription_details. Sending ONLY that shape - no top-level
    `subscription` field at all - is what proves the fallback ladder is real
    rather than dead code the happy path never reaches.
    """
    await seed_subscription(
        user_id=USER_ON_THE_NESTED_SHAPE, stripe_subscription_id="sub_nested_shape"
    )

    res = await post_webhook("invoice.payment_failed", {
        "id": "in_nested",
        "parent": {"subscription_details": {"subscription": "sub_nested_shape"}},
    })

    assert res.status_code == 200
    assert (await subscription_by_stripe_id("sub_nested_shape"))["is_active"] == 0


@pytest.mark.asyncio
async def test_failed_payment_for_an_unknown_subscription_is_ignored(post_webhook):
    """
    A subscription we have no row for is not an error - it can belong to another
    environment sharing the same Stripe account. It must still answer 200, or
    Stripe retries something we are never going to handle.
    """
    res = await post_webhook("invoice.payment_failed", {
        "id": "in_stranger",
        "subscription": "sub_belongs_to_nobody",
    })

    assert res.status_code == 200
    assert res.json() == {"status": "success"}


@pytest.mark.asyncio
async def test_a_successful_retry_restores_access(
    post_webhook, seed_plan, seed_subscription, subscription_by_stripe_id
):
    """
    The test that justifies revoking on the FIRST failure rather than waiting out
    Stripe's dunning schedule.

    Revoking immediately is only defensible because it undoes itself: Stripe
    retries the invoice, payment_succeeded finds the same row and sets is_active
    back to 1. If that ever stopped being true, a member whose card hiccuped once
    would be locked out permanently with nobody at the desk able to explain why.
    """
    plan_id = await seed_plan()
    await seed_subscription(
        user_id=USER_WHOSE_RETRY_CLEARS,
        plan_id=plan_id,
        stripe_subscription_id="sub_retry_clears",
    )

    # a) The card is refused
    await post_webhook("invoice.payment_failed", {
        "id": "in_attempt_1",
        "subscription": "sub_retry_clears",
    })
    assert (await subscription_by_stripe_id("sub_retry_clears"))["is_active"] == 0

    # b) Stripe retries a few days later and it clears
    new_period_end = int(datetime(2030, 6, 1, tzinfo=timezone.utc).timestamp())
    res = await post_webhook("invoice.payment_succeeded", {
        "id": "in_attempt_2",
        "subscription": "sub_retry_clears",
        "subscription_details": {
            "metadata": {"user_id": str(USER_WHOSE_RETRY_CLEARS), "plan_id": str(plan_id)}
        },
        "lines": {"data": [invoice_line(period_end=new_period_end)]},
    })

    assert res.status_code == 200
    restored = await subscription_by_stripe_id("sub_retry_clears")
    assert restored["is_active"] == 1
    # end_date is taken from Stripe's own period end, not incremented locally
    assert restored["end_date"].year == 2030


@pytest.mark.asyncio
async def test_first_payment_creates_the_row_from_line_item_metadata(
    post_webhook, seed_plan, subscription_by_stripe_id
):
    """
    The first-payment path, driven through the LINE ITEM metadata fallback -
    the branch that runs whenever invoice.subscription_details is absent, which is
    exactly the case on the newer API versions.

    That fallback used to call first_line.get("metadata"). A stripe.StripeObject
    has no .get in stripe-python 15.x, so it raised AttributeError, the webhook
    500'd, and the member's subscription was never created at all - while Stripe
    retried the same failing event.
    """
    plan_id = await seed_plan()
    period_end = int(datetime(2031, 3, 15, tzinfo=timezone.utc).timestamp())

    res = await post_webhook("invoice.payment_succeeded", {
        "id": "in_first_payment",
        "subscription": "sub_brand_new",
        # No subscription_details at all - the fallback is the only way through
        "lines": {"data": [invoice_line(
            metadata={"user_id": str(USER_PAYING_FOR_THE_FIRST_TIME), "plan_id": str(plan_id)},
            period_end=period_end,
        )]},
    })

    assert res.status_code == 200
    created = await subscription_by_stripe_id("sub_brand_new")
    assert created is not None, "the webhook answered 200 but wrote nothing"
    assert created["user_id"] == USER_PAYING_FOR_THE_FIRST_TIME
    assert created["plan_id"] == plan_id
    assert created["is_active"] == 1


@pytest.mark.asyncio
async def test_a_bad_signature_is_rejected(monkeypatch, seed_subscription, subscription_by_stripe_id):
    """
    Without this, anybody who knows the URL could revoke any member's pass with a
    handwritten POST. The event is a perfectly valid revocation - only the
    signature is wrong - so the row must be untouched afterwards.
    """
    await seed_subscription(user_id=900_109, stripe_subscription_id="sub_forged_target")

    def refuse_signature(*args, **kwargs):
        raise stripe.error.SignatureVerificationError("Invalid signature", "t=1,v1=forged")

    monkeypatch.setattr(stripe.Webhook, "construct_event", refuse_signature)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            WEBHOOK_URL,
            content=b'{"type":"invoice.payment_failed"}',
            headers={"Stripe-Signature": "t=1,v1=forged"},
        )

    assert res.status_code == 400
    assert (await subscription_by_stripe_id("sub_forged_target"))["is_active"] == 1
