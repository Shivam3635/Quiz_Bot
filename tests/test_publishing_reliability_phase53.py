"""Comprehensive unit and regression tests for Phase 5.3: Publishing Reliability Improvements.

Covers:
- Task 1 (ENG-501): Partial failure cleanup in extended options (Solution 1).
- Task 2 (ENG-502): Safer timeout classification, structured failure tracking, and retry UX.
- Task 3 (ENG-503): Configurable bounded RetryAfter flood-control loop and cancellation preservation.
- Task 4 (ENG-504): Publishing throughput and pacing configuration.
- Task 5 (ENG-505): Header banner failure tracking and user notification.
"""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import Bot, CallbackQuery, Chat, Message, Update, User
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError, TimedOut

from app.bot.handlers.publish import (
    back_to_failed_summary_callback,
    confirm_retry_uncertain_callback,
    format_failed_summary_message,
    retry_confirmed_callback,
    retry_failed_callback,
    retry_with_uncertain_prompt_callback,
    start_publishing_callback,
)
from app.bot.handlers.settings import receive_channel_dest_message
from app.bot.handlers.myquizzes import (
    execute_republish_callback,
    receive_republish_dest_message,
)
from app.bot.keyboards.preview import (
    get_retry_keyboard,
    get_uncertain_confirm_keyboard,
)
from app.bot.states import QuizCreationState
from app.parser.models import QuizQuestion, QuizSettings
from app.services.chat_lock_manager import ChatLockManager, default_chat_lock_manager
from app.services.chat_pacer import ChatPacer, default_chat_pacer
from app.services.publishing_service import (
    FailedQuestionItem,
    PublishingService,
    PublishingSummary,
)
from app.services.telegram_service import SingleQuizDeliveryResult, TelegramService


@pytest.fixture(autouse=True)
def reset_shared_pacer():
    """Ensure test isolation by resetting default_chat_pacer state before and after each test."""
    default_chat_pacer.reset_all()
    yield
    default_chat_pacer.reset_all()


@pytest.fixture
def sample_questions() -> list[QuizQuestion]:
    return [
        QuizQuestion(question="Q1?", options=["A", "B"], correct_option=0),
        QuizQuestion(question="Q2?", options=["C", "D"], correct_option=1),
        QuizQuestion(question="Q3?", options=["E", "F"], correct_option=0),
    ]


@pytest.fixture
def default_settings() -> QuizSettings:
    return QuizSettings(is_anonymous=True, header_banner_enabled=False)


# ============================================================================
# TASK 2: ENG-502 Safer Timeout Handling and Retry UX
# ============================================================================


class TestTimeoutClassificationAndRetryUX:
    """Verifies timeout classification, structured failed records, and safe retry UX."""

    @pytest.mark.asyncio
    async def test_timeout_classified_as_uncertain(self, sample_questions, default_settings):
        """TimedOut exception must be classified with is_uncertain=True and delivery_status='uncertain'."""
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                True,  # Q1 OK
                TimedOut("Gateway Timeout from Telegram"),  # Q2 Timeout
                True,  # Q3 OK
            ]
        )
        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001

        mock_bot = MagicMock(spec=Bot)
        summary = await service.publish_batch(
            bot=mock_bot,
            target_chat_id=123,
            questions=sample_questions,
            settings=default_settings,
        )

        assert summary.total == 3
        assert summary.successful == 2
        assert summary.failed == 1
        assert summary.has_uncertain_failures is True
        assert len(summary.uncertain_items) == 1
        assert len(summary.definitive_failed_items) == 0

        failed_item = summary.failed_items[0]
        assert failed_item.index == 2
        assert failed_item.is_uncertain is True
        assert failed_item.delivery_status == "uncertain"
        assert "timed out" in failed_item.error_message.lower()

    @pytest.mark.asyncio
    async def test_bad_request_classified_as_definitive_failure(self, sample_questions, default_settings):
        """BadRequest exception must be classified with is_uncertain=False and delivery_status='failed'."""
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                BadRequest("Poll option text too long"),
                True,
                True,
            ]
        )
        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001

        mock_bot = MagicMock(spec=Bot)
        summary = await service.publish_batch(
            bot=mock_bot,
            target_chat_id=123,
            questions=sample_questions,
            settings=default_settings,
        )

        assert summary.successful == 2
        assert summary.failed == 1
        assert summary.has_uncertain_failures is False
        assert len(summary.uncertain_items) == 0
        assert len(summary.definitive_failed_items) == 1

        failed_item = summary.failed_items[0]
        assert failed_item.index == 1
        assert failed_item.is_uncertain is False
        assert failed_item.delivery_status == "failed"

    def test_retry_keyboard_definitive_vs_uncertain(self):
        """Retry keyboard must offer duplicate-safe choices when uncertain items exist."""
        # 1. Standard (all definitive failures): simple retry button
        kb_definitive = get_retry_keyboard(has_uncertain=False)
        buttons_def = [b.callback_data for row in kb_definitive.inline_keyboard for b in row]
        assert "retry_failed_quizzes" in buttons_def
        assert "retry_confirmed_quizzes" not in buttons_def

        # 2. Both confirmed failures and uncertain items
        kb_mixed = get_retry_keyboard(has_uncertain=True, has_confirmed=True, confirmed_count=2)
        buttons_mixed = [b.callback_data for row in kb_mixed.inline_keyboard for b in row]
        assert "retry_confirmed_quizzes" in buttons_mixed
        assert "retry_with_uncertain_prompt" in buttons_mixed

        # 3. Only uncertain items
        kb_only_unc = get_retry_keyboard(has_uncertain=True, has_confirmed=False, confirmed_count=0)
        buttons_unc = [b.callback_data for row in kb_only_unc.inline_keyboard for b in row]
        assert "retry_confirmed_quizzes" not in buttons_unc
        assert "retry_with_uncertain_prompt" in buttons_unc

    def test_format_failed_summary_message_warning_text(self, sample_questions):
        """Summary message must clearly warn about duplicate risks when uncertain items exist."""
        # Uncertain failure summary
        unc_item = FailedQuestionItem(
            index=1,
            question=sample_questions[0],
            error_message="Request timed out",
            is_uncertain=True,
            delivery_status="uncertain",
        )
        summary_unc = PublishingSummary(
            total=1, successful=0, failed=1, failed_items=[unc_item], banner_sent=None
        )
        text_unc = format_failed_summary_message(summary_unc, target_chat="@test")
        assert "Uncertain delivery (timeout)" in text_unc
        assert "duplicate polls" in text_unc.lower()
        assert "check your channel" in text_unc.lower()

        # Definitive failure summary
        def_item = FailedQuestionItem(
            index=1,
            question=sample_questions[0],
            error_message="Chat not found",
            is_uncertain=False,
            delivery_status="failed",
        )
        summary_def = PublishingSummary(
            total=1, successful=0, failed=1, failed_items=[def_item], banner_sent=None
        )
        text_def = format_failed_summary_message(summary_def, target_chat="@test")
        assert "Failed to post" in text_def
        assert "duplicate polls" not in text_def.lower()


