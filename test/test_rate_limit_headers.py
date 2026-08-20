import uuid

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.rate_limit import limiter

# ==========================================
# 1. RATE-LIMITED ROUTES MUST STILL RETURN
# ==========================================
#
# conftest sets limiter.enabled = False for the whole session, which is right for
# every other test - nobody wants a 429 halfway through an unrelated suite. The cost
# is that the limiter's own wrapper never runs, so the entire header-injection path
# is invisible to the suite.
#
# That blind spot shipped a real bug. Turning on headers_enabled made slowapi call
# _inject_headers after every successful request, and it writes into the endpoint's
# `response` argument - a route that does not declare `response: Response` gets None
# passed instead, and slowapi raises
# "parameter `response` must be an instance of starlette.responses.Response".
# Every call to /forgot-password, /resend-verification and the avatar upload answered
# 500. Login was fine only because it already declared the parameter to set the
# auth cookie, which is exactly why a hand test of login did not reveal it.
#
# These tests switch the limiter back on for the duration, so the wrapper actually
# runs. No Redis needed: rate_limit.py is built with in_memory_fallback_enabled, so an
# unreachable Redis silently degrades to in-process counting.


@pytest.fixture
def live_limiter():
    """
    Enables the real limiter for one test and puts it back afterwards.

    The restore is in a finally-equivalent (fixture teardown) on purpose: leaking
    enabled=True into the rest of the session would start handing out 429s to tests
    that share this IP, which is every single one of them.
    """
    limiter.enabled = True
    limiter.reset()
    yield limiter
    limiter.enabled = False
    limiter.reset()


@pytest.mark.asyncio
async def test_forgot_password_answers_while_the_limiter_is_live(live_limiter):
    """
    The route returns a plain dict, so slowapi has to be handed the endpoint's
    `response` argument to write headers into. Without it this is a 500, not a 200.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/users/forgot-password",
            json={"email": f"nobody_{uuid.uuid4().hex[:8]}@gym.com"},
        )

    assert res.status_code == 200, res.text
    # The generic body is a security property in its own right - it must not start
    # confirming whether the address exists.
    assert "If that email is registered" in res.json()["message"]


@pytest.mark.asyncio
async def test_resend_verification_answers_while_the_limiter_is_live(live_limiter):
    """Same shape of route, same failure mode, different limit."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/users/resend-verification",
            json={"email": f"nobody_{uuid.uuid4().hex[:8]}@gym.com"},
        )

    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_the_limit_headers_actually_reach_the_client(live_limiter):
    """
    Pins the reason headers_enabled was turned on: the login page reads Retry-After
    off a 429 instead of guessing a flat 60 seconds. If these headers stop being
    emitted, that countdown silently falls back to the guess with nothing failing.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/users/resend-verification",
            json={"email": f"nobody_{uuid.uuid4().hex[:8]}@gym.com"},
        )
        assert "x-ratelimit-limit" in res.headers

        # The limit is 1/15minutes, so the next one is refused.
        blocked = await ac.post(
            "/api/users/resend-verification",
            json={"email": f"nobody_{uuid.uuid4().hex[:8]}@gym.com"},
        )

    assert blocked.status_code == 429
    retry_after = int(blocked.headers["retry-after"])
    assert 0 < retry_after <= 15 * 60
