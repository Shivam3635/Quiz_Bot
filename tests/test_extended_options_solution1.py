"""Unit tests for Solution 1: Extended Option Length Handling (Text Block + Letter Poll)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError, TimedOut

from app.parser.models import QuizQuestion, QuizSettings
from app.parser.validator import QuizValidator
from app.services.publishing_service import PublishingService
from app.services.telegram_service import (
    TelegramService,
    format_extended_question_message,
    is_extended_option_question,
)


def test_is_extended_option_question_detection():
    """Verify detection of normal vs extended option questions."""
    normal_q = QuizQuestion(
        question="What is Python?",
        options=["A programming language", "A snake", "Both", "None"],
        correct_option=2,
    )
    assert not is_extended_option_question(normal_q)

    long_opt = "This is a bilingual explanation option in English and Hindi that exceeds 100 characters. " * 2
    assert len(long_opt) > 100

    extended_q = QuizQuestion(
        question="What is photosynthesis?",
        options=["Simple option", long_opt, "Another simple option", "Short"],
        correct_option=1,
    )
    assert is_extended_option_question(extended_q)


def test_format_extended_question_message():
    """Verify that extended question message contains full question and options with letter tags."""
    long_opt_a = "English description of option A. हिन्दी में विकल्प A का विस्तृत विवरण जो सौ से अधिक अक्षरों का है।"
    long_opt_b = "English description of option B. हिन्दी में विकल्प B का विस्तृत विवरण जो सौ से अधिक अक्षरों का है।"
    q = QuizQuestion(
        question="Explain the process of cellular respiration in biology.",
        options=[long_opt_a, long_opt_b, "Option C short", "Option D short"],
        correct_option=0,
    )

    msg = format_extended_question_message(q)
    assert "Explain the process of cellular respiration" in msg
    assert "<b>[A]</b>" in msg
    assert "<b>[B]</b>" in msg
    assert "<b>[C]</b>" in msg
    assert "<b>[D]</b>" in msg
    assert long_opt_a in msg
    assert long_opt_b in msg


def test_validator_with_extended_options():
    """Verify QuizValidator behavior with allow_extended_options flag."""
    long_opt = "A" * 150
    q = QuizQuestion(
        question="Test Question?",
        options=[long_opt, "Option B", "Option C", "Option D"],
        correct_option=0,
    )

    # 1. Default (allow_extended_options=False): should flag OPTION_TOO_LONG
    strict_validator = QuizValidator(allow_extended_options=False)
    strict_errors = strict_validator.validate_single(q)
    assert any(e.rule == "OPTION_TOO_LONG" for e in strict_errors)

    # 2. allow_extended_options=True: should pass for options <= 1000 chars
    ext_validator = QuizValidator(allow_extended_options=True)
    ext_errors = ext_validator.validate_single(q)
    assert not any(e.rule == "OPTION_TOO_LONG" for e in ext_errors)

    # 3. Exceeding 1000 chars should still fail even with allow_extended_options=True
    too_long_opt = "A" * 1050
    huge_q = QuizQuestion(
        question="Huge Question?",
        options=[too_long_opt, "Option B", "Option C", "Option D"],
        correct_option=0,
    )
    huge_errors = ext_validator.validate_single(huge_q)
    assert any(e.rule == "OPTION_TOO_LONG" for e in huge_errors)


@pytest.mark.asyncio
async def test_publish_normal_question_untouched():
    """Verify normal question (all options <= 100 chars) is UNTOUCHED: no message sent, direct poll sent."""
    service = TelegramService()
    bot = AsyncMock()

    normal_q = QuizQuestion(
        question="Standard question with short options?",
        options=["Option 1", "Option 2", "Option 3", "Option 4"],
        correct_option=1,
    )
    settings = QuizSettings(time_limit=30, is_anonymous=True)

    result = await service.publish_single_quiz(
        bot=bot,
        chat_id="@testchannel",
        question=normal_q,
        settings=settings,
    )

    assert result.success is True
    # Crucial: bot.send_message must NOT be called for normal questions!
    bot.send_message.assert_not_called()

    # bot.send_poll must be called with the original options
    bot.send_poll.assert_called_once()
    poll_kwargs = bot.send_poll.call_args.kwargs
    assert poll_kwargs["options"] == ["Option 1", "Option 2", "Option 3", "Option 4"]
    assert poll_kwargs["correct_option_id"] == 1
    assert poll_kwargs["chat_id"] == "@testchannel"


@pytest.mark.asyncio
async def test_publish_extended_question_solution1():
    """Verify question with oversized option triggers Solution 1: text message first, then letter poll."""
    service = TelegramService()
    bot = AsyncMock()

    long_opt_1 = "Detailed bilingual description 1: " + ("x" * 120)
    extended_q = QuizQuestion(
        question="What is the significance of the treaty signed in 1919?",
        options=[long_opt_1, "Short Option 2", "Short Option 3", "Short Option 4"],
        correct_option=0,
    )
    settings = QuizSettings(time_limit=45, is_anonymous=False)

    result = await service.publish_single_quiz(
        bot=bot,
        chat_id="-100123456789",
        question=extended_q,
        settings=settings,
    )

    assert result.success is True

    # 1. bot.send_message MUST be called with full question text and uncompressed options
    bot.send_message.assert_called_once()
    msg_kwargs = bot.send_message.call_args.kwargs
    assert msg_kwargs["chat_id"] == "-100123456789"
    assert "What is the significance of the treaty" in msg_kwargs["text"]
    assert long_opt_1 in msg_kwargs["text"]
    assert "<b>[A]</b>" in msg_kwargs["text"]

    # 2. bot.send_poll MUST be called with concise letter options
    bot.send_poll.assert_called_once()
    poll_kwargs = bot.send_poll.call_args.kwargs
    assert poll_kwargs["chat_id"] == "-100123456789"
    assert poll_kwargs["options"] == ["Option A", "Option B", "Option C", "Option D"]
    assert poll_kwargs["correct_option_id"] == 0
    assert poll_kwargs["question"] == "👇 Select your answer below:"
    assert "What is the significance" not in poll_kwargs["question"]


@pytest.mark.asyncio
async def test_mixed_batch_publishing():
    """Verify a batch with both normal and extended questions treats each correctly."""
    service = TelegramService()
    bot = AsyncMock()

    normal_q = QuizQuestion(
        question="Normal Question?",
        options=["Norm A", "Norm B"],
        correct_option=0,
    )
    long_opt = "Bilingual text: " + ("y" * 110)
    extended_q = QuizQuestion(
        question="Extended Question?",
        options=["Ext A", long_opt],
        correct_option=1,
    )
    settings = QuizSettings()

    # Publish normal question
    await service.publish_single_quiz(bot=bot, chat_id=123, question=normal_q, settings=settings)
    assert bot.send_message.call_count == 0
    assert bot.send_poll.call_count == 1
    assert bot.send_poll.call_args.kwargs["options"] == ["Norm A", "Norm B"]

    bot.reset_mock()

    # Publish extended question
    await service.publish_single_quiz(bot=bot, chat_id=123, question=extended_q, settings=settings)
    assert bot.send_message.call_count == 1
    assert bot.send_poll.call_count == 1
    assert bot.send_poll.call_args.kwargs["options"] == ["Option A", "Option B"]


# ============================================================================
# ENG-501: Partial Failure & Orphaned Text Block Cleanup Tests
# ============================================================================


@pytest.fixture
def extended_question_fixture() -> QuizQuestion:
    return QuizQuestion(
        question="What is the significance of the 1919 treaty?",
        options=["Detailed bilingual option that is longer than 100 characters " * 3, "Short option B"],
        correct_option=0,
    )


@pytest.mark.asyncio
async def test_extended_option_text_and_poll_both_succeed(extended_question_fixture):
    """When both text message and poll succeed, no deletion is attempted."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 999
    bot.send_message.return_value = mock_msg
    bot.send_poll.return_value = MagicMock()

    res = await service.publish_single_quiz(
        bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
    )
    assert res.success is True
    bot.send_message.assert_called_once()
    bot.send_poll.assert_called_once()
    bot.delete_message.assert_not_called()


