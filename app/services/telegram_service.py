"""Telegram API interaction service for quiz polls and channel validation."""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Tuple
from telegram import Bot
from telegram.constants import ParseMode, PollType
from telegram.error import RetryAfter, TelegramError, TimedOut

from app.config.settings import get_settings
from app.parser.models import QuizQuestion, QuizSettings
from app.parser.validator import has_lone_surrogates, telegram_utf16_length
from app.utils.helpers import (
    convert_markdown_to_telegram_html,
    format_bilingual_question_text,
    format_rich_text_for_telegram,
    sanitize_telegram_html,
    strip_html_tags,
    truncate_rich_text,
)
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


def is_extended_option_question(question: QuizQuestion) -> bool:
    """Return True if any option in the question exceeds Telegram's 100 UTF-16 code units limit."""
    return any(telegram_utf16_length(opt.strip()) > 100 for opt in question.options)


def format_extended_question_message(question: QuizQuestion) -> str:
    """
    Format the complete question and full uncompressed options into a rich Telegram message.
    Used for Solution 1 when options exceed Telegram's 100-character poll button limit.
    """
    raw_question = format_bilingual_question_text(question.display_question)
    q_html = sanitize_telegram_html(convert_markdown_to_telegram_html(raw_question))

    lines = [f"❓ <b>{q_html}</b>\n"]
    for idx, opt in enumerate(question.options):
        letter = chr(ord("A") + idx)
        opt_html = sanitize_telegram_html(convert_markdown_to_telegram_html(opt.strip()))
        lines.append(f"<b>[{letter}]</b> {opt_html}")

    full_text = "\n".join(lines)
    # Ensure within Telegram's 4096 character text message limit
    if len(strip_html_tags(full_text)) > 4000:
        full_text = truncate_rich_text(full_text, 4000)
    return full_text


@dataclass(frozen=True)
class SingleQuizDeliveryResult:
    """Structured per-operation delivery outcome for a single quiz question/poll."""

    success: bool = True
    flood_waited: bool = False

    def __bool__(self) -> bool:
        return self.success

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return self.success == other
        if isinstance(other, SingleQuizDeliveryResult):
            return self.success == other.success and self.flood_waited == other.flood_waited
        return False

    def __hash__(self) -> int:
        return hash(self.success)


_DEFAULT_PACER = object()


