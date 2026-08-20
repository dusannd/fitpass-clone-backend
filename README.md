<div align="center">
<img src="./assets/banner.png" alt="FitPass Clone — Enterprise Gym Management API" width="100%">
**Scalable, production-ready backend infrastructure for modern fitness franchises and gym networks.**
 
Built on FastAPI and Async PostgreSQL — engineered for security, high concurrency, and data integrity.
 
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688.svg?style=flat&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Async-336791.svg?style=flat&logo=postgresql)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-Caching-DC382D.svg?style=flat&logo=redis)](https://redis.io/)
[![Stripe](https://img.shields.io/badge/Stripe-Payments-008CDD.svg?style=flat&logo=stripe)](https://stripe.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Version](https://img.shields.io/badge/version-4.2.0-blueviolet.svg?style=flat)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat)]()
 
</div>
---
 
## Table of Contents
 
- [Overview](#overview)
- [Core System Architecture](#core-system-architecture)
- [What's New in v4.2](#whats-new-in-v42-latest-architecture-update)
- [Technical Stack](#technical-stack)
- [Infrastructure Setup](#infrastructure-setup)
- [Production Deployment](#production-deployment)
- [Test Suite](#test-suite)
---
 
## Overview
 
FitPass Clone orchestrates the entire operational lifecycle of a gym — from Stripe-driven recurring billing and cryptographic physical door access control, to human resources management, role-based workflows, and a 1-on-1 personal training ecosystem.
 
---
 
## Core System Architecture
 
The application is structured around four primary domains. For entity-relationship and data-flow diagrams, see [`SCHEMAS.md`](./SCHEMAS.md).
 
| Domain | Description |
|---|---|
| **Identity & Access Management** | A custom-built, cryptographically secure QR-code turnstile system. Tokens are short-lived, intent-bound (`ENTRY`/`EXIT`), and validated against a Redis-backed anti-passback state machine to guarantee physical security and prevent credential sharing. A single door policy engine evaluates location, weekday and opening-hour restrictions on the gym clock, so the turnstile and the front desk always return the same verdict. |
| **Financial Operations** | Seamlessly integrated with Stripe. Uses robust, idempotent webhooks to handle recurring subscription cycles, dunning-driven access revocation, and complex access parameters (e.g., location-specific or time-restricted memberships). Members manage their own cards, invoices and cancellations through a hosted Billing Portal session. |
| **Facility Operations** | Extensive Role-Based Access Control (RBAC). Administrators manage HR (hiring/firing) and read live financial and attendance analytics, while desk staff monitor real-time facility capacity, inspect member standing, and securely execute audited manual door overrides that stay synchronized with the Redis presence state. |
| **Performance & Coaching** | A built-in marketplace for personal training, gated on the subscription perks a member actually paid for. Supports trainer-client relationship mapping, session scheduling, and detailed workout templates. Members log telemetry set by set (weight, reps, sets) for advanced analytical tracking and automatic personal-record detection. |
 
---
 
## What's New in v4.2 (Latest Architecture Update)
 
Version 4.2 takes the platform from "runs on my machine" to a deployable product — a containerized production stack, a unified door policy, monetizable plan tiers, and a hardened billing and messaging layer.
 
- **Containerized Production Deployment** — A slim `Dockerfile.prod` on `python:3.13-slim` running the API as a single Uvicorn process, and a five-service Compose stack (PostgreSQL, Redis, a one-shot migration runner, the API, and an Nginx-served frontend). Only port 80 is published, healthchecks gate the boot order, and named volumes carry the database and uploaded avatars across redeploys.
- **Resilient Billing Lifecycle** — `invoice.payment_failed` now revokes gym access on the first declined charge and restores it automatically once Stripe's dunning retry clears, account deletion cancels every active subscription upstream before the local rows vanish, and a Customer Portal endpoint hands card management back to the member.
- **Unified Door Policy Engine** — Location and time-window validation was extracted into a single shared module consumed by both the turnstile and the desk panel, eliminating the class of bug where a worker was told "allowed to enter" for a member the door itself would refuse. Weekday and hour checks are evaluated in gym-local time rather than UTC.
- **Tiered Plans with Enforced Perks** — Subscription plans carry real, migration-backfilled perk flags instead of a decorative tier label. Coaching requests and session bookings are gated on the trainer perk, so a premium plan finally sells something the entry plan does not.
- **Hardened Realtime Channel** — WebSocket refusals are accepted before closing so the browser receives a genuine close code instead of an indistinguishable `1006`, and the connection registry is identity-checked, preventing a phone that hands off from mobile data to gym WiFi from unregistering its own live socket.
- **Transactional Email Pipeline** — Branded HTML templates for verification and password reset, delivered over Resend or Gmail SMTP, with every link built from the configured `FRONTEND_URL` and every interpolated value HTML-escaped in a single render pass.
- **Analytics on the Gym Clock** — MRR normalized to a 30-day window, 24-bucket peak-hour reporting, weekly breakdowns and a dedicated HR staff endpoint that filters roles in the database instead of paging through the user table. All time-of-day reporting converts UTC to gym-local before bucketing, and "today" is filtered on a half-open range so the timestamp index still applies.
- **Defence in Depth** — Rate limiting moved to Redis with an in-memory fallback, so counters are shared across workers and survive a cache outage. Every 429 now carries `Retry-After`, unhandled errors return a generic 500 without losing CORS or security headers, and the email-enumeration timing leak on password reset was closed by queuing the mail as a background task.
 
---
 
### Previously in v4.1
 
Version 4.1 was the security rewrite: authentication moved from localStorage tokens to HTTP-Only, `SameSite=Lax` JWT cookies, invisible honeypots and reCAPTCHA v3 were added to every unauthenticated endpoint, avatar uploads were routed through a Pillow pipeline that validates magic bytes and strips EXIF metadata, Stripe webhooks were made idempotent, and the ORM's N+1 bottlenecks were resolved with `selectinload` and targeted B-Tree indexes.
 
---
 
## Technical Stack
 
| Layer | Technology |
|---|---|
| **Framework** | FastAPI 0.136, Python 3.10+ (3.13 in the production image) |
| **Database** | PostgreSQL 15, SQLAlchemy 2.0 (AsyncIO engine) |
| **Migrations** | Alembic |
| **Caching & Rate Limiting** | Redis, SlowAPI |
| **Authentication** | JWT via HTTP-Only Cookies, Passlib (Bcrypt) |
| **Realtime** | WebSockets (cookie-authenticated turnstile event push) |
| **Scheduling** | APScheduler (in-process, single-worker by design) |
| **Media Processing** | Pillow (PIL) |
| **Payments** | Stripe API integration |
| **Deployment** | Docker, Docker Compose, Nginx reverse proxy |
| **Testing** | Pytest & HTTPX (async integration tests isolated via SQLite StaticPool in-memory DB) |
 
---
 
## Infrastructure Setup
 
### 1. Prerequisites
 
Ensure you have Docker, Docker Compose, and Python 3.10+ installed on your host machine.
 
### 2. Services Initialization
 
Spin up the PostgreSQL database and Redis caching layer:
 
```bash
docker-compose up -d
```
 
### 3. Application Environment
 
Create an isolated virtual environment and install the required dependencies:
 
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```
 
### 4. Environment Configuration
 
Create a `.env` file in the root directory to configure the system instances:
 
```env
DATABASE_URL=postgresql+asyncpg://postgres:admin@localhost:5433/fitpass_db
REDIS_HOST=localhost
REDIS_PORT=6379
 
SECRET_KEY=your_cryptographically_secure_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
 
STRIPE_API_KEY=sk_test_your_stripe_key
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
 
# Client origin - used for CORS and for every link inside an outgoing email
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
 
# Reporting timezone. Timestamps are stored in UTC and displayed in gym-local time
GYM_TIMEZONE=Europe/Belgrade
 
# Transactional email - configure either Resend or SMTP, not both
EMAIL_FROM=no-reply@your_domain.com
EMAIL_FROM_NAME=FitPass
RESEND_API_KEY=re_your_resend_key
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your_address@gmail.com
SMTP_PASS=your_gmail_app_password
 
# Optional Security Features
FEATURE_RECAPTCHA=true
RECAPTCHA_SECRET=your_google_recaptcha_secret
```
 
> `GYM_TIMEZONE` drives every time-of-day report — peak hours, daily counters and weekly grouping. `tzdata` is pinned in `requirements.txt` on purpose: Windows ships no IANA database, so removing it breaks timezone resolution at runtime rather than at install time.
 
### 5. Schema Deployment
 
Execute Alembic migrations to apply the relational schema to the database:
 
```bash
alembic upgrade head
```
 
### 6. Bootstrapping the Server
 
Start the Uvicorn ASGI server:
 
```bash
uvicorn app.main:app --reload
```
 
The API is now operational at `http://127.0.0.1:8000`.
 
> To explore the interactive OpenAPI documentation, navigate to `http://127.0.0.1:8000/docs`.
 
---
 
## Production Deployment
 
The production stack is fully containerized and self-contained — it ships its own database and cache, so it does not depend on the development `docker-compose.yml`.
 
```bash
cp .env.prod.example .env.prod   # then fill in the secrets
docker compose -f docker-compose.prod.yml up -d --build
```
 
Four details worth knowing before going live:
 
- **Port 80 is the only door.** PostgreSQL, Redis and the API publish no ports at all; they are reachable exclusively from inside the Compose network, with Nginx proxying `/api` through to the backend.
- **Migrations run as a one-shot service.** The application lifespan runs neither Alembic nor `create_all`, so a dedicated `migrate` container executes `alembic upgrade head` and exits before the API is allowed to start.
- **The auth cookie is issued `Secure`.** A real domain therefore requires TLS terminated at Nginx — over plain HTTP the browser silently discards the session cookie.
- **Data lives in named volumes.** `docker compose down` keeps every user, plan and entry log; `docker compose down -v` is the only command that deletes them.
 
---
 
## Test Suite
 
The repository includes a comprehensive suite of 133 asynchronous integration tests across 18 modules, covering the turnstile state machine, Stripe webhooks, the WebSocket lifecycle, rate limiting, email transport and the admin analytics endpoints. It uses dependency overrides and an isolated SQLite in-memory database to validate end-to-end business logic without mutating production data.
 
Execute the test suite via:
 
```bash
pytest -q
```
 
---
 
<div align="center">
Built with FastAPI, PostgreSQL, and Redis.
 
</div>