@pytest.mark.asyncio
async def test_extended_option_text_fails(extended_question_fixture):
    """When initial text message fails, poll and deletion are never attempted."""
    service = TelegramService()
    bot = AsyncMock()
    bot.send_message.side_effect = BadRequest("Chat not found")

    with pytest.raises(BadRequest, match="Chat not found"):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )
    bot.send_message.assert_called_once()
    bot.send_poll.assert_not_called()
    bot.delete_message.assert_not_called()


@pytest.mark.asyncio
async def test_extended_option_text_succeeds_poll_bad_request_triggers_cleanup(extended_question_fixture):
    """When poll fails with BadRequest, orphaned text message is deleted and original error is preserved."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 101
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = BadRequest("Poll options must be non-empty")

    with pytest.raises(BadRequest, match="Poll options must be non-empty"):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )
    bot.send_message.assert_called_once()
    bot.send_poll.assert_called_once()
    bot.delete_message.assert_called_once_with(chat_id="@channel", message_id=101)


@pytest.mark.asyncio
async def test_extended_option_text_succeeds_poll_forbidden_triggers_cleanup(extended_question_fixture):
    """When poll fails with Forbidden, orphaned text message is deleted and original error is preserved."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 102
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = Forbidden("Bot was kicked from channel")

    with pytest.raises(Forbidden, match="Bot was kicked from channel"):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )
    bot.delete_message.assert_called_once_with(chat_id="@channel", message_id=102)


