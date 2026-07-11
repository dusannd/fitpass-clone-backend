from fastapi import FastAPI


# --- 1. CORE IMPORTS ---
from app.core.database import engine, Base
from app.core.redis_client import check_redis_connection
from app.services.scheduler import start_scheduler

# --- 2. MODEL IMPORTS (Needed for SQLAlchemy to create tables) ---
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.access import EntryLog

# --- 3. ROUTER IMPORTS ---
from app.api import users, subscriptions, access, admin, worker, payments

app = FastAPI(
    title="FitPass Clone / Gym API",
    description="Backend API for gym management and QR access",
    version="4.0.0-dev"
)

# --- STARTUP EVENTS ---
@app.on_event("startup")
async def startup():
    # 1. Database table creation is now handled by Alembic migrations.
    # Base.metadata.create_all is removed.

    # 2. Start our background cron jobs
    start_scheduler()

    # 3. Check Redis connection
    await check_redis_connection()

# --- ROUTER REGISTRATION (This order determines the layout in Swagger UI) ---
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["Subscriptions"])
app.include_router(access.router, prefix="/api/access", tags=["Door Access"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin Dashboard"])
app.include_router(payments.router, prefix="/api/payments", tags=["Stripe Payments"])
app.include_router(worker.router, prefix="/api/worker", tags=["Desk Worker"])


# --- HEALTH CHECK (Placed at the bottom) ---
@app.get("/health", tags=["System Health"])
async def root():
    return {"status": "ok", "message": "Gym API is running"}