from fastapi import FastAPI
from contextlib import asynccontextmanager


# --- 1. CORE IMPORTS ---
from app.core.database import engine, Base
from app.core.redis_client import check_redis_connection, close_redis_connection
from app.services.scheduler import start_scheduler

# --- 2. MODEL IMPORTS (Needed for SQLAlchemy to create tables) ---
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
    version="4.0.0-dev",
    lifespan=lifespan
)

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