class TelegramService:
    """Service for sending native quiz polls and checking chat permissions."""

    def __init__(self, pacer: Any = _DEFAULT_PACER):
        if pacer is _DEFAULT_PACER:
            from app.services.chat_pacer import default_chat_pacer

            self.pacer: Optional[Any] = default_chat_pacer
        else:
            self.pacer = pacer

    @property
    def last_flood_waited(self) -> bool:
        """Deprecated legacy attribute: flood-wait state is now operation-local in SingleQuizDeliveryResult."""
        return False

    @staticmethod
    async def _safe_delete_message(bot: Bot, chat_id: str | int, message_id: Optional[int]) -> bool:
        """
        Safely attempt to delete an orphaned message without raising exceptions.
        Used to clean up the introductory text block in Solution 1 if the subsequent poll fails.
        """
        if message_id is None:
            return False
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            logger.info("Successfully cleaned up orphaned message %s in %s", message_id, chat_id)
            return True
        except Exception as del_err:
            logger.warning(
                "Failed to clean up orphaned extended question message %s in %s: %s",
                message_id,
                chat_id,
                del_err,
            )
            return False

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
    ) -> SingleQuizDeliveryResult:
        """Publish a single native Telegram Quiz Poll with rich text formatting."""
        flood_waited = False
        # Pre-publish validation: ensure no unencodable lone surrogates reach the Telegram API
        if (
            has_lone_surrogates(question.question)
            or any(has_lone_surrogates(opt) for opt in question.options)
            or has_lone_surrogates(question.explanation)
        ):
            raise TelegramError(
                "Question, options, or explanation contains unencodable Unicode surrogate characters."
            )

        open_period = settings.time_limit if (settings.time_limit and 5 <= settings.time_limit <= 600) else None

        explanation_text = None
        if settings.explanation_enabled and question.explanation:
            explanation_text = format_rich_text_for_telegram(question.explanation, max_plain_length=200)

        # CHECK: Does this specific question have options exceeding 100 chars?
        # Only questions with oversized options use Solution 1 (Text block + Letter poll).
        # All other questions remain 100% UNTOUCHED (sent directly as native poll).
        if is_extended_option_question(question):
            # --- SOLUTION 1: Text Block + Letter Poll ---
            text_block = format_extended_question_message(question)
            try:
                if self.pacer:
                    await self.pacer.wait_for_turn(chat_id)
                sent_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=text_block,
                    parse_mode=ParseMode.HTML,
                )
                if self.pacer:
                    self.pacer.record_send(chat_id)
            except TelegramError as e:
                err_msg = str(e).lower()
                if "parse" in err_msg or "entity" in err_msg or "entities" in err_msg:
                    logger.warning("Extended question text HTML parse failed (%s). Retrying as plain text.", e)
                    plain_text = strip_html_tags(text_block)
                    if len(plain_text) > 4000:
                        plain_text = plain_text[:3997] + "..."
                    if self.pacer:
                        await self.pacer.wait_for_turn(chat_id)
                    sent_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=plain_text,
                        parse_mode=None,
                    )
                    if self.pacer:
                        self.pacer.record_send(chat_id)
                else:
                    raise

            # Step pacing: sub-delay between text message and poll if pacer not present
            if not self.pacer:
                app_settings = get_settings()
                sub_delay = getattr(app_settings, "EXTENDED_OPTION_SUB_DELAY", 1.0)
                if sub_delay > 0:
                    await asyncio.sleep(sub_delay)

            # Compact letter poll directly below the text block
            poll_prompt = "👇 Select your answer below:"
            poll_question = format_rich_text_for_telegram(poll_prompt, max_plain_length=300)
            letter_options = [f"Option {chr(ord('A') + i)}" for i in range(len(question.options))]
            sent_msg_id = getattr(sent_msg, "message_id", None)

            # Bounded poll retry loop (AUD-531): retry ONLY the poll so text block is never duplicated
            app_settings = get_settings()
            max_poll_retries = getattr(app_settings, "MAX_FLOOD_RETRIES", 3)
            poll_attempt = 0

            while True:
                try:
                    if self.pacer:
                        await self.pacer.wait_for_turn(chat_id)
                    await bot.send_poll(
                        chat_id=chat_id,
                        question=poll_question,
                        options=letter_options,
                        type=PollType.QUIZ,
                        is_anonymous=settings.is_anonymous,
                        correct_option_id=question.correct_option,
                        explanation=explanation_text,
                        open_period=open_period,
                        question_parse_mode=ParseMode.HTML,
                        explanation_parse_mode=ParseMode.HTML if explanation_text else None,
                    )
                    if self.pacer:
                        self.pacer.record_send(chat_id)
                    return SingleQuizDeliveryResult(success=True, flood_waited=flood_waited)
                except RetryAfter as e:
                    poll_attempt += 1
                    flood_waited = True
                    wait_seconds = int(e.retry_after) + 1
                    if self.pacer:
                        self.pacer.record_flood_wait(chat_id, wait_seconds)
                    if poll_attempt > max_poll_retries:
                        logger.error(
                            "Exhausted flood control retries (%d/%d) on extended poll for chat %s",
                            poll_attempt - 1,
                            max_poll_retries,
                            chat_id,
                        )
                        # Exhausted flood retries: definitive rejection -> delete orphaned text message
                        await self._safe_delete_message(bot, chat_id, sent_msg_id)
                        # Re-raise as TelegramError so outer PublishingService does NOT re-execute send_message
                        raise TelegramError(
                            f"Flood control limit reached on poll after {max_poll_retries} retries: {e.message}"
                        ) from e
                    logger.warning(
                        "Flood control on extended poll (attempt %d/%d), pausing for %ds",
                        poll_attempt,
                        max_poll_retries,
                        wait_seconds,
                    )
                    await asyncio.sleep(wait_seconds)
                    if self.pacer:
                        self.pacer.clear_flood_wait(chat_id)
                except TimedOut:
                    # Timeout: outcome uncertain; do NOT delete text message
                    raise
                except asyncio.CancelledError:
                    logger.warning("Publishing extended poll cancelled")
                    raise
                except TelegramError as e:
                    err_msg = str(e).lower()
                    if "parse" in err_msg or "entity" in err_msg or "entities" in err_msg:
                        logger.warning("Poll rich text parse failed (%s). Retrying plain text.", e)
                        plain_q = strip_html_tags(poll_question)
                        plain_explanation = strip_html_tags(explanation_text) if explanation_text else None
                        try:
                            if self.pacer:
                                await self.pacer.wait_for_turn(chat_id)
                            await bot.send_poll(
                                chat_id=chat_id,
                                question=plain_q[:300],
                                options=letter_options,
                                type=PollType.QUIZ,
                                is_anonymous=settings.is_anonymous,
                                correct_option_id=question.correct_option,
                                explanation=plain_explanation[:200] if plain_explanation else None,
                                open_period=open_period,
                                question_parse_mode=None,
                                explanation_parse_mode=None,
                            )
                            if self.pacer:
                                self.pacer.record_send(chat_id)
                            return SingleQuizDeliveryResult(success=True, flood_waited=flood_waited)
                        except Exception as fallback_poll_err:
                            if not isinstance(fallback_poll_err, (TimedOut, RetryAfter)):
                                await self._safe_delete_message(bot, chat_id, sent_msg_id)
                            raise
                    else:
                        await self._safe_delete_message(bot, chat_id, sent_msg_id)
                        raise
                except Exception as e:
                    await self._safe_delete_message(bot, chat_id, sent_msg_id)
                    raise
            return SingleQuizDeliveryResult(success=True, flood_waited=flood_waited)

        # --- UNTOUCHED: Standard question where all options <= 100 characters ---
        options = question.options
        raw_question = format_bilingual_question_text(question.display_question)
        poll_question = format_rich_text_for_telegram(raw_question, max_plain_length=300)

        try:
            if self.pacer:
                await self.pacer.wait_for_turn(chat_id)
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
            if self.pacer:
                self.pacer.record_send(chat_id)
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

                if self.pacer:
                    await self.pacer.wait_for_turn(chat_id)
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
                if self.pacer:
                    self.pacer.record_send(chat_id)
            else:
                raise
        return SingleQuizDeliveryResult(success=True, flood_waited=flood_waited)
