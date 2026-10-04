"""Controlled publishing service with progress tracking and retry mechanism."""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from app.config.settings import get_settings
from app.parser.models import QuizQuestion, QuizSettings
from app.services.telegram_service import TelegramService
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class FailedQuestionItem:
    """Details of a question that failed to publish."""

    index: int
    question: QuizQuestion
    error_message: str


@dataclass
class PublishingSummary:
    """Outcome of bulk publishing operation."""

    total: int
    successful: int
    failed: int
    failed_items: list[FailedQuestionItem] = field(default_factory=list)

    @property
    def is_complete_success(self) -> bool:
        """Return True if all questions were published without failures."""
        return self.failed == 0 and self.successful > 0


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

    def __init__(self, telegram_service: Optional[TelegramService] = None):
        self.telegram_service = telegram_service or TelegramService()
        self.settings = get_settings()

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
        progress_callback signature: async (completed: int, total: int, status_text: str)
        """
        total = len(questions)
        successful = 0
        failed = 0
        failed_items: list[FailedQuestionItem] = []

        delay = self.settings.DEFAULT_DELAY_BETWEEN_POSTS

        logger.info("Initiating bulk publishing of %d quizzes to %s", total, target_chat_id)

        # Send channel header banner if enabled
        if settings.header_banner_enabled:
            banner_text = self.format_header_banner(total=total, settings=settings)
            try:
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=banner_text,
                    parse_mode=ParseMode.HTML,
                )
                logger.info("Sent quiz header banner to %s", target_chat_id)
                await asyncio.sleep(1.0)
            except Exception as banner_err:
                logger.warning("Failed to send header banner (proceeding with quizzes): %s", banner_err)

        for idx, question in enumerate(questions, start=1):
            try:
                await self.telegram_service.publish_single_quiz(
                    bot=bot,
                    chat_id=target_chat_id,
                    question=question,
                    settings=settings,
                )
                successful += 1
                logger.info("Published quiz %d/%d successfully", idx, total)
            except RetryAfter as e:
                # Handle flood wait from Telegram
                wait_seconds = int(e.retry_after) + 1
                logger.warning("Flood control hit, pausing for %ds", wait_seconds)
                await asyncio.sleep(wait_seconds)
                # Retry once
                try:
                    await self.telegram_service.publish_single_quiz(
                        bot=bot,
                        chat_id=target_chat_id,
                        question=question,
                        settings=settings,
                    )
                    successful += 1
                except Exception as retry_err:
                    failed += 1
                    failed_items.append(
                        FailedQuestionItem(index=idx, question=question, error_message=str(retry_err))
                    )
            except TelegramError as e:
                failed += 1
                err_msg = e.message
                logger.error("Failed to publish quiz %d: %s", idx, err_msg)
                failed_items.append(
                    FailedQuestionItem(index=idx, question=question, error_message=err_msg)
                )
            except Exception as e:
                failed += 1
                logger.error("Unexpected error publishing quiz %d: %s", idx, str(e))
                failed_items.append(
                    FailedQuestionItem(index=idx, question=question, error_message=str(e))
                )

            # Invoke progress update periodically or at milestones
            if progress_callback:
                progress_bar = render_progress_bar(idx, total)
                msg = f"{progress_bar}\n\n<b>{idx} / {total}</b> processed"
                try:
                    await progress_callback(idx, total, msg)
                except Exception as cb_err:
                    logger.debug("Progress callback failed (non-critical): %s", cb_err)

            # Cooperative delay between posts to prevent hitting rate limits
            if idx < total:
                await asyncio.sleep(delay)

        return PublishingSummary(
            total=total,
            successful=successful,
            failed=failed,
            failed_items=failed_items,
        )