@pytest.mark.asyncio
async def test_extended_option_text_succeeds_poll_timed_out_no_cleanup(extended_question_fixture):
    """When poll times out, text message MUST NOT be deleted because delivery outcome is uncertain."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 103
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = TimedOut("Network connection lost")

    with pytest.raises(TimedOut, match="Network connection lost"):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )
    bot.send_message.assert_called_once()
    bot.send_poll.assert_called_once()
    # CRITICAL: delete_message must NOT be called on timeout!
    bot.delete_message.assert_not_called()


@pytest.mark.asyncio
async def test_extended_option_cleanup_deletion_failure_handled_safely(extended_question_fixture):
    """When cleanup delete_message fails, deletion error is logged and original poll error is preserved."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 104
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = BadRequest("Invalid poll structure")
    bot.delete_message.side_effect = TelegramError("Message cannot be deleted (insufficient rights)")

    with pytest.raises(BadRequest, match="Invalid poll structure"):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )
    bot.delete_message.assert_called_once_with(chat_id="@channel", message_id=104)


@pytest.mark.asyncio
async def test_extended_option_fallback_poll_failure_triggers_cleanup(extended_question_fixture):
    """When rich poll fails entity parsing and fallback plain text poll also fails, cleanup occurs."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 105
    bot.send_message.return_value = mock_msg
    # First call: parse error, second call: BadRequest
    bot.send_poll.side_effect = [
        TelegramError("can't parse entities in poll title"),
        BadRequest("Poll creation rejected"),
    ]

    with pytest.raises(BadRequest, match="Poll creation rejected"):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )
    assert bot.send_poll.call_count == 2
    bot.delete_message.assert_called_once_with(chat_id="@channel", message_id=105)


@pytest.mark.asyncio
async def test_extended_option_poll_retry_after_once_succeeds(extended_question_fixture):
    """When poll receives RetryAfter once, ONLY poll is retried; text message is sent exactly once."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 201
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = [RetryAfter(0), True]

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )

    assert res.success is True
    # The text message must be sent exactly once!
    assert bot.send_message.call_count == 1
    assert bot.send_poll.call_count == 2
    bot.delete_message.assert_not_called()
    assert res.flood_waited is True


@pytest.mark.asyncio
async def test_extended_option_poll_retry_after_twice_succeeds_crucial_assertion(extended_question_fixture):
    """
    CRUCIAL ASSERTION:
    When text message succeeds and poll receives RetryAfter twice before succeeding:
    send_message.call_count == 1
    send_poll.call_count == 3
    """
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 202
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = [RetryAfter(0), RetryAfter(0), True]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        res = await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )

    assert res.success is True
    assert bot.send_message.call_count == 1
    assert bot.send_poll.call_count == 3
    bot.delete_message.assert_not_called()
    assert res.flood_waited is True


@pytest.mark.asyncio
async def test_extended_option_poll_max_retries_exhausted_deletes_text(extended_question_fixture):
    """When poll exhausts MAX_FLOOD_RETRIES, text message is deleted and TelegramError is raised."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 203
    bot.send_message.return_value = mock_msg
    # 4 consecutive RetryAfter responses (exceeds default MAX_FLOOD_RETRIES=3)
    bot.send_poll.side_effect = [RetryAfter(0), RetryAfter(0), RetryAfter(0), RetryAfter(0)]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TelegramError, match="Flood control limit reached on poll"):
            await service.publish_single_quiz(
                bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
            )

    assert bot.send_message.call_count == 1
    assert bot.send_poll.call_count == 4
    bot.delete_message.assert_called_once_with(chat_id="@channel", message_id=203)


@pytest.mark.asyncio
async def test_extended_option_poll_cancellation_propagated(extended_question_fixture):
    """asyncio.CancelledError raised during poll execution is propagated and does NOT delete text."""
    service = TelegramService()
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 204
    bot.send_message.return_value = mock_msg
    bot.send_poll.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await service.publish_single_quiz(
            bot=bot, chat_id="@channel", question=extended_question_fixture, settings=QuizSettings()
        )

    assert bot.send_message.call_count == 1
    bot.delete_message.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_unicode_surrogate_publishing_safety():
    """Verify that questions with lone surrogates produce controlled errors and never call Telegram API."""
    service = TelegramService()
    bot = AsyncMock()

    malformed_q = QuizQuestion.model_construct(
        question="What is this \ud800 character?",
        options=["Option A", "Option B"],
        correct_option=0,
    )

    # 1. Direct publish_single_quiz raises TelegramError before sending to Telegram
    with pytest.raises(TelegramError, match="contains unencodable Unicode surrogate characters"):
        await service.publish_single_quiz(
            bot=bot,
            chat_id="@channel",
            question=malformed_q,
            settings=QuizSettings(),
        )

    bot.send_message.assert_not_called()
    bot.send_poll.assert_not_called()

    # 2. Batch publishing handles it gracefully as a controlled failed question without unhandled exceptions
    pub_service = PublishingService(telegram_service=service)
    valid_q = QuizQuestion(
        question="What is 2 + 2?",
        options=["3", "4", "5", "6"],
        correct_option=1,
    )

    summary = await pub_service.publish_batch(
        bot=bot,
        target_chat_id="@channel",
        questions=[valid_q, malformed_q],
        settings=QuizSettings(header_banner_enabled=False),
    )

    assert summary.total == 2
    assert summary.successful == 1
    assert summary.failed == 1
    assert len(summary.failed_items) == 1
    assert summary.failed_items[0].is_uncertain is False
    assert "surrogate" in summary.failed_items[0].error_message
