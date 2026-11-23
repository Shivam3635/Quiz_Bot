"""Background scheduler service and worker for Phase 1: Single Quiz Scheduling."""

import asyncio
import html
import uuid
from datetime import UTC, datetime

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.database.database import SessionLocal
from app.database.repositories import (
    claim_due_schedules,
    complete_publish_job,
    complete_quiz_schedule,
    create_publish_job,
    fail_quiz_schedule,
    get_quiz_schedule_by_id,
    get_quiz_set_by_id,
    reconstruct_quiz_data,
    recover_stale_leases,
    update_schedule_heartbeat,
)
from app.services.publishing_service import PublishingService
from app.services.telegram_service import TelegramService
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

# Default misfire tolerance: 6 hours. Schedules older than this are considered severely outdated.
DEFAULT_MISFIRE_GRACE_SECONDS = 6 * 3600
# Lease timeout: if a worker does not heartbeat for 5 minutes, consider lease stale.
DEFAULT_LEASE_TIMEOUT_SECONDS = 300


class QuizSchedulerService:
    """
    Manages persistent scheduling and background execution of automated quiz conductions.
    Uses PostgreSQL row-locking and lease recovery for multi-worker safety.
    """

    def __init__(
        self,
        bot: Bot | None = None,
        publishing_service: PublishingService | None = None,
        telegram_service: TelegramService | None = None,
        poll_interval: float = 5.0,
        misfire_grace_seconds: int = DEFAULT_MISFIRE_GRACE_SECONDS,
        lease_timeout_seconds: int = DEFAULT_LEASE_TIMEOUT_SECONDS,
    ):
        self.bot = bot
        self.publishing_service = publishing_service or PublishingService()
        self.telegram_service = telegram_service or TelegramService()
        self.poll_interval = poll_interval
        self.misfire_grace_seconds = misfire_grace_seconds
        self.lease_timeout_seconds = lease_timeout_seconds
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"

        self._running = False
        self._task: asyncio.Task | None = None

    async def _notify_user(self, telegram_id: int, text: str) -> None:
        """Send notification to the scheduling user's private Telegram chat."""
        if not self.bot:
            logger.warning("Cannot send notification: Bot instance is not configured.")
            return
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            logger.warning("Failed to send execution notification to user %s: %s", telegram_id, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("Unexpected error notifying user %s: %s", telegram_id, e)

    async def execute_schedule(self, schedule_id: int, lease_token: str) -> bool:
        """Execute a claimed quiz schedule using the existing publishing engine."""
        now_utc = datetime.now(UTC).replace(tzinfo=None)

        with SessionLocal() as db:
            schedule = get_quiz_schedule_by_id(db, schedule_id)
            if not schedule:
                logger.error("Schedule %s not found for execution", schedule_id)
                return False

            user_telegram_id = schedule.user.telegram_id if schedule.user else None
            quiz_set = get_quiz_set_by_id(db, schedule.quiz_set_id)
            if not quiz_set or quiz_set.user_id != schedule.user_id:
                err_msg = f"Quiz set {schedule.quiz_set_id} was deleted or is no longer accessible."
                logger.error("Schedule %s failed: %s", schedule_id, err_msg)
                fail_quiz_schedule(db, schedule_id, err_msg)
                if user_telegram_id:
                    await self._notify_user(
                        user_telegram_id,
                        f"❌ <b>Scheduled Quiz Failed!</b>\n\n"
                        f"Your scheduled quiz (ID: <code>{schedule.id}</code>) could not run.\n"
                        f"Reason: <i>{html.escape(err_msg)}</i>",
                    )
                return False

            # Check for excessive misfire delay
            scheduled_at_utc = schedule.scheduled_at
            delay_seconds = (now_utc - scheduled_at_utc).total_seconds()
            if delay_seconds > self.misfire_grace_seconds:
                err_msg = (
                    f"Schedule missed: delayed by {int(delay_seconds)}s "
                    f"(exceeds grace limit of {self.misfire_grace_seconds}s)."
                )
                logger.error("Schedule %s missed: %s", schedule_id, err_msg)
                fail_quiz_schedule(db, schedule_id, err_msg)
                if user_telegram_id:
                    await self._notify_user(
                        user_telegram_id,
                        f"⚠️ <b>Scheduled Quiz Missed!</b>\n\n"
                        f"• <b>Quiz:</b> {html.escape(quiz_set.title)}\n"
                        f"• <b>Scheduled For:</b> <code>{schedule.scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
                        f"• <b>Reason:</b> <i>The scheduled execution time expired during downtime.</i>",
                    )
                return False

            quiz_title = quiz_set.title
            target_chat = schedule.chat_id
            target_title = schedule.chat_title or target_chat

        # Validate destination group access & bot permissions
        if self.bot:
            valid, msg, resolved_chat_id = await self.telegram_service.validate_channel_access(
                self.bot, target_chat
            )
            if not valid:
                err_msg = f"Target chat verification failed: {msg}"
                logger.error("Schedule %s permission check failed: %s", schedule_id, err_msg)
                with SessionLocal() as db:
                    fail_quiz_schedule(db, schedule_id, err_msg)
                if user_telegram_id:
                    await self._notify_user(
                        user_telegram_id,
                        f"❌ <b>Scheduled Quiz Failed!</b>\n\n"
                        f"• <b>Quiz:</b> {html.escape(quiz_title)}\n"
                        f"• <b>Target:</b> {html.escape(target_title)}\n"
                        f"• <b>Reason:</b> <i>{html.escape(msg)}</i>\n\n"
                        "Please verify that the bot is an Administrator with 'Post Messages' rights.",
                    )
                return False
            if resolved_chat_id:
                target_chat = str(resolved_chat_id)

        # Reconstruct quiz data and settings
        questions, settings = reconstruct_quiz_data(quiz_set)
        settings.channel_id = target_chat

        if not questions:
            err_msg = "Quiz set has no questions to publish."
            with SessionLocal() as db:
                fail_quiz_schedule(db, schedule_id, err_msg)
            if user_telegram_id:
                await self._notify_user(
                    user_telegram_id,
                    f"❌ <b>Scheduled Quiz Failed!</b>\n\n"
                    f"Quiz <b>{html.escape(quiz_title)}</b> has no questions.",
                )
            return False

        # Create PublishJob for tracking
        with SessionLocal() as db:
            job = create_publish_job(db, quiz_set.id, len(questions))
            job_id = job.id

        # Heartbeat loop during publication
        heartbeat_active = True

        async def heartbeat_loop():
            while heartbeat_active:
                await asyncio.sleep(15)
                try:
                    with SessionLocal() as db:
                        update_schedule_heartbeat(db, schedule_id, lease_token)
                except Exception as hb_err:  # noqa: BLE001
                    logger.debug("Heartbeat update failed for schedule %s: %s", schedule_id, hb_err)

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        try:
            logger.info(
                "Worker %s executing schedule %s (Quiz: '%s', Target: %s)",
                self.worker_id,
                schedule_id,
                quiz_title,
                target_chat,
            )

            if not self.bot:
                raise RuntimeError("Bot instance is required to execute quiz publishing.")

            summary = await self.publishing_service.publish_batch(
                bot=self.bot,
                target_chat_id=target_chat,
                questions=questions,
                settings=settings,
            )

            heartbeat_active = False
            heartbeat_task.cancel()

            with SessionLocal() as db:
                if summary.is_complete_success:
                    complete_publish_job(
                        db, job_id, summary.successful, summary.failed, status="completed"
                    )
                    complete_quiz_schedule(db, schedule_id)
                    logger.info(
                        "Schedule %s completed successfully (%d questions)",
                        schedule_id,
                        summary.successful,
                    )
                    if user_telegram_id:
                        await self._notify_user(
                            user_telegram_id,
                            f"🎉 <b>Scheduled Quiz Conducted!</b>\n\n"
                            f"• <b>Quiz:</b> <b>{html.escape(quiz_title)}</b>\n"
                            f"• <b>Target:</b> <b>{html.escape(target_title)}</b>\n"
                            f"• <b>Delivered:</b> <code>{summary.successful}/{len(questions)}</code> questions\n"
                            f"• <b>Time:</b> <code>{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}</code>",
                        )
                    return True
                else:
                    err_msg = f"Published {summary.successful}/{len(questions)} questions; {summary.failed} failed."
                    complete_publish_job(
                        db, job_id, summary.successful, summary.failed, status="failed"
                    )
                    fail_quiz_schedule(db, schedule_id, err_msg)
                    logger.warning("Schedule %s completed with failures: %s", schedule_id, err_msg)
                    if user_telegram_id:
                        await self._notify_user(
                            user_telegram_id,
                            f"⚠️ <b>Scheduled Quiz Completed with Errors</b>\n\n"
                            f"• <b>Quiz:</b> <b>{html.escape(quiz_title)}</b>\n"
                            f"• <b>Target:</b> <b>{html.escape(target_title)}</b>\n"
                            f"• <b>Delivered:</b> <code>{summary.successful}/{len(questions)}</code> questions\n"
                            f"• <b>Failed:</b> <code>{summary.failed}</code> questions",
                        )
                    return False

        except Exception as exec_err:
            heartbeat_active = False
            heartbeat_task.cancel()
            logger.exception("Unexpected error executing schedule %s", schedule_id)
            with SessionLocal() as db:
                complete_publish_job(db, job_id, 0, len(questions), status="failed")
                fail_quiz_schedule(db, schedule_id, str(exec_err))
            if user_telegram_id:
                await self._notify_user(
                    user_telegram_id,
                    f"❌ <b>Scheduled Quiz Execution Error</b>\n\n"
                    f"• <b>Quiz:</b> {html.escape(quiz_title)}\n"
                    f"• <b>Error:</b> <i>{html.escape(str(exec_err))}</i>",
                )
            return False

    async def process_due_schedules(self) -> int:
        """Poll and execute all currently due schedules."""
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        lease_token = f"{self.worker_id}-{uuid.uuid4().hex[:6]}"

        with SessionLocal() as db:
            due_items = claim_due_schedules(
                db=db,
                now_utc=now_utc,
                batch_size=5,
                lease_token=lease_token,
            )

        if not due_items:
            return 0

        logger.info(
            "Worker %s claimed %d due schedule(s) to process", self.worker_id, len(due_items)
        )
        processed = 0
        for item in due_items:
            try:
                await self.execute_schedule(item.id, lease_token)
                processed += 1
            except Exception:
                logger.exception("Failed processing schedule %s", item.id)
        return processed

    async def recover_stale_tasks(self) -> int:
        """Scan and recover stale leases left by crashed workers."""
        from datetime import timedelta

        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            seconds=self.lease_timeout_seconds
        )
        with SessionLocal() as db:
            recovered = recover_stale_leases(db, cutoff)
            if recovered:
                logger.warning(
                    "Worker %s recovered %d stale/crashed schedule lease(s)",
                    self.worker_id,
                    len(recovered),
                )
                for rec in recovered:
                    if rec.user and rec.user.telegram_id:
                        await self._notify_user(
                            rec.user.telegram_id,
                            f"⚠️ <b>Schedule Crash Recovery Notice</b>\n\n"
                            f"Your schedule (ID: <code>{rec.id}</code>) was interrupted by a system restart. "
                            f"To prevent duplicate publishing, it has been marked failed.",
                        )
            return len(recovered)

    async def _worker_loop(self) -> None:
        """Continuous polling loop for scheduled tasks."""
        logger.info("Quiz Scheduler Worker started (%s)", self.worker_id)
        # Run lease recovery on startup
        try:
            await self.recover_stale_tasks()
        except Exception as rec_err:  # noqa: BLE001
            logger.warning("Startup stale lease recovery error: %s", rec_err)

        while self._running:
            try:
                await self.process_due_schedules()
            except Exception as poll_err:  # noqa: BLE001
                logger.error("Error in scheduler polling loop: %s", poll_err)

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

        logger.info("Quiz Scheduler Worker stopped (%s)", self.worker_id)

    def start(self) -> asyncio.Task:
        """Start the background scheduler worker task."""
        if self._running:
            return self._task  # type: ignore
        self._running = True
        self._task = asyncio.create_task(self._worker_loop())
        return self._task

    async def stop(self) -> None:
        """Gracefully stop the background scheduler worker."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
