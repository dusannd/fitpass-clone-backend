# FitPass Clone - Gym Management & Access API

A robust, asynchronous backend API designed for gym management, subscription tracking, and physical door access control via short-lived QR codes. 

Built with **FastAPI**, following **Domain-Driven Design (DDD)** principles, and utilizing **PostgreSQL** and **Redis** for state management and caching.

## Key Features

* **Role-Based Access Control (RBAC):** JWT-based authentication with distinct role management (Admin, Member).
* **Secure Door Access (QR Tokens):** 
  * Generates short-lived JWT-based QR tokens (expires in 60 seconds) to prevent credential sharing via screenshots.
  * **Anti-Replay Protection:** Utilizes **Redis** to cache scanned tokens, preventing malicious actors from reusing a valid QR code to trigger the physical door mechanism multiple times.
* **Subscription Management:** Business logic for purchasing, tracking, and validating user gym plans.
* **Background Processing:** Integrated `APScheduler` to automatically detect and deactivate expired subscriptions asynchronously without blocking the main event loop.
* **Admin Analytics:** Endpoints to track daily successful and failed physical entry attempts, aiding in gym capacity monitoring.

## Tech Stack

* **Framework:** FastAPI (Python 3.10+)
* **Database:** PostgreSQL (Async via `asyncpg` & `SQLAlchemy`)
* **Caching / Anti-Replay:** Redis
* **Authentication:** JWT (JSON Web Tokens), `passlib` for bcrypt password hashing
* **Infrastructure:** Docker & Docker Compose
* **Architecture:** Domain-Driven Design (DDD) module layout

## Project Structure

```text
app/
├── api/          # API Routers (Controllers)
├── core/         # Core settings, Database setup, Security logic, Redis client
├── models/       # SQLAlchemy Database Models
├── schemas/      # Pydantic Models (Data Validation & Serialization)
└── services/     # Business Logic & Background Jobs (Scheduler)
```

## Getting Started

### 1. Start Infrastructure (PostgreSQL & Redis)
Ensure Docker is installed, then spin up the database and cache containers:
```bash
docker-compose up -d
```

### 2. Configure Environment
Create a `.env` file in the root directory (ensure this file is ignored by version control):
```env
SECRET_KEY=your_super_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

### 3. Install Dependencies
Activate your Python virtual environment and install the required packages:
```bash
pip install -r requirements.txt
```

### 4. Run the Application
Start the FastAPI uvicorn server:
```bash
uvicorn app.main:app --reload
```

* Base API URL: `http://localhost:8000`
* Swagger UI Documentation: `http://localhost:8000/docs`

## Security Architecture

This project implements standard security practices:
- Passwords are never stored in plain text (Bcrypt hashing is enforced).
- JWTs are utilized for stateless API authentication.
- Physical access control endpoints are protected against replay attacks using Redis TTL (Time-To-Live) mechanisms.
- Database operations are strictly asynchronous, preventing thread-blocking under concurrent loads.