# ============================================================================
# TASK 3: ENG-503 Improved RetryAfter Handling
# ============================================================================


class TestImprovedRetryAfterHandling:
    """Verifies configurable bounded RetryAfter flood-control loop and error escalation."""

    @pytest.mark.asyncio
    async def test_single_retry_after_succeeds(self, sample_questions, default_settings):
        """Single flood control wait is honored and succeeds on next attempt."""
        mock_telegram = MagicMock(spec=TelegramService)
        # Q1 triggers RetryAfter(2), then succeeds
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                RetryAfter(2),
                True,
                True,  # Q2
                True,  # Q3
            ]
        )
        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001
        service.settings.MAX_FLOOD_RETRIES = 3

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,
                settings=default_settings,
            )

        assert summary.successful == 3
        assert summary.failed == 0
        assert summary.is_complete_success
        # Must have slept for 2 + 1 = 3 seconds on flood wait
        mock_sleep.assert_any_call(3)

    @pytest.mark.asyncio
    async def test_multiple_retry_after_succeeds_within_bounds(self, sample_questions, default_settings):
        """Multiple consecutive RetryAfter responses succeed within MAX_FLOOD_RETRIES."""
        mock_telegram = MagicMock(spec=TelegramService)
        # Q1 triggers RetryAfter(1) twice, then succeeds on attempt 3
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                RetryAfter(1),  # attempt 1 -> wait 2
                RetryAfter(2),  # attempt 2 -> wait 3
                True,  # attempt 3 -> OK
                True,  # Q2
                True,  # Q3
            ]
        )
        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001
        service.settings.MAX_FLOOD_RETRIES = 3

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,
                settings=default_settings,
            )

        assert summary.successful == 3
        assert summary.failed == 0
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(3)

    @pytest.mark.asyncio
    async def test_max_flood_retries_exhausted(self, sample_questions, default_settings):
        """When MAX_FLOOD_RETRIES is exceeded, the question fails and loop continues to next question."""
        mock_telegram = MagicMock(spec=TelegramService)
        # Q1 triggers RetryAfter continuously
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                RetryAfter(1),
                RetryAfter(1),
                RetryAfter(1),
                RetryAfter(1),  # 4th time -> exceeds MAX_FLOOD_RETRIES=3
                True,  # Q2 OK
                True,  # Q3 OK
            ]
        )
        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001
        service.settings.MAX_FLOOD_RETRIES = 3

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            summary = await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,
                settings=default_settings,
            )

        assert summary.total == 3
        assert summary.successful == 2
        assert summary.failed == 1
        assert "Flood control limit reached after 3 retries" in summary.failed_items[0].error_message

    @pytest.mark.asyncio
    async def test_retry_after_followed_by_bad_request(self, sample_questions, default_settings):
        """When RetryAfter is followed by a non-retryable BadRequest, fails immediately without further retries."""
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                RetryAfter(1),
                BadRequest("Poll text too long"),
                True,  # Q2
                True,  # Q3
            ]
        )
        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001
        service.settings.MAX_FLOOD_RETRIES = 3

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            summary = await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,
                settings=default_settings,
            )

        assert summary.successful == 2
        assert summary.failed == 1
        assert "Poll text too long" in summary.failed_items[0].error_message

    @pytest.mark.asyncio
    async def test_cancellation_preserved_during_flood_wait(self, sample_questions, default_settings):
        """asyncio.CancelledError must not be swallowed during publishing or flood wait."""
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(side_effect=asyncio.CancelledError())

        service = PublishingService(telegram_service=mock_telegram)
        mock_bot = MagicMock(spec=Bot)

        with pytest.raises(asyncio.CancelledError):
            await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,
                settings=default_settings,
            )


# ============================================================================
# TASK 4 & 5: ENG-504 Pacing & ENG-505 Header Banner Failure Reporting
# ============================================================================


