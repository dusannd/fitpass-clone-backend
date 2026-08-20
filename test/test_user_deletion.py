import logging
import uuid

import pytest
import stripe
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.api.dependencies import get_current_admin

# ==========================================
# 1. DELETING AN ACCOUNT MUST STOP THE BILLING
# ==========================================
#
# Deleting the User row drops the user_subscriptions rows with it, but Stripe knows
# nothing about our database. Left alone, the recurring charge keeps running against
# a customer who no longer has an account - and the local record of WHICH
# subscription to cancel is destroyed in the same transaction, so nobody can even
# find it afterwards.
#
# These tests assert on the plumbing, not just the status code: which id reached
# Stripe, and how many times. A test that only checked for 204 would pass just as
# happily against the version that never calls Stripe at all.

USERS_URL = "/api/users/"

# The admin id the endpoint records. Nothing reads it back, but it has to be a real
# int because the route annotates the dependency as one.
ACTING_ADMIN_ID = 990_001


class CancelRecorder:
    """
    Stands in for stripe.Subscription.cancel.

    A plain sync function, because the endpoint hands it to run_in_threadpool
    exactly like the real (synchronous) SDK. `error` lets a single test turn the
    fake into an outage without a second fixture.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.error: Exception | None = None

    def cancel(self, subscription_id, *args, **kwargs):
        self.calls.append(subscription_id)
        if self.error:
            raise self.error
        return type("FakeSub", (), {"id": subscription_id, "status": "canceled"})()

    def install(self, monkeypatch):
        # Patching the class attribute rather than a module-level name, so it does
        # not matter which module imported stripe - users.py is covered either way.
        monkeypatch.setattr(stripe.Subscription, "cancel", self.cancel)
        return self


@pytest.fixture
def fake_cancel(monkeypatch):
    """
    Installed by EVERY test in this module, including the ones that expect Stripe
    never to be called. That way a regression which starts cancelling too eagerly
    fails on the "was not called" assertion instead of quietly opening a socket to
    the live Stripe API from the test suite.
    """
    return CancelRecorder().install(monkeypatch)


@pytest.fixture
def admin_client():
    """
    An AsyncClient that the delete endpoint sees as an admin.

    The teardown POPS only its own key. dependency_overrides.clear() would also wipe
    the get_db override registered in conftest, and every test after that would
    write to the real database.
    """
    def _make() -> AsyncClient:
        app.dependency_overrides[get_current_admin] = lambda: ACTING_ADMIN_ID
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _make
    app.dependency_overrides.pop(get_current_admin, None)


async def _register(ac: AsyncClient) -> int:
    """
    Creates a real user over HTTP and returns their id.

    The uuid suffix matters: the in-memory database is shared across the whole
    session, so a fixed email would collide with another module's user.
    """
    res = await ac.post(USERS_URL, json={
        "email": f"deletion_{uuid.uuid4().hex[:8]}@gym.com",
        "password": "strongpassword123",
        "first_name": "Doomed",
        "last_name": "Account",
    })
    assert res.status_code == 200, f"Could not register: {res.text}"
    return res.json()["id"]


@pytest.mark.asyncio
async def test_deleting_a_member_cancels_their_stripe_subscription(
    admin_client, fake_cancel, seed_subscription
):
    """
    The happy path, asserted on the id rather than the call count: the endpoint has
    to send the subscription id we stored, not just call Stripe with something.
    """
    async with admin_client() as ac:
        user_id = await _register(ac)
        await seed_subscription(user_id=user_id, stripe_subscription_id="sub_delete_111")

        res = await ac.delete(f"{USERS_URL}{user_id}")

    assert res.status_code == 204
    assert fake_cancel.calls == ["sub_delete_111"]


@pytest.mark.asyncio
async def test_deleting_a_member_without_stripe_never_calls_stripe(
    admin_client, fake_cancel, seed_subscription
):
    """
    Rows that predate the Stripe integration, and passes the desk activates by hand,
    carry no stripe_subscription_id. Calling Stripe with None would be an error at
    the API, so the query has to filter them out before we ever get there.
    """
    async with admin_client() as ac:
        user_id = await _register(ac)
        await seed_subscription(user_id=user_id, stripe_subscription_id=None)

        res = await ac.delete(f"{USERS_URL}{user_id}")

    assert res.status_code == 204
    assert fake_cancel.calls == []


@pytest.mark.asyncio
async def test_every_active_stripe_subscription_is_cancelled(
    admin_client, fake_cancel, seed_subscription
):
    """
    A user can hold more than one active row - an upgrade leaves the previous
    subscription behind. Cancelling only the first one we find would half-fix the
    billing bug, which is worse than not fixing it, because it looks fixed.
    """
    async with admin_client() as ac:
        user_id = await _register(ac)
        await seed_subscription(user_id=user_id, stripe_subscription_id="sub_first_222")
        await seed_subscription(user_id=user_id, stripe_subscription_id="sub_second_333")

        res = await ac.delete(f"{USERS_URL}{user_id}")

    assert res.status_code == 204
    assert sorted(fake_cancel.calls) == ["sub_first_222", "sub_second_333"]


@pytest.mark.asyncio
async def test_stripe_failure_still_deletes_the_user_and_logs_the_id(
    admin_client, fake_cancel, seed_subscription, caplog
):
    """
    An admin must be able to delete an account while Stripe is down - a GDPR erasure
    request does not wait for someone else's outage.

    But the row is about to disappear, and with it the only copy of the subscription
    id. So the log line is not decoration: it is the sole remaining record of which
    subscription is still billing and has to be cancelled by hand in the Stripe
    dashboard. Hence the assertion on the id being IN the message, not merely that
    something was logged.
    """
    fake_cancel.error = stripe.StripeError("Stripe is having a bad day")

    async with admin_client() as ac:
        user_id = await _register(ac)
        await seed_subscription(user_id=user_id, stripe_subscription_id="sub_orphan_444")

        with caplog.at_level(logging.ERROR):
            res = await ac.delete(f"{USERS_URL}{user_id}")

        assert res.status_code == 204

        # Really gone, not just reported as gone - a second delete finds nothing.
        second = await ac.delete(f"{USERS_URL}{user_id}")

    assert second.status_code == 404

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("sub_orphan_444" in r.getMessage() for r in errors), (
        "The orphaned subscription id must appear in the log - it is the only "
        f"record left of it. Saw: {[r.getMessage() for r in errors]}"
    )
