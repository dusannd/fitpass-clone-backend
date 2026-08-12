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
    start_scheduler()
    await check_redis_connection()

    yield

    print("Shutting down FastAPI server...")
    await close_redis_connection()

app = FastAPI(
    title="FitPass Clone / Gym API",
    description="Backend API for gym management and QR access",
    version="4.1.0",
    lifespan=lifespan
)


app.add_middleware(
    CORSMiddleware,
    allow_origins = settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- SECURITY HEADERS ---
# Swagger UI loads swagger-ui-bundle.js and swagger-ui.css from cdn.jsdelivr.net,
# so a "default-src 'self'" policy renders /docs as a blank page. These three are
# a developer tool with no user data on them, so they are the one place the policy
# is skipped - every actual API response still gets it.
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    Stamps the baseline security headers onto every response.

    Registered AFTER the CORS middleware on purpose: add_middleware inserts at the
    front of the stack, so whatever is added last ends up outermost. That means
    CORS preflight responses get these headers too.
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


# --- GLOBAL ERROR HANDLER ---
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Turns any unhandled crash into a generic 500 instead of a stack trace.

    Without this the client receives the raw exception, which for a database error
    means the SQL statement and often the connection details along with it.

    Only genuinely unhandled errors reach here. FastAPI routes HTTPException and
    RequestValidationError to their own handlers further down the stack, so the
    existing 400/403/404 responses - and slowapi's 429 - are untouched.

    logger.exception is the whole point of the function: the traceback still has to
    reach the server log, it just must not reach the client.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again later."},
    )

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