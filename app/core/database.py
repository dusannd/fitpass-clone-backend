from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# We will use a local PostgreSQL database
# Format: postgresql+asyncpg://user:password@localhost:5432/database_name
# Note: We will move this to a .env file later for security
SQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://postgres:admin@localhost:5433/fitpass_db"

# Create async engine
engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True)

# Create session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Base class for all database models
Base = declarative_base()

# Dependency for FastAPI to get DB session
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()