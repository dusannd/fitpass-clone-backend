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
    """
    Yields a session to the route and cleans up after it, whatever happens.

    The rollback is explicit rather than left to the context manager. Closing a
    session does discard an open transaction on its own, but only once the
    exception has already travelled past every other dependency's teardown - and
    anything that touched the same session in between would hit a
    PendingRollbackError instead of the real error.

    The exception is re-raised untouched so the global handler in main.py still
    logs it and answers with a generic 500. HTTPException is caught here too, and
    that is intended: a route that raises a 404 after a couple of db.add() calls
    should not leave them pending.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()