class TestHeaderBannerAndPacing:
    """Verifies header banner delivery tracking, error reporting, and pacing delays."""

    @pytest.mark.asyncio
    async def test_banner_enabled_and_succeeds(self, sample_questions):
        """Header banner is successfully sent and tracked as banner_sent=True."""
        settings = QuizSettings(header_banner_enabled=True)
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001
        service.settings.HEADER_BANNER_DELAY = 0.0001

        mock_bot = MagicMock(spec=Bot)
        mock_bot.send_message = AsyncMock(return_value=MagicMock())

        summary = await service.publish_batch(
            bot=mock_bot,
            target_chat_id=123,
            questions=sample_questions,
            settings=settings,
        )

        assert summary.banner_sent is True
        assert summary.banner_failed is False
        assert summary.is_complete_success is True
        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_banner_fails_quizzes_continue_and_tracked(self, sample_questions):
        """Header banner failure does NOT abort batch and is tracked in banner_failed."""
        settings = QuizSettings(header_banner_enabled=True)
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001

        mock_bot = MagicMock(spec=Bot)
        mock_bot.send_message = AsyncMock(side_effect=TelegramError("Cannot send text in channel"))

        summary = await service.publish_batch(
            bot=mock_bot,
            target_chat_id=123,
            questions=sample_questions,
            settings=settings,
        )

        # Quizzes still succeed!
        assert summary.successful == 3
        assert summary.failed == 0
        assert summary.banner_sent is False
        assert summary.banner_failed is True
        assert summary.is_complete_success is True

    @pytest.mark.asyncio
    async def test_banner_disabled_tracked_as_none(self, sample_questions):
        """When header banner is disabled, banner_sent is None and no banner message is sent."""
        settings = QuizSettings(header_banner_enabled=False)
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.0001

        mock_bot = MagicMock(spec=Bot)
        mock_bot.send_message = AsyncMock()

        summary = await service.publish_batch(
            bot=mock_bot,
            target_chat_id=123,
            questions=sample_questions,
            settings=settings,
        )

        assert summary.banner_sent is None
        assert summary.banner_failed is False
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_standard_poll_pacing_delay(self, sample_questions):
        """Verify that DEFAULT_DELAY_BETWEEN_POSTS is called between posts and NOT after the final post."""
        settings = QuizSettings(header_banner_enabled=False)
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 2.5

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,  # 3 questions
                settings=settings,
            )

        # Should sleep 2.5s between Q1-Q2 and Q2-Q3 (exactly 2 times for 3 questions, zero after Q3)
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert pytest.approx(call.args[0], abs=0.05) == 2.5

    @pytest.mark.asyncio
    async def test_extended_option_sub_delay_pacing(self):
        """Verify that centralized pacer paces between text message and poll."""
        service = TelegramService()
        bot = AsyncMock()
        mock_msg = MagicMock()
        mock_msg.message_id = 999
        bot.send_message.return_value = mock_msg
        bot.send_poll.return_value = MagicMock()

        ext_q = QuizQuestion(
            question="What is this treaty?",
            options=["Long option exceeding 100 characters " * 4, "Short option"],
            correct_option=0,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await service.publish_single_quiz(
                bot=bot,
                chat_id="@channel",
                question=ext_q,
                settings=QuizSettings(),
            )

        # Must sleep for configured pacing interval (3.0s by default)
        assert mock_sleep.call_count == 1
        assert pytest.approx(mock_sleep.call_args[0][0], abs=0.05) == 3.0

    @pytest.mark.asyncio
    async def test_flood_wait_skips_redundant_post_delay(self, sample_questions):
        """When a question encounters flood control, retry waits flood duration, and subsequent question paces cleanly."""
        settings = QuizSettings(header_banner_enabled=False)
        mock_telegram = MagicMock(spec=TelegramService)
        # Q1 encounters RetryAfter(5), then succeeds. Q2 and Q3 succeed normally.
        mock_telegram.publish_single_quiz = AsyncMock(
            side_effect=[
                RetryAfter(5),
                True,  # Q1 retry
                True,  # Q2
                True,  # Q3
            ]
        )

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 2.5
        service.settings.MAX_FLOOD_RETRIES = 3

        mock_bot = MagicMock(spec=Bot)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=sample_questions,
                settings=settings,
            )

        # Sleep calls:
        # 1. Flood wait sleep on Q1: 5 + 1 = 6s
        # 2. Pacing interval between Q1 retry and Q2: 2.5s
        # 3. Pacing interval between Q2 and Q3: 2.5s
        # Q3 is final -> no trailing delay!
        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list[0].args[0] == 6
        assert pytest.approx(mock_sleep.call_args_list[1].args[0], abs=0.05) == 2.5
        assert pytest.approx(mock_sleep.call_args_list[2].args[0], abs=0.05) == 2.5

    @pytest.mark.asyncio
    async def test_header_banner_delay_respected(self, sample_questions):
        """When header banner is enabled, HEADER_BANNER_DELAY is respected before Q1."""
        settings = QuizSettings(header_banner_enabled=True)
        mock_telegram = MagicMock(spec=TelegramService)
        mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

        service = PublishingService(telegram_service=mock_telegram)
        service.settings.HEADER_BANNER_DELAY = 2.0
        service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 2.5

        mock_bot = MagicMock(spec=Bot)
        mock_bot.send_message = AsyncMock(return_value=MagicMock())

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await service.publish_batch(
                bot=mock_bot,
                target_chat_id=123,
                questions=[sample_questions[0]],  # 1 question
                settings=settings,
            )

        # Should sleep exactly once for HEADER_BANNER_DELAY (2.0s).
        # Since there is only 1 question, no post-question delay should occur!
        assert mock_sleep.call_count == 1
        assert pytest.approx(mock_sleep.call_args[0][0], abs=0.05) == 2.0


# ============================================================================
# Handlers and UX Flow Tests
# ============================================================================


