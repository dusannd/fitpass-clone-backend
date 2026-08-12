import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from httpx import AsyncClient, ASGITransport

from app.main import (
    app,
    add_security_headers,
    catch_unhandled_errors,
    unhandled_exception_handler,
)

HEALTH_URL = "/health"
ORIGIN = "http://localhost:5173"
GENERIC_500 = {"detail": "Internal server error. Please try again later."}


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
# 2. MIDDLEWARE ORDER
# ==========================================
def middleware_chain(application: FastAPI) -> list[str]:
    """
    Names every layer of the built stack, outermost first.

    The two BaseHTTPMiddleware layers are indistinguishable by class, so the
    dispatch function's name is used where there is one. build_middleware_stack
    returns a fresh stack and is not assigned back, so calling it here does not
    disturb the running application.
    """
    node, names = application.build_middleware_stack(), []
    while node is not None:
        dispatch = getattr(node, "dispatch_func", None)
        names.append(getattr(dispatch, "__name__", None) or type(node).__name__)
        node = getattr(node, "app", None)
    return names


def test_middleware_order_is_load_bearing():
    """
    The registration order in main.py decides whether a 500 keeps its headers.

    catch_unhandled_errors has to sit INSIDE CORSMiddleware, otherwise the response
    it builds never passes through CORS and the browser blocks it. Swapping two
    lines in main.py is an easy, invisible way to undo that, so the relationship is
    pinned here.

    Only relative positions are asserted, not an exact list: a framework upgrade
    that inserts an unrelated internal layer should not fail this test.
    """
    chain = middleware_chain(app)

    for name in ("add_security_headers", "CORSMiddleware", "catch_unhandled_errors",
                 "ExceptionMiddleware"):
        assert name in chain, f"{name} missing from the stack: {chain}"

    assert (
        chain.index("add_security_headers")
        < chain.index("CORSMiddleware")
        < chain.index("catch_unhandled_errors")
        < chain.index("ExceptionMiddleware")
    ), f"middleware order changed: {chain}"


# ==========================================
# 3. UNHANDLED ERRORS
# ==========================================
def build_boom_app() -> FastAPI:
    """
    A throwaway app wired exactly like main.py, carrying routes that fail.

    The registration order below mirrors the real one and is checked against it by
    test_middleware_order_is_load_bearing. The production functions are imported
    rather than re-declared - a re-declared copy would only ever test itself.

    A throwaway app rather than routes bolted onto the shared app: anything mounted
    there stays for the rest of the session, and there is no existing endpoint that
    crashes on demand.
    """
    boom_app = FastAPI()

    boom_app.middleware("http")(catch_unhandled_errors)
    boom_app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    boom_app.middleware("http")(add_security_headers)
    boom_app.add_exception_handler(Exception, unhandled_exception_handler)

    @boom_app.get("/boom")
    async def boom():
        raise ValueError("secret connection string leaked here")

    @boom_app.get("/notfound")
    async def notfound():
        raise HTTPException(status_code=404, detail="User with ID 7 does not exist.")

    return boom_app


@pytest.mark.asyncio
async def test_unhandled_exception_returns_generic_500():
    """
    A crash answers with a fixed message and nothing about what actually broke.

    Note this runs on httpx's DEFAULT raise_app_exceptions=True: because the catch
    happens in a middleware, the exception is turned into a response before it can
    reach ServerErrorMiddleware, so nothing escapes. That is the whole reason the
    catch was moved inward.
    """
    transport = ASGITransport(app=build_boom_app())

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/boom")

    assert res.status_code == 500
    assert res.json() == GENERIC_500

    # The actual security property. A 500 alone proves nothing - Starlette's own
    # default handler returns one too, with the traceback attached.
    body = res.text
    assert "secret connection string" not in body
    assert "ValueError" not in body
    assert "Traceback" not in body


@pytest.mark.asyncio
async def test_500_carries_cors_headers():
    """
    The regression this arrangement exists for.

    A 500 built above the CORS layer arrives without Access-Control-Allow-Origin,
    so a browser on another origin discards it and the SPA's axios interceptor sees
    a network error with no status at all - instead of a 500 it could report. It
    does not show up in development, where VITE_API_BASE_URL=/api goes through the
    Vite proxy and every request is same-origin.
    """
    transport = ASGITransport(app=build_boom_app())

    async with AsyncClient(transport=transport, base_url="http://api.test") as ac:
        res = await ac.get("/boom", headers={"Origin": ORIGIN})

    assert res.status_code == 500
    assert res.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.asyncio
async def test_500_carries_security_headers():
    """
    The same journey back up the stack has to reach the header middleware too, or
    the one response most likely to be probed is the one without any headers.
    """
    transport = ASGITransport(app=build_boom_app())

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/boom")

    assert res.status_code == 500
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["content-security-policy"] == "default-src 'self';"


@pytest.mark.asyncio
async def test_http_exception_is_untouched():
    """
    Ordinary error responses must keep their own status and message.

    ExceptionMiddleware sits further in than the new catch, so HTTPException never
    reaches it - but "catch Exception and return 500" is exactly the shape of a
    change that swallows every 404 and 403 in the application.
    """
    transport = ASGITransport(app=build_boom_app())

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/notfound")

    assert res.status_code == 404
    assert res.json() == {"detail": "User with ID 7 does not exist."}


@pytest.mark.asyncio
async def test_unhandled_exception_is_logged(caplog):
    """
    The traceback still has to reach the server log.

    Hiding an error from the client is only half the job - swallowing it entirely
    would leave a crash with no trace of it anywhere, which is worse than the leak
    this exists to close.
    """
    transport = ASGITransport(app=build_boom_app())

    with caplog.at_level(logging.ERROR, logger="app.main"):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.get("/boom")

    assert "Unhandled error on GET /boom" in caplog.text
    # logger.exception, not logger.error - the traceback is the useful part.
    assert "secret connection string leaked here" in caplog.text


@pytest.mark.asyncio
async def test_exception_handler_still_answers_when_middleware_is_bypassed():
    """
    Why the Exception handler stays registered even though the middleware now does
    the work: it is the net for anything that fails ABOVE the middleware, in the
    header stamping or in CORS.

    This app deliberately has no middleware, so the error reaches
    ServerErrorMiddleware - the situation the handler exists for.

    raise_app_exceptions=False is mandatory on this one. ServerErrorMiddleware
    ALWAYS re-raises after calling its handler, so servers can log it and test
    clients can choose to see it. Left at the default, this would blow up with the
    ValueError rather than ever reading the response.
    """
    bare_app = FastAPI()
    bare_app.add_exception_handler(Exception, unhandled_exception_handler)

    @bare_app.get("/boom")
    async def boom():
        raise ValueError("secret connection string leaked here")

    transport = ASGITransport(app=bare_app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/boom")

    assert res.status_code == 500
    assert res.json() == GENERIC_500
    assert "secret connection string" not in res.text
