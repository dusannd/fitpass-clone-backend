import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.rate_limit import limiter
from app.core.config import settings

logger = logging.getLogger(__name__)

# --- 1. CORE IMPORTS ---
from app.core.database import engine, Base
from app.core.redis_client import check_redis_connection, close_redis_connection
from app.services.email import describe_email_provider
from app.services.scheduler import start_scheduler
from app.services.storage import STATIC_DIR, AVATAR_DIR

# --- 2. MODEL IMPORTS (Needed for SQLAlchemy to create tables) ---
from app.models.user import User, Role, UserProfile
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.access import EntryLog
from app.models.workout import WorkoutPlan, Exercise
from app.models.coaching import TrainerClientLink, Appointment

# --- 3. ROUTER IMPORTS ---
from app.api import users, subscriptions, access, admin, worker, payments, trainer, workouts, coaching



# --- LIFESPAN (Startup & Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):

    print("Starting up FastAPI server...")
    # Printed at boot because the provider is chosen silently from whichever
    # credentials happen to be in .env - a missing email is otherwise indis-
    # tinguishable from a provider that was never picked in the first place.
    print(f"Email provider: {describe_email_provider()}")
    start_scheduler()
    await check_redis_connection()

    yield

    print("Shutting down FastAPI server...")
    await close_redis_connection()

app = FastAPI(
    title="FitPass Clone / Gym API",
    description="Backend API for gym management and QR access",
    version="4.2.0",
    lifespan=lifespan
)


# ==========================================
# MIDDLEWARE - THE REGISTRATION ORDER BELOW IS LOAD-BEARING
# ==========================================
# add_middleware inserts at the FRONT of the stack, so whatever is registered
# last ends up outermost. The three blocks below therefore produce:
#
#   ServerErrorMiddleware -> add_security_headers -> CORSMiddleware
#                         -> catch_unhandled_errors -> ExceptionMiddleware -> router
#
# which is what lets a crash come back as an ordinary response that still passes
# through CORS and the header stamping on its way out. Reorder these and the 500
# silently loses those headers again - test_middleware_order_is_load_bearing is
# there to catch exactly that.

GENERIC_500_DETAIL = "Internal server error. Please try again later."


def internal_error_response() -> JSONResponse:
    """
    The only 500 body a client is ever allowed to see.

    Shared by the middleware and the fallback handler below so the two can never
    drift into answering differently.
    """
    return JSONResponse(status_code=500, content={"detail": GENERIC_500_DETAIL})


# --- 1. INNERMOST: turn a crash into a normal response ---
@app.middleware("http")
async def catch_unhandled_errors(request: Request, call_next):
    """
    Catches unhandled exceptions here rather than leaving them to the exception
    handler at the bottom of this file.

    The reason is placement. A handler registered for the Exception key is pulled
    out of ExceptionMiddleware by build_middleware_stack and handed to
    ServerErrorMiddleware, which sits ABOVE every user middleware. A 500 produced
    up there has already skipped CORS and the security headers - so a browser on a
    different origin blocks the response outright and the SPA sees a network error
    with no status instead of a clean 500.

    Catching in here means the 500 is just a response travelling back up the stack
    like any other.

    HTTPException, RequestValidationError and slowapi's RateLimitExceeded never
    arrive here - ExceptionMiddleware sits further in and resolves them first.
    Client disconnects do not either: anyio raises CancelledError, which derives
    from BaseException, so it unwinds normally instead of becoming a bogus 500.
    """
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return internal_error_response()


# --- 2. CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins = settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- 3. OUTERMOST: security headers ---
# Swagger UI loads swagger-ui-bundle.js and swagger-ui.css from cdn.jsdelivr.net,
# so a "default-src 'self'" policy renders /docs as a blank page. These three are
# a developer tool with no user data on them, so they are the one place the policy
# is skipped - every actual API response still gets it.
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    Stamps the baseline security headers onto every response.

    Registered last so it ends up outermost, which is what makes it reach CORS
    preflight responses too - CORSMiddleware answers those itself and never calls
    inward, so anything registered below it would miss them.
    """
    response = await call_next(request)

    # 1. Never let a browser second-guess the declared content type, and never
    #    allow the API to be framed.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"

    # 2. HSTS only when the request genuinely arrived over TLS. Browsers ignore
    #    this header on plain HTTP anyway, but the Vite dev server proxies /api
    #    over HTTPS and would hand it straight to the browser. HSTS is keyed by
    #    host and ignores the port, so that would pin *localhost* to HTTPS for a
    #    year and break every other http://localhost project on the machine.
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )
    if is_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # 3. The API only ever answers with JSON, so nothing legitimate needs to load
    #    from anywhere else.
    if request.url.path not in DOCS_PATHS:
        response.headers["Content-Security-Policy"] = "default-src 'self';"

    return response


# --- LAST RESORT: the net above the middleware ---
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Answers with the same generic 500 for anything catch_unhandled_errors cannot
    reach - a failure inside the security header middleware, or inside CORS.

    Registering the Exception key installs this on ServerErrorMiddleware, the
    outermost layer of all, so it covers what the middleware above cannot. That
    also means a 500 produced here has bypassed CORS and the header stamping,
    which is precisely why it is the fallback and not the main path.

    The two never both run for one request: if the middleware handled the error,
    execution never gets here, so nothing is logged twice.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return internal_error_response()

# --- STATIC FILES (Profile pictures) ---
# Mounted under /api on purpose: the Vite dev server runs on HTTPS and already
# proxies /api to us, so avatars load without CORS or mixed content warnings.
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- ROUTER REGISTRATION (This order determines the layout in Swagger UI) ---
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["Subscriptions"])
app.include_router(access.router, prefix="/api/access", tags=["Door Access"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin Dashboard"])
app.include_router(payments.router, prefix="/api/payments", tags=["Stripe Payments"])
app.include_router(worker.router, prefix="/api/worker", tags=["Desk Worker"])
app.include_router(trainer.router, prefix="/api/trainer", tags=["Trainer Dashboard"])
app.include_router(workouts.router, prefix="/api/workouts", tags=["Workouts (Members)"])
app.include_router(coaching.router, prefix="/api/coaching", tags=["Coaching (1-on-1)"])



# --- HEALTH CHECK (Placed at the bottom) ---
@app.get("/health", tags=["System Health"])
async def root():
    return {"status": "ok", "message": "Gym API is running"}