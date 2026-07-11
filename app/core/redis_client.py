import redis.asyncio as redis
from app.core.config import settings

# Connect securely using environment variables
redis_db = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=0,
    decode_responses=True
)

async def check_redis_connection():
    """
    Pings Redis on server startup to ensure the connection is alive.
    Throws an exception if Redis is unreachable.
    """
    try:
        await redis_db.ping()
        print("Connected to Redis successfully!")
    except Exception as e:
        print(f"Failed to connect to Redis: {e}")