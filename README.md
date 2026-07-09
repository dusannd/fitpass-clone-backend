# 🏋️‍♂️ FitPass Clone - Gym Management & Access API

A robust, asynchronous backend API designed for gym networks (SaaS). It handles subscription tracking, multi-gym access management, physical door access control via short-lived, secure QR codes, and automated payments.

Built with **FastAPI**, following **Domain-Driven Design (DDD)** principles, and utilizing **PostgreSQL** (with Alembic migrations) and **Redis** for anti-replay security caching.

## 🌟 What is this application?
This API serves as the brain for a modern gym network (like FitPass). 
- **Users** buy subscription plans via Stripe and generate time-sensitive QR codes on their phones.
- **Door Scanners** (IoT devices) read the QR code and ask the API: *"Can this person enter this specific gym right now?"*
- **Desk Workers** can manually override doors if a user's phone dies and check user statuses.
- **Admins** can create complex rules, monitor business analytics, and audit worker actions to prevent fraud.

---

## 🚀 What's New in v3.0.0 (Production-Ready Release)

In version 3.0.0, the system evolved into a fully monetized, tested, and secure platform:

* **Stripe Payment Gateway Integration:**
  * Added `/checkout-session` for generating secure payment URLs.
  * Implemented a secure `/webhook` to automatically provision subscriptions upon successful payment.
* **Advanced Analytics & Anti-Fraud Audit:**
  * **User Dossier:** Admins can now track a specific user's complete entry history (when, where, and how they entered).
  * **Worker Audit Log:** A dedicated endpoint to monitor all manual door overrides by desk workers to prevent unauthorized access fraud.
* **Soft Delete Architecture & Database Integrity:**
  * Subscription Plans can now be safely archived (`is_active=False`) without breaking existing user subscriptions or historical entry logs.
  * Gym locations utilize `ondelete="SET NULL"` for safe removal.
  * Added an Admin maintenance endpoint to reset PostgreSQL sequences.
* **Strict Data Validation & Resilience:**
  * Implemented strict Pydantic constraints (e.g., preventing negative prices or zero-day durations).
  * Added Guard Clauses to prevent `500 Internal Server Error` on foreign key violations (returning graceful 400/404s instead).
  * System now strictly prevents users from buying overlapping double subscriptions.
* **Automated Testing:**
  * Introduced an asynchronous testing suite using `pytest` and `httpx` (`ASGITransport`).

---

## 🛠️ Tech Stack

* **Framework:** FastAPI (Python 3.10+)
* **Database:** PostgreSQL (Async via `asyncpg` & `SQLAlchemy`) + Alembic for migrations
* **Caching / Anti-Replay:** Redis
* **Authentication:** JWT (JSON Web Tokens), `passlib` for bcrypt password hashing
* **Payments:** Stripe Python SDK
* **Testing:** Pytest, HTTPX
* **Infrastructure:** Docker & Docker Compose

## 📁 Project Structure

```text
fitpass-clone/
├── app/
│   ├── api/          # API Routers (Controllers, Payments, Webhooks)
│   ├── core/         # Core settings, Database setup, Security logic, Redis
│   ├── models/       # SQLAlchemy Database Models
│   ├── schemas/      # Pydantic Models (Data Validation & Serialization)
│   └── services/     # Business Logic & Background Jobs (Scheduler)
├── tests/            # Automated Pytest suite
│   └── test_main.py  
├── alembic/          # Database migration scripts
├── docker-compose.yml
└── pytest.ini        # Pytest configuration
```

## ⚙️ Getting Started

### 1. Start Infrastructure (PostgreSQL & Redis)
Ensure Docker is installed, then spin up the database and cache containers:
```bash
docker-compose up -d
```

### 2. Apply Database Migrations
Create the tables by running Alembic:
```bash
alembic upgrade head
```

### 3. Configure Environment
Create a `.env` file in the root directory:
```env
SECRET_KEY=your_super_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Stripe Configuration (Test Mode)
STRIPE_API_KEY=sk_test_your_stripe_secret_key
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
```

### 4. Run the Application
Start the FastAPI uvicorn server:
```bash
uvicorn app.main:app --reload
```
API Documentation will be available at: `http://localhost:8000/docs`

### 5. Run Automated Tests
To run the test suite asynchronously:
```bash
pytest
```
