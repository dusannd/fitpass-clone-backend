import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.database import Base, get_db
from app.main import app
from app.core.config import settings
from app.core.rate_limit import limiter
limiter.enabled = False


# 1. MAGIC FLAG: Tell the application we are currently running tests!
settings.TESTING = True


# Create a separate database JUST for testing (SQLite in-memory is the fastest)
# It creates a fresh empty database every time you run pytest
SQLALCHEMY_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine_test = create_async_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
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