class TestPublishingHandlerUX:
    """Verifies user-facing retry, confirmation, and warning flows in publish handlers."""

    def _create_mock_update_and_context(self, user_data=None):
        update = MagicMock(spec=Update)
        query = MagicMock(spec=CallbackQuery)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock(spec=Message)
        query.message.chat_id = 99999
        query.message.edit_text = AsyncMock()
        update.callback_query = query
        update.effective_user = MagicMock(spec=User)
        update.effective_user.id = 123456
        update.effective_user.username = "tester"
        update.effective_chat = MagicMock(spec=Chat)
        update.effective_chat.id = 99999

        context = MagicMock()
        context.user_data = user_data or {}
        context.bot = MagicMock(spec=Bot)
        return update, context

    @pytest.mark.asyncio
    async def test_start_publishing_shows_banner_warning_on_complete_success(self, sample_questions):
        """When quiz publishing succeeds but banner fails, final message includes banner note."""
        update, context = self._create_mock_update_and_context(
            user_data={
                "bulk_questions": sample_questions,
                "quiz_settings": QuizSettings(header_banner_enabled=True),
            }
        )

        mock_summary = PublishingSummary(
            total=3,
            successful=3,
            failed=0,
            failed_items=[],
            banner_sent=False,  # Banner failed
        )

        with patch("app.bot.handlers.publish.publishing_service.publish_batch", AsyncMock(return_value=mock_summary)):
            state = await start_publishing_callback(update, context)

            assert state == -1  # ConversationHandler.END
            call_text = update.callback_query.edit_message_text.return_value.edit_text.call_args[0][0]
            assert "header banner could not be posted and was omitted" in call_text

    @pytest.mark.asyncio
    async def test_start_publishing_with_uncertain_failures_displays_warning(self, sample_questions):
        """When timeouts occur, summary message includes duplicate warning and safe retry keyboard."""
        update, context = self._create_mock_update_and_context(
            user_data={
                "bulk_questions": sample_questions,
                "quiz_settings": QuizSettings(),
            }
        )

        unc_item = FailedQuestionItem(
            index=2,
            question=sample_questions[1],
            error_message="Timed out",
            is_uncertain=True,
            delivery_status="uncertain",
        )
        def_item = FailedQuestionItem(
            index=1,
            question=sample_questions[0],
            error_message="Chat not found",
            is_uncertain=False,
            delivery_status="failed",
        )

        mock_summary = PublishingSummary(
            total=3,
            successful=1,
            failed=2,
            failed_items=[def_item, unc_item],
            banner_sent=None,
        )

        with patch("app.bot.handlers.publish.publishing_service.publish_batch", AsyncMock(return_value=mock_summary)):
            state = await start_publishing_callback(update, context)

            assert state == QuizCreationState.CONFIRMING_PUBLISH
            call_kwargs = update.callback_query.edit_message_text.return_value.edit_text.call_args.kwargs
            call_text = update.callback_query.edit_message_text.return_value.edit_text.call_args[0][0]

            assert "Uncertain delivery (timeout): <b>1</b>" in call_text
            assert "Confirmed failed: <b>1</b>" in call_text
            assert "duplicate polls" in call_text.lower()

            reply_markup = call_kwargs["reply_markup"]
            button_callbacks = [b.callback_data for row in reply_markup.inline_keyboard for b in row]
            assert "retry_confirmed_quizzes" in button_callbacks
            assert "retry_with_uncertain_prompt" in button_callbacks

    @pytest.mark.asyncio
    async def test_retry_confirmed_callback_only_retries_confirmed_failures(self, sample_questions):
        """retry_confirmed_callback isolates confirmed failures and does not retry uncertain questions."""
        def_q = sample_questions[0]
        unc_q = sample_questions[1]

        update, context = self._create_mock_update_and_context(
            user_data={
                "bulk_questions": [def_q, unc_q],
                "definitive_failed_questions": [def_q],
                "uncertain_failed_questions": [unc_q],
                "quiz_settings": QuizSettings(),
            }
        )

        mock_summary = PublishingSummary(total=1, successful=1, failed=0, failed_items=[], banner_sent=None)

        with patch("app.bot.handlers.publish.publishing_service.publish_batch", AsyncMock(return_value=mock_summary)) as mock_pub:
            await retry_confirmed_callback(update, context)

            # Verification: only the definitive question was passed to publish_batch!
            assert mock_pub.call_args.kwargs["questions"] == [def_q]

    @pytest.mark.asyncio
    async def test_retry_with_uncertain_prompt_callback_prompts_confirmation(self, sample_questions):
        """retry_with_uncertain_prompt_callback displays confirmation prompt before retrying."""
        update, context = self._create_mock_update_and_context(
            user_data={
                "uncertain_failed_questions": [sample_questions[0]],
            }
        )

        state = await retry_with_uncertain_prompt_callback(update, context)
        assert state == QuizCreationState.CONFIRMING_PUBLISH

        edit_call = update.callback_query.edit_message_text.call_args
        prompt_text = edit_call[0][0]
        reply_markup = edit_call.kwargs["reply_markup"]

        assert "Duplicate Poll Risk Confirmation" in prompt_text
        button_callbacks = [b.callback_data for row in reply_markup.inline_keyboard for b in row]
        assert "confirm_retry_uncertain" in button_callbacks
        assert "back_to_failed_summary" in button_callbacks

    @pytest.mark.asyncio
    async def test_confirm_retry_uncertain_callback_retries_all(self, sample_questions):
        """confirm_retry_uncertain_callback re-populates all failed items and retries."""
        q1, q2 = sample_questions[0], sample_questions[1]
        item1 = FailedQuestionItem(index=1, question=q1, error_message="err1")
        item2 = FailedQuestionItem(index=2, question=q2, error_message="err2", is_uncertain=True)

        update, context = self._create_mock_update_and_context(
            user_data={
                "failed_questions": [item1, item2],
                "quiz_settings": QuizSettings(),
            }
        )

        mock_summary = PublishingSummary(total=2, successful=2, failed=0, failed_items=[], banner_sent=None)

        with patch("app.bot.handlers.publish.publishing_service.publish_batch", AsyncMock(return_value=mock_summary)) as mock_pub:
            await confirm_retry_uncertain_callback(update, context)

            assert mock_pub.call_args.kwargs["questions"] == [q1, q2]

    @pytest.mark.asyncio
    async def test_back_to_failed_summary_callback_restores_view(self, sample_questions):
        """back_to_failed_summary_callback restores the failed summary message without publishing."""
        unc_item = FailedQuestionItem(
            index=1,
            question=sample_questions[0],
            error_message="Timed out",
            is_uncertain=True,
            delivery_status="uncertain",
        )
        summary = PublishingSummary(
            total=1, successful=0, failed=1, failed_items=[unc_item], banner_sent=None
        )

        update, context = self._create_mock_update_and_context(
            user_data={
                "last_publishing_summary": summary,
                "target_chat": "@mychannel",
            }
        )

        state = await back_to_failed_summary_callback(update, context)
        assert state == QuizCreationState.CONFIRMING_PUBLISH

        edit_call = update.callback_query.edit_message_text.call_args
        msg_text = edit_call[0][0]
        assert "Uncertain delivery (timeout)" in msg_text


# ============================================================================
# Phase 5.3.3: Concurrency Isolation & Shared State Tests
# ============================================================================


