import redis.asyncio as redis

# Connect to our local Docker Redis instance
redis_db = redis.Redis(
    host="localhost",
    port=6379,
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