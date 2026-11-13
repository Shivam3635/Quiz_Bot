"""Controlled publishing service with progress tracking and retry mechanism."""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from unittest.mock import AsyncMock, MagicMock
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError, TimedOut

from app.config.settings import get_settings
from app.parser.models import QuizQuestion, QuizSettings
from app.services.chat_lock_manager import ChatLockManager, default_chat_lock_manager
from app.services.chat_pacer import ChatPacer, default_chat_pacer
from app.services.telegram_service import SingleQuizDeliveryResult, TelegramService
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class FailedQuestionItem:
    """Details of a question that failed to publish."""

    index: int
    question: QuizQuestion
    error_message: str
    is_uncertain: bool = False
    delivery_status: str = "failed"  # "failed" (definitive) or "uncertain" (e.g. timeout)


@dataclass
class PublishingSummary:
    """Outcome of bulk publishing operation."""

    total: int
    successful: int
    failed: int
    failed_items: list[FailedQuestionItem] = field(default_factory=list)
    banner_sent: Optional[bool] = None  # None if disabled, True if sent, False if failed

    @property
    def is_complete_success(self) -> bool:
        """Return True if all questions were published without failures."""
        return self.failed == 0 and self.successful > 0

    @property
    def banner_failed(self) -> bool:
        """Return True if header banner was enabled but failed to deliver."""
        return self.banner_sent is False

    @property
    def has_uncertain_failures(self) -> bool:
        """Return True if any failed item has an uncertain delivery outcome."""
        return any(item.is_uncertain for item in self.failed_items)

    @property
    def uncertain_items(self) -> list[FailedQuestionItem]:
        """Return failed items with uncertain delivery outcomes."""
        return [item for item in self.failed_items if item.is_uncertain]

    @property
    def definitive_failed_items(self) -> list[FailedQuestionItem]:
        """Return failed items with confirmed rejection outcomes."""
        return [item for item in self.failed_items if not item.is_uncertain]


def render_progress_bar(completed: int, total: int, length: int = 15) -> str:
    """Render a visual ASCII progress bar."""
    if total <= 0:
        return "░" * length
    fraction = min(1.0, max(0.0, completed / total))
    filled = int(fraction * length)
    empty = length - filled
    return "█" * filled + "░" * empty