class TestPhase533ConcurrencyIsolation:
    """Verifies per-chat lock serialization, concurrent multi-chat publishing, and operation-local flood state."""

    @pytest.mark.asyncio
    async def test_two_concurrent_publish_batch_to_different_chats(self, sample_questions):
        """Operations targeting different chats run concurrently without blocking each other."""
        service = PublishingService()
        bot = AsyncMock()

        executed_chats = []

        async def fake_publish_single(bot, chat_id, question, settings):
            executed_chats.append((chat_id, question.question))
            await asyncio.sleep(0.01)
            return SingleQuizDeliveryResult(success=True)

        service.telegram_service.publish_single_quiz = fake_publish_single

        # Run concurrent batches to two different chats
        q_batch1 = [sample_questions[0]]
        q_batch2 = [sample_questions[1]]

        t1 = asyncio.create_task(
            service.publish_batch(bot=bot, target_chat_id=101, questions=q_batch1, settings=QuizSettings())
        )
        t2 = asyncio.create_task(
            service.publish_batch(bot=bot, target_chat_id=102, questions=q_batch2, settings=QuizSettings())
        )

        res1, res2 = await asyncio.gather(t1, t2)

        assert res1.is_complete_success
        assert res2.is_complete_success
        assert len(executed_chats) == 2
        chat_ids = {c[0] for c in executed_chats}
        assert chat_ids == {101, 102}

    @pytest.mark.asyncio
    async def test_two_concurrent_publish_batch_to_same_chat_serialized(self, sample_questions):
        """Operations targeting the same chat execute sequentially; batches do not interleave."""
        lock_manager = ChatLockManager()
        service = PublishingService(chat_lock_manager=lock_manager)
        bot = AsyncMock()

        execution_log = []

        async def fake_publish(bot, chat_id, question, settings):
            execution_log.append(f"start_{question.question}")
            await asyncio.sleep(0.02)
            execution_log.append(f"end_{question.question}")
            return SingleQuizDeliveryResult(success=True)

        service.telegram_service.publish_single_quiz = fake_publish

        batch_a = [
            QuizQuestion(question="A1", options=["1", "2"], correct_option=0),
            QuizQuestion(question="A2", options=["1", "2"], correct_option=0),
        ]
        batch_b = [
            QuizQuestion(question="B1", options=["1", "2"], correct_option=0),
            QuizQuestion(question="B2", options=["1", "2"], correct_option=0),
        ]

        t_a = asyncio.create_task(
            service.publish_batch(bot=bot, target_chat_id=999, questions=batch_a, settings=QuizSettings())
        )
        t_b = asyncio.create_task(
            service.publish_batch(bot=bot, target_chat_id=999, questions=batch_b, settings=QuizSettings())
        )

        res_a, res_b = await asyncio.gather(t_a, t_b)

        assert res_a.is_complete_success
        assert res_b.is_complete_success

        # Verify no interleaving: either all A's completed then all B's, or vice versa
        a_indices = [i for i, entry in enumerate(execution_log) if "A" in entry]
        b_indices = [i for i, entry in enumerate(execution_log) if "B" in entry]

        assert max(a_indices) < min(b_indices) or max(b_indices) < min(a_indices)

    @pytest.mark.asyncio
    async def test_concurrent_operations_one_flood_wait_one_succeeds(self, sample_questions):
        """A flood wait on one destination chat does NOT block or delay an independent chat."""
        service = PublishingService()
        bot = AsyncMock()

        async def fake_publish(bot, chat_id, question, settings):
            if chat_id == 501:
                # Chat 501 encounters RetryAfter then succeeds
                if not hasattr(fake_publish, "hit_501"):
                    fake_publish.hit_501 = True
                    raise RetryAfter(2)
            return SingleQuizDeliveryResult(success=True)

        service.telegram_service.publish_single_quiz = fake_publish
        service.settings.MAX_FLOOD_RETRIES = 3

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            t1 = asyncio.create_task(
                service.publish_batch(bot=bot, target_chat_id=501, questions=[sample_questions[0]], settings=QuizSettings())
            )
            t2 = asyncio.create_task(
                service.publish_batch(bot=bot, target_chat_id=502, questions=[sample_questions[1]], settings=QuizSettings())
            )

            res1, res2 = await asyncio.gather(t1, t2)

        assert res1.is_complete_success
        assert res2.is_complete_success
        # Chat 501 slept for RetryAfter (2 + 1 = 3s)
        mock_sleep.assert_any_call(3)

    @pytest.mark.asyncio
    async def test_cancellation_while_waiting_for_per_chat_lock(self):
        """When a coroutine waiting for a per-chat lock is cancelled, it releases cleanly without affecting the holder."""
        lock_manager = ChatLockManager()

        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def holder_operation():
            async with lock_manager.lock_chat(777):
                holder_started.set()
                await holder_release.wait()
            return "holder_done"

        async def waiter_operation():
            async with lock_manager.lock_chat(777):
                return "waiter_done"

        t_holder = asyncio.create_task(holder_operation())
        await holder_started.wait()

        # Holder now holds lock for 777
        assert lock_manager.is_locked(777)
        assert lock_manager.get_waiter_count(777) == 1

        t_waiter = asyncio.create_task(waiter_operation())
        await asyncio.sleep(0.01)
        assert lock_manager.get_waiter_count(777) == 2

        # Cancel the waiter task
        t_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t_waiter

        # Holder should STILL be holding the lock safely
        assert lock_manager.is_locked(777)
        assert lock_manager.get_waiter_count(777) == 1

        # Release holder
        holder_release.set()
        res_holder = await t_holder
        assert res_holder == "holder_done"

        # Now lock should be completely unlocked and cleaned up
        assert not lock_manager.is_locked(777)
        assert lock_manager.get_waiter_count(777) == 0

    @pytest.mark.asyncio
    async def test_lock_release_following_exceptions(self, sample_questions):
        """If an unexpected exception occurs inside the chat lock context, the lock is cleanly released."""
        lock_manager = ChatLockManager()

        with pytest.raises(RuntimeError, match="Catastrophic disk failure"):
            async with lock_manager.lock_chat(888):
                raise RuntimeError("Catastrophic disk failure")

        assert not lock_manager.is_locked(888)
        assert lock_manager.get_waiter_count(888) == 0

    @pytest.mark.asyncio
    async def test_no_shared_flood_wait_state_between_concurrent_calls(self):
        """Two concurrent publish_single_quiz operations maintain independent, operation-local flood state."""
        tg_service = TelegramService(pacer=None)
        bot = AsyncMock()

        call1_started = asyncio.Event()

        async def poll_side_effect(*args, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id == "chat_flood":
                if not hasattr(poll_side_effect, "flooded"):
                    poll_side_effect.flooded = True
                    call1_started.set()
                    raise RetryAfter(0)
            return True

        bot.send_poll.side_effect = poll_side_effect

        long_opt = "Option text exceeding 100 characters for testing " * 3
        q1 = QuizQuestion(question="Q1?", options=["A", long_opt], correct_option=0)
        q2 = QuizQuestion(question="Q2?", options=["C", long_opt], correct_option=1)

        mock_msg = MagicMock()
        mock_msg.message_id = 999
        bot.send_message.return_value = mock_msg

        with patch("asyncio.sleep", new_callable=AsyncMock):
            task1 = asyncio.create_task(
                tg_service.publish_single_quiz(bot=bot, chat_id="chat_flood", question=q1, settings=QuizSettings())
            )
            await call1_started.wait()

            task2 = asyncio.create_task(
                tg_service.publish_single_quiz(bot=bot, chat_id="chat_clean", question=q2, settings=QuizSettings())
            )

            res1, res2 = await asyncio.gather(task1, task2)

        # res1 suffered flood wait, res2 did not!
        assert res1.success is True
        assert res1.flood_waited is True
        assert res2.success is True
        assert res2.flood_waited is False


# ============================================================================
# Phase 5.3.3: Centralized Message Pacing Tests
# ============================================================================


class TestPhase533CentralizedMessagePacing:
    """Verifies ChatPacer tracks monotonic timestamps across all message types and destinations."""

    @pytest.mark.asyncio
    async def test_standard_multiquestion_batch_monotonic_pacing(self, sample_questions):
        """Standard batch paces 3.0s between each post and terminates immediately after final post."""
        pacer = ChatPacer(pacing_interval=3.0)
        service = PublishingService(chat_pacer=pacer)
        bot = AsyncMock()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=bot,
                target_chat_id=3001,
                questions=sample_questions,
                settings=QuizSettings(header_banner_enabled=False),
            )

        assert summary.is_complete_success
        # Slept before Q2 and before Q3, zero after Q3
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert pytest.approx(call.args[0], abs=0.05) == 3.0

    @pytest.mark.asyncio
    async def test_extended_option_multiquestion_batch_paces_both_text_and_poll(self):
        """Extended question batch paces before text message, before poll, and before subsequent questions."""
        pacer = ChatPacer(pacing_interval=3.0)
        service = PublishingService(chat_pacer=pacer)
        bot = AsyncMock()
        mock_msg = MagicMock()
        mock_msg.message_id = 701
        bot.send_message.return_value = mock_msg
        bot.send_poll.return_value = MagicMock()

        long_opt = "Option text exceeding 100 characters for testing " * 3
        questions = [
            QuizQuestion(question="Ext1?", options=["A", long_opt], correct_option=0),
            QuizQuestion(question="Ext2?", options=["B", long_opt], correct_option=0),
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=bot,
                target_chat_id=3002,
                questions=questions,
                settings=QuizSettings(header_banner_enabled=False),
            )

        assert summary.is_complete_success
        # Outgoing messages:
        # 1. Ext1 text (0s delay - first message to chat)
        # 2. Ext1 poll (3.0s delay after Ext1 text)
        # 3. Ext2 text (3.0s delay after Ext1 poll)
        # 4. Ext2 poll (3.0s delay after Ext2 text)
        # Final message: Ext2 poll -> zero trailing sleep!
        assert mock_sleep.call_count == 3
        for call in mock_sleep.call_args_list:
            assert pytest.approx(call.args[0], abs=0.05) == 3.0

    @pytest.mark.asyncio
    async def test_banner_followed_by_first_question_pacing(self, sample_questions):
        """Banner message paces before question 1 with no trailing delay after question 1."""
        pacer = ChatPacer(pacing_interval=3.0)
        service = PublishingService(chat_pacer=pacer)
        service.settings.HEADER_BANNER_DELAY = 2.0
        bot = AsyncMock()
        bot.send_message.return_value = MagicMock()
        bot.send_poll.return_value = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=bot,
                target_chat_id=3003,
                questions=[sample_questions[0]],  # 1 question
                settings=QuizSettings(header_banner_enabled=True),
            )

        assert summary.is_complete_success
        # Banner sent immediately (first msg)
        # Q1 poll paced 2.0s after banner
        # Zero sleep after Q1
        assert mock_sleep.call_count == 1
        assert pytest.approx(mock_sleep.call_args[0][0], abs=0.05) == 2.0

    @pytest.mark.asyncio
    async def test_retry_after_followed_by_next_question_pacing(self, sample_questions):
        """When Q1 encounters flood wait, retry occurs after flood wait, and Q2 is spaced by pacing interval."""
        pacer = ChatPacer(pacing_interval=3.0)
        service = PublishingService(chat_pacer=pacer)
        bot = AsyncMock()

        call_count = 0

        async def poll_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Q1 first attempt
                raise RetryAfter(4)
            return True

        bot.send_poll.side_effect = poll_side_effect

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            summary = await service.publish_batch(
                bot=bot,
                target_chat_id=3004,
                questions=[sample_questions[0], sample_questions[1]],
                settings=QuizSettings(header_banner_enabled=False),
            )

        assert summary.is_complete_success
        # Sleeps:
        # 1. Q1 RetryAfter: 4 + 1 = 5s
        # 2. Before Q2: 3.0s pacing interval after Q1 retry completion
        # Zero trailing sleep after Q2
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == 5
        assert pytest.approx(mock_sleep.call_args_list[1].args[0], abs=0.05) == 3.0

    @pytest.mark.asyncio
    async def test_different_chats_paced_independently(self):
        """Pacing records for one chat do not affect permissible send times for another chat."""
        pacer = ChatPacer(pacing_interval=5.0)

        # Record send for chat 1 at t=100
        pacer.record_send("chat_alpha")
        next_alpha = pacer.get_next_send_time("chat_alpha")
        next_beta = pacer.get_next_send_time("chat_beta")

        assert next_alpha > 0.0
        assert next_beta == 0.0  # chat_beta is completely unconstrained

        # chat_beta wait_for_turn should return 0.0 immediately
        wait_beta = await pacer.wait_for_turn("chat_beta")
        assert wait_beta == 0.0


