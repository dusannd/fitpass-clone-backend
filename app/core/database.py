from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# Fetching the database URL securely from Pydantic settings (.env)
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

# --- NEW: PRODUCTION-READY CONNECTION POOLING ---
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=False,              # Disable SQL echoing in production to prevent terminal spam
    pool_pre_ping=True,      # Tests connection validity before querying (prevents "lost connection" crashes)
    pool_size=10,            # Number of permanent connections maintained in the pool
    max_overflow=20          # Number of extra connections allowed during high traffic spikes
)

# Create session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Base class for all database models
Base = declarative_base()

# Dependency for FastAPI to get DB session securely
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()