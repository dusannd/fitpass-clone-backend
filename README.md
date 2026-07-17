# FitPass Clone / Gym Management API (v4)

A comprehensive, production-ready backend API for managing a modern gym franchise. Built with **FastAPI**, **PostgreSQL (Async)**, and **Redis**, this system handles everything from Stripe subscription payments and dynamic QR-code door access to HR management and 1-on-1 personal training workflows.

## Key Features

**Role-Based Access Control (RBAC)**
- **Admin**: Full system control, HR panel (hiring/firing), and gym analytics.
- **Worker (Desk Staff)**: View user subscription statuses and perform manual door overrides.
- **Trainer**: Accept clients, schedule appointments, and create public/private workout plans.
- **Member**: Buy subscriptions, track workouts, and generate dynamic QR codes for gym entry.

**Payments & Subscriptions**
- Seamless integration with **Stripe Checkout**.
- Automated Stripe Webhooks for real-time subscription activation.
- Background tasks (APScheduler) for auto-deactivating expired subscriptions.
- Complex subscription rules (time limits, specific days, specific gym locations).

**Smart Door Access (Anti-Fraud)**
- Generates 60-second short-lived JWT QR tokens.
- Validates tokens and checks active subscriptions at the scanner.
- Prevents QR code sharing/replay attacks using **Redis**.

**Coaching & Workout Tracking**
- Request 1-on-1 coaching and schedule appointments.
- Trainers can create detailed workout plans with specific exercises (sets, reps, rest times).
- Members can log active workout sessions and track weight/progress over time.

---

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL with SQLAlchemy 2.0 (Async)
- **Migrations**: Alembic
- **Caching & Security**: Redis (for rate limiting and QR token invalidation)
- **Authentication**: JWT (JSON Web Tokens) & Passlib (Bcrypt)
- **Payments**: Stripe API
- **Testing**: Pytest & HTTPX (Async tests)

---

## Getting Started

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.10+

### 2. Infrastructure Setup (Database & Redis)
Spin up the PostgreSQL and Redis containers using Docker:
```bash
docker-compose up -d
```

### 3. Application Setup
Create a virtual environment and install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Environment Variables
Create a `.env` file in the root directory:
```env
DATABASE_URL=postgresql+asyncpg://postgres:admin@localhost:5433/fitpass_db
REDIS_HOST=localhost
REDIS_PORT=6379
SECRET_KEY=your_super_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
STRIPE_API_KEY=sk_test_your_stripe_key
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
```

### 5. Run Database Migrations
Apply the Alembic migrations to create the database schema:
```bash
alembic upgrade head
```

### 6. Start the Server
```bash
uvicorn app.main:app --reload
```
The API will be available at `http://127.0.0.1:8000`.
Check out the interactive Swagger UI documentation at `http://127.0.0.1:8000/docs`.

---

## Testing
The project includes a robust suite of async integration tests utilizing an in-memory SQLite database to ensure code reliability without touching your production data.

Run the test suite:
```bash
pytest -v
```

---

## License
This project is licensed under the AGPLv3 License - see the [LICENSE](LICENSE) file for details.