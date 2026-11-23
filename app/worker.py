"""Standalone background worker entrypoint for QuizBotPro Quiz Automation Engine."""

import asyncio
import signal
import sys

from telegram import Bot

from app.config.settings import get_settings
from app.database.database import init_db
from app.services.scheduler_service import QuizSchedulerService
from app.utils.logger import setup_logger

logger = setup_logger("app.worker")


async def run_worker() -> None:
    """Run the standalone Quiz Scheduler Worker."""
    settings = get_settings()
    logger.info(
        "Initializing QuizBotPro Standalone Scheduler Worker in %s mode...", settings.ENVIRONMENT
    )

    # Ensure database is verified and tables exist
    try:
        init_db()
        logger.info("Database verified and initialized successfully.")
    except Exception as e:  # noqa: BLE001
        logger.critical("Fatal: Worker database initialization failed: %s", e)
        sys.exit(1)

    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN in (
        "your_bot_token_here",
        "mock_token_for_tests",
    ):
        logger.error("CRITICAL: Valid TELEGRAM_BOT_TOKEN is required for scheduler worker.")
        sys.exit(1)

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    worker = QuizSchedulerService(bot=bot)

    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Stop signal received. Shutting down worker gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except (NotImplementedError, RuntimeError):
            # Windows signal handling fallback
            pass

    worker.start()
    logger.info("Quiz Scheduler Worker is actively polling PostgreSQL for due schedules.")

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Stopping scheduler worker...")
        await worker.stop()
        logger.info("Scheduler worker shutdown complete.")


def main() -> None:
    """CLI entrypoint for standalone worker."""
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user.")


if __name__ == "__main__":
    main()
