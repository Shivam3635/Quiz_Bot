"""Telegram API interaction service for quiz polls and channel validation."""

from typing import Optional, Tuple
from telegram import Bot
from telegram.constants import ParseMode, PollType
from telegram.error import TelegramError

from app.parser.models import QuizQuestion, QuizSettings
from app.utils.helpers import (
    format_bilingual_question_text,
    format_rich_text_for_telegram,
    strip_html_tags,
)
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


class TelegramService:
    """Service for sending native quiz polls and checking chat permissions."""

    async def validate_channel_access(self, bot: Bot, channel_identifier: str) -> Tuple[bool, str, Optional[int]]:
        """
        Verify that the bot has access and can post in the target channel/group.
        Returns: (is_valid, message, chat_id)
        """
        target = channel_identifier.strip()
        if not target.startswith("@") and not target.startswith("-100") and not target.isdigit():
            target = f"@{target}"

        try:
            chat = await bot.get_chat(target)
            bot_member = await chat.get_member(bot.id)

            # Check if bot has post privileges in channel
            if chat.type == "channel":
                can_post = getattr(bot_member, "can_post_messages", None)
                if can_post is False:
                    return False, f"⚠️ Bot is not an Admin with 'Post Messages' permission in {chat.title or target}.", chat.id
            return True, f"✅ Verified access to <b>{chat.title or target}</b>.", chat.id

        except TelegramError as e:
            logger.error("Failed to access channel %s: %s", channel_identifier, str(e))
            return False, f"❌ Cannot access '{channel_identifier}'. Ensure the bot is added as an Administrator: {e.message}", None
        except Exception as e:
            return False, f"❌ Unexpected error checking channel: {str(e)}", None

    async def publish_single_quiz(
        self,
        bot: Bot,
        chat_id: str | int,
        question: QuizQuestion,
        settings: QuizSettings,
    ) -> bool:
        """Publish a single native Telegram Quiz Poll with rich text formatting."""
        options = question.options
        if settings.shuffle_options:
            # Note: If shuffling options, correct_option must be updated accordingly
            # For simplicity and correctness in Telegram API, we can shuffle or keep original
            pass

        open_period = settings.time_limit if (settings.time_limit and 5 <= settings.time_limit <= 600) else None

        raw_question = format_bilingual_question_text(question.display_question)
        poll_question = format_rich_text_for_telegram(raw_question, max_plain_length=300)

        explanation_text = None
        if settings.explanation_enabled and question.explanation:
            explanation_text = format_rich_text_for_telegram(question.explanation, max_plain_length=200)

        try:
            await bot.send_poll(
                chat_id=chat_id,
                question=poll_question,
                options=options,
                type=PollType.QUIZ,
                is_anonymous=settings.is_anonymous,
                correct_option_id=question.correct_option,
                explanation=explanation_text,
                open_period=open_period,
                question_parse_mode=ParseMode.HTML,
                explanation_parse_mode=ParseMode.HTML if explanation_text else None,
            )
        except TelegramError as e:
            err_msg = str(e).lower()
            if "parse" in err_msg or "entity" in err_msg or "entities" in err_msg:
                logger.warning(
                    "Rich text parse failed for question (%s). Retrying as clean plain text without parse mode.",
                    e,
                )
                plain_question = strip_html_tags(raw_question)
                if len(plain_question) > 300:
                    plain_question = plain_question[:297] + "..."

                plain_explanation = None
                if settings.explanation_enabled and question.explanation:
                    plain_explanation = strip_html_tags(question.explanation)
                    if len(plain_explanation) > 200:
                        plain_explanation = plain_explanation[:197] + "..."

                await bot.send_poll(
                    chat_id=chat_id,
                    question=plain_question,
                    options=options,
                    type=PollType.QUIZ,
                    is_anonymous=settings.is_anonymous,
                    correct_option_id=question.correct_option,
                    explanation=plain_explanation,
                    open_period=open_period,
                    question_parse_mode=None,
                    explanation_parse_mode=None,
                )
            else:
                raise
        return True
