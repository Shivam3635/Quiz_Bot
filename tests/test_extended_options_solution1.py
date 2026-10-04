"""Unit tests for Solution 1: Extended Option Length Handling (Text Block + Letter Poll)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.parser.models import QuizQuestion, QuizSettings
from app.parser.validator import QuizValidator
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

    assert result is True
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

    assert result is True

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
