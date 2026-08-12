import logging

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.main import app, unhandled_exception_handler

HEALTH_URL = "/health"


# ==========================================
# 1. SECURITY HEADERS
# ==========================================
@pytest.mark.asyncio
async def test_security_headers_present_on_every_response():
    """
    The baseline headers land on an ordinary response.

    /health is used because it needs no cookie and touches no database - if this
    fails, the middleware itself is wrong rather than the route.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get(HEALTH_URL)

    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["content-security-policy"] == "default-src 'self';"


@pytest.mark.asyncio
async def test_hsts_absent_over_plain_http():
    """
    No HSTS on a plain HTTP request.

    This is the guard that keeps the dev machine safe. The Vite dev server runs on
    HTTPS and proxies /api through to this backend, so a header sent here reaches
    the browser as if it came from https://localhost. HSTS is keyed by host and
    ignores the port, so it would pin localhost to HTTPS for a year and break every
    other http://localhost project - a failure that looks like a broken project,
    not like a stray header.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get(HEALTH_URL)

    assert "strict-transport-security" not in res.headers


@pytest.mark.asyncio
async def test_hsts_present_when_forwarded_proto_is_https():
    """
    In production the TLS terminator sits in front of the app, so the request
    reaches us as HTTP with X-Forwarded-Proto set. HSTS has to survive that hop or
    it would never be sent anywhere that matters.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get(HEALTH_URL, headers={"X-Forwarded-Proto": "https"})

    assert res.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


@pytest.mark.asyncio
async def test_csp_is_skipped_on_the_docs_routes():
    """
    Swagger UI pulls its JS and CSS from cdn.jsdelivr.net, so "default-src 'self'"
    blocks both and /docs renders as a blank white page - with the real cause
    buried in the browser console. The other headers still apply there.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/docs")

    assert res.status_code == 200
    assert "content-security-policy" not in res.headers
    # Skipping the CSP must not have skipped everything else.
    assert res.headers["x-content-type-options"] == "nosniff"


# ==========================================
# 2. GLOBAL EXCEPTION HANDLER
# ==========================================
def build_boom_app() -> FastAPI:
    """
    A throwaway app carrying the real handler and one route that crashes.

    A route added to the shared app would stay mounted for the rest of the
    session, and there is no existing endpoint that raises on demand. Importing
    the handler rather than re-declaring it is what keeps this a test of the
    production code.
    """
    boom_app = FastAPI()
    boom_app.add_exception_handler(Exception, unhandled_exception_handler)

    @boom_app.get("/boom")
    async def boom():
        raise ValueError("secret connection string leaked here")

    return boom_app


@pytest.mark.asyncio
async def test_unhandled_exception_returns_generic_500():
    """
    A crash answers with a fixed message and nothing about what actually broke.

    raise_app_exceptions=False is mandatory: Starlette's ServerErrorMiddleware
    ALWAYS re-raises after calling the handler, so that servers can log it and test
    clients can choose to see it. Left at the default, this test would blow up with
    the ValueError instead of ever reading the response.
    """
    transport = ASGITransport(app=build_boom_app(), raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/boom")

    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error. Please try again later."}

    # The actual security property. A 500 alone proves nothing - Starlette's own
    # default handler returns one too, with the traceback attached.
    body = res.text
    assert "secret connection string" not in body
    assert "ValueError" not in body
    assert "Traceback" not in body


@pytest.mark.asyncio
async def test_unhandled_exception_is_logged(caplog):
    """
    The traceback still has to reach the server log.

    Hiding an error from the client is only half the job - swallowing it entirely
    would leave a crash with no trace of it anywhere, which is worse than the leak
    this handler exists to close.
    """
    transport = ASGITransport(app=build_boom_app(), raise_app_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="app.main"):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.get("/boom")

    assert "Unhandled error on GET /boom" in caplog.text
    # logger.exception, not logger.error - the traceback is the useful part.
    assert "secret connection string leaked here" in caplog.text
