import pytest
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.main import app
from app.core.config import settings
from app.core.rate_limit import limiter
from app.models.coaching import Appointment
from app.models.access import EntryLog
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.user import User, Role

limiter.enabled = False
settings.TESTING = True
settings.FEATURE_RECAPTCHA = False

SQLALCHEMY_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine_test = create_async_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=engine_test, class_=AsyncSession, expire_on_commit=False
)

# This overrides the get_db dependency in FastAPI so all requests go to the test DB
async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


# This setup function automatically builds the tables before tests, and destroys them after
@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield  # Tests run here

    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def backdate_appointment():
    """
    Moves an existing appointment into the past, straight through the database.

    POST /coaching/appointments refuses past timestamps on purpose, so this is the
    only way for a test to reach the "session is over, close it out" state. Lives
    here rather than in a test module because that is where the test sessionmaker
    is - test/ has no __init__.py, so it is not importable as a package.
    """
    async def _backdate(appointment_id: int, hours_ago: int = 2):
        async with TestingSessionLocal() as session:
            appointment = (await session.execute(
                select(Appointment).where(Appointment.id == appointment_id)
            )).scalars().first()

            appointment.start_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
            appointment.end_time = appointment.start_time + timedelta(hours=1)

            session.add(appointment)
            await session.commit()

    return _backdate


@pytest.fixture
def seed_entry_logs():
    """
    Writes turnstile logs straight into the database.

    There is no API that lets a test create an arbitrary log - a real one only
    appears when somebody scans a QR code - so the worker panel endpoints have to
    be given their data directly.

    Every row gets an EXPLICIT timestamp. The column's server_default is the
    database clock, and SQLite's CURRENT_TIMESTAMP only has second resolution, so
    a loop of inserts would produce identical timestamps and make every ordering
    assertion in the worker tests a coin flip.

    Lives here rather than in a test module for the same reason as the fixture
    above: test/ has no __init__.py, so it is not importable as a package.
    """
    async def _seed(entries: list[dict]):
        created_ids = []
        async with TestingSessionLocal() as session:
            for offset, entry in enumerate(entries):
                log = EntryLog(
                    user_id=entry["user_id"],
                    worker_id=entry.get("worker_id"),
                    access_granted=entry.get("access_granted", True),
                    action_type=entry.get("action_type", "ENTRY"),
                    reason=entry.get("reason"),
                    # Caller order is oldest -> newest unless it says otherwise
                    timestamp=entry.get(
                        "timestamp",
                        datetime.now(timezone.utc) - timedelta(minutes=len(entries) - offset),
                    ),
                )
                session.add(log)
                await session.flush()
                created_ids.append(log.id)

            await session.commit()
        return created_ids

    return _seed


@pytest.fixture
def seed_subscription():
    """
    Writes a UserSubscription row straight into the database.

    The only path that creates one in production is a Stripe webhook carrying a
    signed payload, so a test cannot reach this state over HTTP at all.

    stripe_subscription_id is explicit because None is a state worth testing: rows
    that predate the Stripe integration, and passes the desk activates by hand,
    both have no Stripe subscription behind them and therefore no billing portal.

    A plan is created on demand when none is passed - the FK needs a real one, and
    the in-memory database is shared across the whole session, so the name gets a
    uuid suffix rather than colliding with test_plans.py.

    Lives here rather than in a test module for the same reason as the fixtures
    above: test/ has no __init__.py, so it is not importable as a package.
    """
    async def _seed(
        user_id: int,
        plan_id: int | None = None,
        stripe_subscription_id: str | None = "sub_test_123",
        days_left: int = 30,
        is_active: int = 1,
        includes_trainer: bool = False,
    ):
        async with TestingSessionLocal() as session:
            if plan_id is None:
                plan = SubscriptionPlan(
                    name=f"Seeded Plan {uuid.uuid4().hex[:6]}",
                    description="Created by seed_subscription",
                    price=3000,
                    duration_days=30,
                    # The one perk the backend enforces. Defaults to False so every
                    # existing caller keeps the plan it always got; coaching tests
                    # ask for True explicitly.
                    includes_trainer=includes_trainer,
                )
                session.add(plan)
                await session.flush()
                plan_id = plan.id

            subscription = UserSubscription(
                user_id=user_id,
                plan_id=plan_id,
                start_date=datetime.now(timezone.utc) - timedelta(days=1),
                end_date=datetime.now(timezone.utc) + timedelta(days=days_left),
                is_active=is_active,
                stripe_subscription_id=stripe_subscription_id,
            )
            session.add(subscription)
            await session.commit()
            return subscription.id

    return _seed


@pytest.fixture
def expire_subscription():
    """
    Deactivates an existing subscription, the way losing a pass really happens.

    In production this is done by Stripe's customer.subscription.deleted webhook
    (app/api/payments.py) or by the hourly sweep in app/services/scheduler.py -
    neither of which a test can reach over HTTP without a signed Stripe payload.

    Takes the id returned by seed_subscription, so a test can set a member up with
    a perk, use it, and then take it away without touching the plan itself.
    """
    async def _expire(subscription_id: int):
        async with TestingSessionLocal() as session:
            subscription = await session.get(UserSubscription, subscription_id)
            subscription.is_active = 0
            await session.commit()

    return _expire


@pytest.fixture
def set_user_roles():
    """
    REPLACES a user's roles with the ones named.

    POST /users/ always hands out "member", and promoting someone through the
    admin API would mean building an admin account and a JWT first. Replacing
    rather than appending is the point: a test for "only members show up" needs a
    user who is NOT a member.
    """
    async def _set(user_id: int, role_names: list[str]):
        async with TestingSessionLocal() as session:
            user = (await session.execute(
                select(User).where(User.id == user_id)
            )).scalars().first()

            roles = []
            for name in role_names:
                role = (await session.execute(
                    select(Role).where(Role.name == name)
                )).scalars().first()

                # The roles table is seeded lazily by whoever needs a role first,
                # so a test may well be the first thing to ask for "trainer".
                if not role:
                    role = Role(name=name, description=f"{name} (created by a test)")
                    session.add(role)
                    await session.flush()

                roles.append(role)

            user.roles = roles
            session.add(user)
            await session.commit()

    return _set


@pytest.fixture
def clear_user_name():
    """
    Nulls out a user's first and last name.

    UserCreate requires both, so the API cannot produce this state - but the
    columns are nullable, and rows like this exist in older databases. It is the
    only way to test that the panel renders a fallback instead of "None None".
    """
    async def _clear(user_id: int):
        async with TestingSessionLocal() as session:
            user = (await session.execute(
                select(User).where(User.id == user_id)
            )).scalars().first()

            user.first_name = None
            user.last_name = None

            session.add(user)
            await session.commit()

    return _clear