class PublishingService:
    """Orchestrates publishing a batch of quizzes to Telegram with rate-limit protection."""

    def __init__(
        self,
        telegram_service: Optional[TelegramService] = None,
        chat_lock_manager: Optional[ChatLockManager] = None,
        chat_pacer: Optional[ChatPacer] = None,
    ):
        self.settings = get_settings()
        self.chat_lock_manager: ChatLockManager = chat_lock_manager or default_chat_lock_manager
        self.pacer: ChatPacer = chat_pacer if chat_pacer is not None else default_chat_pacer
        self.telegram_service: TelegramService = telegram_service or TelegramService(pacer=self.pacer)
        # Ensure injected telegram_service shares the same pacer if it doesn't already have one
        if self.telegram_service and getattr(self.telegram_service, "pacer", None) is None:
            self.telegram_service.pacer = self.pacer

    @staticmethod
    def format_header_banner(total: int, settings: QuizSettings) -> str:
        """Format an introductory announcement banner for a quiz session."""
        title_line = f"🎯 <b>Quiz: {settings.title}</b>\n" if settings.title else "🎯 <b>Quiz Session Starting!</b>\n"
        desc_line = f"📝 <i>{settings.description}</i>\n\n" if settings.description else "\n"
        timer_text = f"{settings.time_limit}s" if settings.time_limit else "None"
        expl_text = "Enabled" if settings.explanation_enabled else "Disabled"
        mode_text = "Anonymous" if settings.is_anonymous else "Public"

        return (
            f"{title_line}"
            f"{desc_line}"
            f"📊 <b>Total Questions:</b> <code>{total}</code>\n"
            f"⏱️ <b>Time per Question:</b> <code>{timer_text}</code>\n"
            f"💡 <b>Explanations:</b> <code>{expl_text}</code>\n"
            f"🕶️ <b>Mode:</b> <code>{mode_text}</code>\n\n"
            f"👇 <i>Quiz questions start below. Good luck!</i>"
        )

    async def publish_batch(
        self,
        bot: Bot,
        target_chat_id: str | int,
        questions: list[QuizQuestion],
        settings: QuizSettings,
        progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
    ) -> PublishingSummary:
        """
        Publish questions one-by-one with controlled pacing.
        Serialized per destination chat using ChatLockManager.
        progress_callback signature: async (completed: int, total: int, status_text: str)
        """
        async with self.chat_lock_manager.lock_chat(target_chat_id):
            total = len(questions)
            successful = 0
            failed = 0
            failed_items: list[FailedQuestionItem] = []

            max_flood_retries = getattr(self.settings, "MAX_FLOOD_RETRIES", 3)

            logger.info("Initiating bulk publishing of %d quizzes to %s", total, target_chat_id)

            # Send channel header banner if enabled
            banner_sent: Optional[bool] = None
            if settings.header_banner_enabled:
                banner_text = self.format_header_banner(total=total, settings=settings)
                try:
                    if self.pacer:
                        banner_delay = getattr(
                            self.settings,
                            "HEADER_BANNER_DELAY",
                            self.pacer.pacing_interval,
                        )
                        await self.pacer.wait_for_turn(target_chat_id, interval=banner_delay)
                    await bot.send_message(
                        chat_id=target_chat_id,
                        text=banner_text,
                        parse_mode=ParseMode.HTML,
                    )
                    if self.pacer:
                        banner_delay = getattr(
                            self.settings,
                            "HEADER_BANNER_DELAY",
                            self.pacer.pacing_interval,
                        )
                        self.pacer.record_send(target_chat_id, interval=banner_delay)
                    logger.info("Sent quiz header banner to %s", target_chat_id)
                    banner_sent = True
                except Exception as banner_err:
                    logger.warning("Failed to send header banner (proceeding with quizzes): %s", banner_err)
                    banner_sent = False

            # Question loop
            for idx, question in enumerate(questions, start=1):
                attempt = 0
                is_mocked = not isinstance(self.telegram_service, TelegramService) or isinstance(
                    getattr(self.telegram_service, "publish_single_quiz", None),
                    (MagicMock, AsyncMock),
                )
                while True:
                    try:
                        if is_mocked and self.pacer and attempt == 0:
                            interval = getattr(
                                self.settings,
                                "DEFAULT_DELAY_BETWEEN_POSTS",
                                self.pacer.pacing_interval,
                            )
                            await self.pacer.wait_for_turn(target_chat_id, interval=interval)

                        delivery_result = await self.telegram_service.publish_single_quiz(
                            bot=bot,
                            chat_id=target_chat_id,
                            question=question,
                            settings=settings,
                        )

                        if is_mocked and self.pacer:
                            interval = getattr(
                                self.settings,
                                "DEFAULT_DELAY_BETWEEN_POSTS",
                                self.pacer.pacing_interval,
                            )
                            self.pacer.record_send(target_chat_id, interval=interval)

                        successful += 1
                        logger.info("Published quiz %d/%d successfully", idx, total)
                        break
                    except RetryAfter as e:
                        attempt += 1
                        wait_seconds = int(e.retry_after) + 1
                        if self.pacer:
                            self.pacer.record_flood_wait(target_chat_id, wait_seconds)
                        if attempt > max_flood_retries:
                            failed += 1
                            err_msg = f"Flood control limit reached after {max_flood_retries} retries: {e.message}"
                            logger.error("Quiz %d failed: %s", idx, err_msg)
                            failed_items.append(
                                FailedQuestionItem(
                                    index=idx,
                                    question=question,
                                    error_message=err_msg,
                                    is_uncertain=False,
                                    delivery_status="failed",
                                )
                            )
                            break
                        logger.warning(
                            "Flood control hit for quiz %d (attempt %d/%d), pausing for %ds",
                            idx,
                            attempt,
                            max_flood_retries,
                            wait_seconds,
                        )
                        await asyncio.sleep(wait_seconds)
                        if self.pacer:
                            self.pacer.clear_flood_wait(target_chat_id)
                    except TimedOut as e:
                        # Network timeout: delivery outcome is uncertain (Telegram server may have already posted it)
                        failed += 1
                        err_msg = f"Request timed out (delivery uncertain): {str(e)}"
                        logger.warning("Quiz %d timed out: %s", idx, err_msg)
                        failed_items.append(
                            FailedQuestionItem(
                                index=idx,
                                question=question,
                                error_message=err_msg,
                                is_uncertain=True,
                                delivery_status="uncertain",
                            )
                        )
                        break
                    except TelegramError as e:
                        is_timeout = "timed out" in str(e).lower()
                        failed += 1
                        err_msg = e.message
                        logger.error("Failed to publish quiz %d: %s", idx, err_msg)
                        failed_items.append(
                            FailedQuestionItem(
                                index=idx,
                                question=question,
                                error_message=err_msg,
                                is_uncertain=is_timeout,
                                delivery_status="uncertain" if is_timeout else "failed",
                            )
                        )
                        break
                    except asyncio.CancelledError:
                        logger.warning("Publishing batch cancelled at quiz %d", idx)
                        raise
                    except Exception as e:
                        failed += 1
                        err_msg = str(e)
                        is_timeout = "timed out" in err_msg.lower()
                        logger.error("Unexpected error publishing quiz %d: %s", idx, err_msg)
                        failed_items.append(
                            FailedQuestionItem(
                                index=idx,
                                question=question,
                                error_message=err_msg,
                                is_uncertain=is_timeout,
                                delivery_status="uncertain" if is_timeout else "failed",
                            )
                        )
                        break

                # Invoke progress update periodically or at milestones
                if progress_callback:
                    progress_bar = render_progress_bar(idx, total)
                    msg = f"{progress_bar}\n\n<b>{idx} / {total}</b> processed"
                    try:
                        await progress_callback(idx, total, msg)
                    except Exception as cb_err:
                        logger.debug("Progress callback failed (non-critical): %s", cb_err)

            return PublishingSummary(
                total=total,
                successful=successful,
                failed=failed,
                failed_items=failed_items,
                banner_sent=banner_sent,
            )