# ============================================================================
# PHASE 5.3.5: Publishing Integration Fixes
# ============================================================================


class TestPhase535IntegrationFixes:
    """Regression and integration tests for Phase 5.3.5 follow-up fixes."""

    def test_normalize_chat_id_consistency(self):
        """ChatLockManager and ChatPacer normalize numeric IDs and usernames consistently."""
        for manager in (ChatLockManager, ChatPacer):
            # Numeric int vs numeric str match
            assert manager.normalize_chat_id("-100123") == "-100123"
            assert manager.normalize_chat_id(-100123) == "-100123"
            assert manager.normalize_chat_id("-100123") == manager.normalize_chat_id(-100123)
            assert manager.normalize_chat_id(" -1001234567890 ") == "-1001234567890"
            assert manager.normalize_chat_id(-1001234567890) == "-1001234567890"

            # Positive numeric IDs
            assert manager.normalize_chat_id(987654) == "987654"
            assert manager.normalize_chat_id("987654") == "987654"
            assert manager.normalize_chat_id(987654) == manager.normalize_chat_id("987654")

            # Usernames stripped of @ and lowercased
            assert manager.normalize_chat_id("@MyChannel") == "mychannel"
            assert manager.normalize_chat_id("mychannel") == "mychannel"
            assert manager.normalize_chat_id("@mychannel") == manager.normalize_chat_id("MyChannel")
            assert manager.normalize_chat_id("  @Quiz_Channel  ") == "quiz_channel"

    @pytest.mark.asyncio
    async def test_settings_channel_dest_persists_canonical_chat_id(self):
        """Settings handler persists canonical numeric chat_id returned by validate_channel_access."""
        update = MagicMock()
        update.message = AsyncMock()
        update.message.text = "@cool_quiz_channel"
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {"quiz_settings": QuizSettings()}

        with patch("app.bot.handlers.settings.telegram_service.validate_channel_access", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "Access confirmed", -1001999888777)
            state = await receive_channel_dest_message(update, context)

        assert state == QuizCreationState.CONFIGURING_SETTINGS
        saved_settings: QuizSettings = context.user_data["quiz_settings"]
        # Must persist canonical numeric string, not raw username
        assert saved_settings.channel_id == "-1001999888777"
        mock_val.assert_called_once_with(context.bot, "@cool_quiz_channel")

    @pytest.mark.asyncio
    async def test_republish_dest_message_persists_canonical_chat_id(self):
        """MyQuizzes republish destination override persists canonical numeric chat_id."""
        update = MagicMock()
        update.message = AsyncMock()
        update.message.text = "@republish_target"
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {"waiting_for_republish_dest_for": 42}

        with patch("app.bot.handlers.myquizzes.telegram_service.validate_channel_access", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "Access confirmed", -1002223334445)
            result = await receive_republish_dest_message(update, context)

        assert result is True
        # Must persist canonical numeric string in custom_dest override
        assert context.user_data["custom_dest_42"] == "-1002223334445"
        mock_val.assert_called_once_with(context.bot, "@republish_target")

    @pytest.mark.asyncio
    async def test_saved_quiz_settings_channel_dest_persists_canonical_chat_id(self):
        """Editing channel destination in saved quiz settings persists canonical numeric chat_id."""
        update = MagicMock()
        update.message = AsyncMock()
        update.message.text = "@saved_target_group"
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {
            "waiting_for_channel_text": True,
            "saved_quiz_set_id": 99,
            "quiz_settings": QuizSettings(),
        }

        with patch("app.bot.handlers.myquizzes.telegram_service.validate_channel_access", new_callable=AsyncMock) as mock_val, \
             patch("app.bot.handlers.myquizzes.update_quiz_set_settings", MagicMock()), \
             patch("app.bot.handlers.myquizzes.SessionLocal"):
            mock_val.return_value = (True, "Access confirmed", -1003334445556)
            result = await receive_republish_dest_message(update, context)

        assert result is True
        saved_settings: QuizSettings = context.user_data["quiz_settings"]
        assert saved_settings.channel_id == "-1003334445556"

    def test_default_services_share_chat_pacer_instance(self):
        """Default service instances across modules share default_chat_pacer, preserving dependency injection."""
        from app.bot.handlers.myquizzes import publishing_service as ps_myquizzes
        from app.bot.handlers.publish import publishing_service as ps_publish

        s1 = PublishingService()
        s2 = PublishingService()
        ts = TelegramService()

        # Shared default instances
        assert s1.pacer is default_chat_pacer
        assert s2.pacer is default_chat_pacer
        assert s1.telegram_service.pacer is default_chat_pacer
        assert ts.pacer is default_chat_pacer
        assert ps_publish.pacer is default_chat_pacer
        assert ps_myquizzes.pacer is default_chat_pacer

        # Injected custom pacer is preserved
        custom_pacer = ChatPacer(pacing_interval=9.9)
        s_custom = PublishingService(chat_pacer=custom_pacer)
        assert s_custom.pacer is custom_pacer
        assert s_custom.pacer is not default_chat_pacer
        assert s_custom.telegram_service.pacer is custom_pacer

    def test_cross_handler_shared_pacer_enforces_interval(self):
        """Outgoing sends recorded on one service enforce pacing delay on another service sharing default_chat_pacer."""
        default_chat_pacer.reset_all()
        try:
            s1 = PublishingService()
            s2 = PublishingService()

            # s1 records send to chat -100555
            s1.pacer.record_send(-100555, interval=4.0)

            # s2 querying the same chat (either as int or str) reflects the constraint
            next_allowed_int = s2.pacer.get_next_send_time(-100555)
            next_allowed_str = s2.pacer.get_next_send_time("-100555")

            assert next_allowed_int == next_allowed_str
            assert next_allowed_int > default_chat_pacer.clock()
        finally:
            default_chat_pacer.reset_all()

    @pytest.mark.asyncio
    async def test_republish_duplicate_prevention_rejects_concurrent_click(self):
        """execute_republish_callback rejects second rapid click with an alert if is_publishing is True."""
        update = MagicMock()
        query = AsyncMock()
        query.data = "execute_republish_77"
        update.callback_query = query

        context = MagicMock()
        context.user_data = {"is_publishing": True}

        with patch("app.bot.handlers.myquizzes.publishing_service.publish_batch", new_callable=AsyncMock) as mock_pub:
            await execute_republish_callback(update, context)

        query.answer.assert_called_once_with("⚠️ Publishing is already in progress. Please wait.", show_alert=True)
        mock_pub.assert_not_called()

    @pytest.mark.asyncio
    async def test_republish_flag_lifecycle_success_and_exception(self, sample_questions):
        """is_publishing flag is True during execution and cleared in finally on success or exception."""
        update = MagicMock()
        query = AsyncMock()
        query.data = "execute_republish_88"
        progress_msg = AsyncMock()
        query.edit_message_text = AsyncMock(return_value=progress_msg)
        update.callback_query = query
        update.effective_user.id = 12345

        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {}

        fake_quiz_set = MagicMock()
        fake_quiz_set.questions = sample_questions

        # 1. Successful execution
        publishing_flag_observed = []

        async def fake_publish_batch(*args, **kwargs):
            publishing_flag_observed.append(context.user_data.get("is_publishing"))
            return PublishingSummary(total=1, successful=1, failed=0)

        with patch("app.bot.handlers.myquizzes.SessionLocal"), \
             patch("app.bot.handlers.myquizzes.get_quiz_set_by_id", return_value=fake_quiz_set), \
             patch("app.bot.handlers.myquizzes.reconstruct_quiz_data", return_value=([sample_questions[0]], QuizSettings())), \
             patch("app.bot.handlers.myquizzes.create_publish_job", return_value=MagicMock(id=1)), \
             patch("app.bot.handlers.myquizzes.complete_publish_job", MagicMock()), \
             patch("app.bot.handlers.myquizzes.publishing_service.publish_batch", side_effect=fake_publish_batch):
            await execute_republish_callback(update, context)

        assert publishing_flag_observed == [True]
        # Flag cleared after success
        assert context.user_data.get("is_publishing") is None

        # 2. Exception execution
        context.user_data = {}

        async def failing_publish_batch(*args, **kwargs):
            publishing_flag_observed.append(context.user_data.get("is_publishing"))
            raise RuntimeError("Simulated network crash during republish")

        with patch("app.bot.handlers.myquizzes.SessionLocal"), \
             patch("app.bot.handlers.myquizzes.get_quiz_set_by_id", return_value=fake_quiz_set), \
             patch("app.bot.handlers.myquizzes.reconstruct_quiz_data", return_value=([sample_questions[0]], QuizSettings())), \
             patch("app.bot.handlers.myquizzes.create_publish_job", return_value=MagicMock(id=1)), \
             patch("app.bot.handlers.myquizzes.publishing_service.publish_batch", side_effect=failing_publish_batch):
            with pytest.raises(RuntimeError, match="Simulated network crash"):
                await execute_republish_callback(update, context)

        # Flag cleared even after exception
        assert context.user_data.get("is_publishing") is None

    def test_single_quiz_delivery_result_immutability_and_hash(self):
        """SingleQuizDeliveryResult is frozen dataclass: attributes cannot be mutated, hashable."""
        res = SingleQuizDeliveryResult(success=True, flood_waited=False)

        # Mutation must raise FrozenInstanceError
        with pytest.raises(FrozenInstanceError):
            res.success = False

        with pytest.raises(FrozenInstanceError):
            res.flood_waited = True

        # Hash matches bool hash to fulfill Python's equality/hash contract
        assert hash(res) == hash(True)
        res_set = {res}
        assert res in res_set
        assert SingleQuizDeliveryResult(success=True, flood_waited=False) in res_set

        # Boolean evaluation
        assert bool(res) is True
        assert bool(SingleQuizDeliveryResult(success=False, flood_waited=False)) is False

        # Equality semantics
        assert res == True
        assert res != False
        assert res == SingleQuizDeliveryResult(success=True, flood_waited=False)
        assert res != SingleQuizDeliveryResult(success=True, flood_waited=True)

    def test_single_quiz_delivery_result_hash_contract_and_dictionary_compatibility(self):
        """Comprehensive verification of Python's hash contract (a == b => hash(a) == hash(b)) and dict safety."""
        res_succ = SingleQuizDeliveryResult(success=True, flood_waited=False)
        res_fail = SingleQuizDeliveryResult(success=False, flood_waited=False)
        res_succ_flooded = SingleQuizDeliveryResult(success=True, flood_waited=True)
        res_fail_flooded = SingleQuizDeliveryResult(success=False, flood_waited=True)

        # 1. Successful result compares equal to True
        assert res_succ == True
        assert res_succ_flooded == True

        # 2. Failed result compares equal to False
        assert res_fail == False
        assert res_fail_flooded == False

        # 3. Successful result has same hash as True
        assert hash(res_succ) == hash(True)
        assert hash(res_succ_flooded) == hash(True)

        # 4. Failed result has same hash as False
        assert hash(res_fail) == hash(False)
        assert hash(res_fail_flooded) == hash(False)

        # 5. Two result objects with equal fields compare equal and have equal hashes
        res_succ_copy = SingleQuizDeliveryResult(success=True, flood_waited=False)
        assert res_succ == res_succ_copy
        assert hash(res_succ) == hash(res_succ_copy)

        res_flood_copy = SingleQuizDeliveryResult(success=True, flood_waited=True)
        assert res_succ_flooded == res_flood_copy
        assert hash(res_succ_flooded) == hash(res_flood_copy)

        # 6. Two objects with different flood_waited values remain unequal when success matches
        assert res_succ != res_succ_flooded
        assert res_fail != res_fail_flooded
        # They share the same hash (valid hash collision)
        assert hash(res_succ) == hash(res_succ_flooded)

        # 7. Objects remain immutable; modifying either field raises FrozenInstanceError
        with pytest.raises(FrozenInstanceError):
            res_succ.success = False
        with pytest.raises(FrozenInstanceError):
            res_succ.flood_waited = True
        with pytest.raises(FrozenInstanceError):
            res_fail.success = True
        with pytest.raises(FrozenInstanceError):
            res_fail.flood_waited = True

        # 8. Result objects can be used safely in sets and dictionaries
        # Sets distinguish unequal objects with identical hash
        result_set = {res_succ, res_succ_flooded, res_fail, res_fail_flooded}
        assert len(result_set) == 4
        assert res_succ in result_set
        assert res_succ_flooded in result_set
        assert res_fail in result_set
        assert res_fail_flooded in result_set

        # Dictionaries distinguish unequal objects with identical hash
        result_dict = {
            res_succ: "ok_clean",
            res_succ_flooded: "ok_flooded",
            res_fail: "fail_clean",
            res_fail_flooded: "fail_flooded",
        }
        assert len(result_dict) == 4
        assert result_dict[res_succ] == "ok_clean"
        assert result_dict[res_succ_flooded] == "ok_flooded"
        assert result_dict[res_fail] == "fail_clean"
        assert result_dict[res_fail_flooded] == "fail_flooded"

        # Dict lookup via boolean works symmetrically when key matches
        bool_dict = {res_succ: "success_value"}
        assert bool_dict[True] == "success_value"

        fail_dict = {res_fail: "failure_value"}
        assert fail_dict[False] == "failure_value"

