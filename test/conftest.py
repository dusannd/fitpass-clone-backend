import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool # <--- DODAJ OVO
from app.core.database import Base, get_db
from app.main import app
from app.core.config import settings
from app.core.rate_limit import limiter
from app.models.coaching import Appointment

limiter.enabled = False
settings.TESTING = True
settings.FEATURE_RECAPTCHA = False

SQLALCHEMY_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine_test = create_async_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool, # <--- DODAJ OVO DA BAZA PREZIVI SVE REKVESTE
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
