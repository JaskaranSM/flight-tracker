import asyncio
import logging
import sys

from config import BOT_TOKEN, TRACKED_FLIGHTS, DEFAULT_FLIGHTS, LOG_DIR
from database import Database
from analytics import Analytics
from bot import FlightBot
from scheduler import Scheduler
from scraper import scrape_air_india


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "flight_tracker.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


async def async_main():
    setup_logging()
    logger = logging.getLogger(__name__)

    if not BOT_TOKEN:
        logger.error("FLIGHT_TRACKER_BOT_TOKEN not set")
        sys.exit(1)

    db = Database()
    analytics = Analytics(db)
    bot = FlightBot(db, analytics)
    scheduler = Scheduler(db, analytics, scrape_air_india, bot.send_message)
    bot.scheduler = scheduler

    for flight in set(TRACKED_FLIGHTS) | set(DEFAULT_FLIGHTS):
        flight = flight.strip()
        if flight:
            db.add_tracked_flight(flight)

    scheduler.start()
    logger.info("Scheduler started")

    try:
        await bot.run()
    except Exception as e:
        logger.error(f"Bot failed: {e}")
    finally:
        await scheduler.stop()


if __name__ == "__main__":
    asyncio.run(async_main())
