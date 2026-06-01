from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from datetime import datetime, timezone
import logging

from app.core.database import AsyncSessionLocal
from app.models.subscription import UserSubscription

# Setup logger to monitor background tasks in the terminal
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def deactivate_expired_subscriptions():
    """
    Background task that finds all active subscriptions where the end_date
    has passed, and sets their is_active status to 0 (inactive).
    """
    logger.info("Running background job: Checking for expired subscriptions...")

    # Since this is a background task running outside a FastAPI request,
    # we cannot use Depends(get_db). We must manually open a database session.
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)

        # SQL UPDATE command:
        # UPDATE user_subscriptions SET is_active = 0 WHERE end_date < now AND is_active = 1
        stmt = (
            update(UserSubscription)
            .where(UserSubscription.end_date < now)
            .where(UserSubscription.is_active == 1)
            .values(is_active=0)
        )

        result = await db.execute(stmt)
        await db.commit()

        # result.rowcount returns the number of rows affected by the UPDATE statement
        logger.info(f"Background job finished. Deactivated {result.rowcount} expired subscriptions.")


# Create an instance of the Scheduler
scheduler = AsyncIOScheduler()


def start_scheduler():
    """
    Configures and starts the background scheduler.
    """
    # In production you might want to run this once a day at midnight:
    # scheduler.add_job(deactivate_expired_subscriptions, "cron", hour=0, minute=0)

    # For testing purposes, we run it every hour
    scheduler.add_job(deactivate_expired_subscriptions, "interval", hours=1)
    scheduler.start()
    logger.info("Scheduler started successfully.")