<div align="center">
<img src="./assets/banner.png" alt="FitPass Clone — Enterprise Gym Management API" width="100%">
**Scalable, production-ready backend infrastructure for modern fitness franchises and gym networks.**
 
Built on FastAPI and Async PostgreSQL — engineered for security, high concurrency, and data integrity.
 
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?style=flat&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Async-336791.svg?style=flat&logo=postgresql)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-Caching-DC382D.svg?style=flat&logo=redis)](https://redis.io/)
[![Stripe](https://img.shields.io/badge/Stripe-Payments-008CDD.svg?style=flat&logo=stripe)](https://stripe.com/)
[![Version](https://img.shields.io/badge/version-4.1.0-blueviolet.svg?style=flat)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat)]()
 
</div>
---
 
## Table of Contents
 
- [Overview](#overview)
- [Core System Architecture](#core-system-architecture)
- [What's New in v4.1](#whats-new-in-v41-latest-architecture-update)
- [Technical Stack](#technical-stack)
- [Infrastructure Setup](#infrastructure-setup)
- [Test Suite](#test-suite)
---
 
## Overview
 
FitPass Clone orchestrates the entire operational lifecycle of a gym — from Stripe-driven recurring billing and cryptographic physical door access control, to human resources management, role-based workflows, and a 1-on-1 personal training ecosystem.
 
---
 
## Core System Architecture
 
The application is structured around four primary domains. For entity-relationship and data-flow diagrams, see [`SCHEMAS.md`](./schemas.md).
 
| Domain | Description |
|---|---|
| **Identity & Access Management** | A custom-built, cryptographically secure QR-code turnstile system. Tokens are short-lived, intent-bound (`ENTRY`/`EXIT`), and validated against a Redis-backed anti-passback state machine to guarantee physical security and prevent credential sharing. |
| **Financial Operations** | Seamlessly integrated with Stripe. Uses robust, idempotent webhooks to handle recurring subscription cycles, automated access revocation, and complex access parameters (e.g., location-specific or time-restricted memberships). |
| **Facility Operations** | Extensive Role-Based Access Control (RBAC). Administrators manage HR (hiring/firing), while desk staff monitor live facility capacity, inspect member standing, and securely execute audited manual door overrides. |
| **Performance & Coaching** | A built-in marketplace for personal training. Supports trainer-client relationship mapping, session scheduling, and detailed workout templates. Members log telemetry (weight, sets, reps) for advanced analytical tracking. |
 
---
 
## What's New in v4.1 (Latest Architecture Update)
 
Version 4.1 introduces critical improvements to system security, caching infrastructure, and database optimization.
 
- **Zero-Trust Authentication** — Migrated from localStorage JSON tokens to strict HTTP-Only, `SameSite=Lax` JWT cookies, fundamentally neutralizing Cross-Site Scripting (XSS) vectors across the platform.
- **Automated Threat Mitigation** — Integrated invisible honeypot fields and Google reCAPTCHA v3 across all unauthenticated endpoints to actively drop bot traffic and credential stuffing attempts.
- **Secure Media Processing Pipeline** — A robust local media storage layer via Pillow. Validates magic bytes, clamps maximum pixels to prevent decompression bomb attacks, normalizes aspect ratios, and strips EXIF/GPS metadata before saving to disk.
- **Idempotent Stripe Webhooks** — Refactored payment reconciliation to rely on Stripe's absolute `period_end` timestamps and `stripe_subscription_id` mappings, making the billing engine immune to network retries, duplicate deliveries, and race conditions.
- **Database Query Optimization** — Resolved cascading ORM N+1 query bottlenecks via aggressive `selectinload` implementations and added targeted B-Tree indexes on highly queried associative tables (`user_subscriptions`).
- **Strict Turnstile Logic** — `EntryLog` now natively enforces intent (`ENTRY` vs `EXIT`) synchronized with Redis, preventing users from generating conflicting QR codes or bypassing physical access control restrictions.
---
 
## Technical Stack
 
| Layer | Technology |
|---|---|
| **Framework** | FastAPI, Python 3.10+ |
| **Database** | PostgreSQL 15, SQLAlchemy 2.0 (AsyncIO engine) |
| **Migrations** | Alembic |
| **Caching & Rate Limiting** | Redis, SlowAPI |
| **Authentication** | JWT via HTTP-Only Cookies, Passlib (Bcrypt) |
| **Media Processing** | Pillow (PIL) |
| **Payments** | Stripe API integration |
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
 
# Optional Security Features
FEATURE_RECAPTCHA=true
RECAPTCHA_SECRET=your_google_recaptcha_secret
```
 
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
 
## Test Suite
 
The repository includes a comprehensive suite of asynchronous integration tests. It uses dependency overrides and an isolated SQLite in-memory database to validate end-to-end business logic without mutating production data.
 
Execute the test suite via:
 
```bash
pytest -v
```
 
---
 
<div align="center">
Built with FastAPI, PostgreSQL, and Redis.
 
</div>