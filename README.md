# 🏋️‍♂️ FitPass Clone - Gym Management & Access API

A robust, asynchronous backend API designed for gym networks (SaaS). It handles subscription tracking, multi-gym access management, and physical door access control via short-lived, secure QR codes.

Built with **FastAPI**, following **Domain-Driven Design (DDD)** principles, and utilizing **PostgreSQL** (with Alembic migrations) and **Redis** for anti-replay security caching.

## 🌟 What is this application?
This API serves as the brain for a modern gym network (like FitPass). 
- **Users** buy subscription plans and generate time-sensitive QR codes on their phones.
- **Door Scanners** (IoT devices) read the QR code and ask the API: *"Can this person enter this specific gym right now?"*
- **Desk Workers** can manually override doors if a user's phone dies and check user statuses.
- **Admins** can create complex rules: E.g., *"The Student Plan is only valid at the Downtown Gym, Monday to Friday, from 09:00 to 16:00."*

---

## 🚀 What's New in v2.0.0 (Major Update)

In version 2.0.0, the system evolved from a simple API to a full-fledged SaaS platform:

* **Dynamic Subscription Engine:**
  * Added **Gym Locations**: A single plan can now grant access to specific gym locations (Many-to-Many).
  * Added **Time & Day Rules**: Subscriptions can now be restricted by time (e.g., 09:00 - 16:00) and days of the week.
* **Role-Based Access Control (RBAC):** 
  * Replaced flat roles with a Many-to-Many `Role` architecture. Users can now hold multiple roles simultaneously (e.g., `member` and `worker`).
  * Implemented dynamic `RequireRole` class dependencies for endpoint protection.
* **Desk Worker API:**
  * Added dedicated endpoints for gym receptionists to manually open doors (logging the `worker_id` for accountability).
  * Added user status checking (fetching active plans, expiration dates, and remaining days).
* **Database Migrations:** Transitioned from manual table creation to **Alembic** asynchronous migrations.

---

## 🛠️ Tech Stack

* **Framework:** FastAPI (Python 3.10+)
* **Database:** PostgreSQL (Async via `asyncpg` & `SQLAlchemy`) + Alembic for migrations
* **Caching / Anti-Replay:** Redis
* **Authentication:** JWT (JSON Web Tokens), `passlib` for bcrypt password hashing
* **Infrastructure:** Docker & Docker Compose

## 📁 Project Structure

```text
app/
├── api/          # API Routers (Controllers)
├── core/         # Core settings, Database setup, Security logic, Redis client
├── models/       # SQLAlchemy Database Models
├── schemas/      # Pydantic Models (Data Validation & Serialization)
└── services/     # Business Logic & Background Jobs (Scheduler)
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
```

### 4. Run the Application
Start the FastAPI uvicorn server:
```bash
uvicorn app.main:app --reload
```
API Documentation will be available at: `http://localhost:8000/docs`