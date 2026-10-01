"""Unit tests for publishing and quiz services with Telegram API mocking."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Bot
from telegram.error import TelegramError

from app.parser.models import QuizQuestion, QuizSettings
from app.parser.parser import BulkQuizParser
from app.services.publishing_service import PublishingService, render_progress_bar
from app.services.quiz_service import QuizService
from app.services.telegram_service import TelegramService


@pytest.fixture
def sample_questions() -> list[QuizQuestion]:
    return [
        QuizQuestion(
            question="What is 2 + 2?",
            options=["3", "4", "5"],
            correct_option=1,
            explanation="2 + 2 equals 4.",
        ),
        QuizQuestion(
            question="What is the capital of Japan?",
            options=["Tokyo", "Kyoto", "Osaka"],
            correct_option=0,
            explanation=None,
        ),
    ]


@pytest.fixture
def default_settings() -> QuizSettings:
    return QuizSettings(
        is_anonymous=True,
        explanation_enabled=True,
        time_limit=30,
        channel_id=None,
    )


def test_render_progress_bar():
    """Verify visual progress bar rendering."""
    assert render_progress_bar(0, 10, length=10) == "░░░░░░░░░░"
    assert render_progress_bar(5, 10, length=10) == "█████░░░░░"
    assert render_progress_bar(10, 10, length=10) == "██████████"


@pytest.mark.asyncio
async def test_publishing_service_success(sample_questions, default_settings):
    """Test successful batch publishing with all polls sent."""
    mock_telegram = MagicMock(spec=TelegramService)
    mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

    pub_service = PublishingService(telegram_service=mock_telegram)
    pub_service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.001  # instant for test

    mock_bot = MagicMock(spec=Bot)
    callback_calls = []

    async def progress_cb(completed, total, text):
        callback_calls.append((completed, total))

    summary = await pub_service.publish_batch(
        bot=mock_bot,
        target_chat_id=12345,
        questions=sample_questions,
        settings=default_settings,
        progress_callback=progress_cb,
    )

    assert summary.total == 2
    assert summary.successful == 2
    assert summary.failed == 0
    assert summary.is_complete_success
    assert mock_telegram.publish_single_quiz.await_count == 2
    assert len(callback_calls) == 2


@pytest.mark.asyncio
async def test_publishing_service_partial_failure(sample_questions, default_settings):
    """Test handling of Telegram API errors during batch publishing."""
    mock_telegram = MagicMock(spec=TelegramService)
    # Question 1 succeeds, Question 2 fails
    mock_telegram.publish_single_quiz = AsyncMock(
        side_effect=[True, TelegramError("Chat not found")]
    )

    pub_service = PublishingService(telegram_service=mock_telegram)
    pub_service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.001

    mock_bot = MagicMock(spec=Bot)
    summary = await pub_service.publish_batch(
        bot=mock_bot,
        target_chat_id="@invalid_chat",
        questions=sample_questions,
        settings=default_settings,
    )

    assert summary.total == 2
    assert summary.successful == 1
    assert summary.failed == 1
    assert not summary.is_complete_success
    assert len(summary.failed_items) == 1
    assert summary.failed_items[0].index == 2
    assert "Chat not found" in summary.failed_items[0].error_message


def test_quiz_service_valid_flow():
    """Test QuizService parsing and validating end-to-end valid text."""
    service = QuizService()
    raw = """
Q1. What is the color of the sky?
A) Blue
B) Green
C) Red
Answer: A
"""
    result = service.process_raw_text(raw)
    assert result.is_ready_for_publish
    assert len(result.questions) == 1
    assert result.questions[0].correct_letter == "A"


def test_quiz_service_invalid_flow():
    """Test QuizService with malformed input."""
    service = QuizService()
    raw = """
Q1. Question without options
Answer: A
"""
    result = service.process_raw_text(raw)
    assert not result.is_ready_for_publish
    assert len(result.questions) == 0
    assert result.raw_batch.has_errors


def test_bilingual_inline_batch_end_to_end():
    """Test the full user-provided bilingual exam batch end-to-end."""
    service = QuizService()
    raw = """
Q1. How many degrees are there in a compass? कम्पास में कुल कितने डिग्री होते हैं?
A. 360 (360) ✅
B. 270 (270)
C. 180 (180)
D. 90 (90)

Q2. Which direction is indicated by the top of a map? मानचित्र के ऊपरी भाग द्वारा कौन-सी दिशा दर्शाई जाती है?
A. North (उत्तर) ✅
B. West (पश्चिम)
C. South (दक्षिण)
D. East (पूर्व)

Q3. What is used to find North at night? रात में उत्तर दिशा ज्ञात करने के लिए किसका उपयोग किया जाता है?
A. Tree (वृक्ष)
B. Pole Star (ध्रुव तारा) ✅
C. Sun (सूर्य)
D. Moon (चंद्रमा)

Q4. Which type of map provides detailed information about natural features like mountains, rivers, and forests? पर्वतों, नदियों और वनों जैसी प्राकृतिक विशेषताओं की विस्तृत जानकारी कौन-सा मानचित्र देता है?
A. Topographic Map (स्थलाकृतिक मानचित्र) ✅
B. Political Map (राजनीतिक मानचित्र)
C. Road Map (सड़क मानचित्र)
D. Weather Map (मौसम मानचित्र)

Q5. Whose orders will the Section Commander wait for after reorganization in Section Battle Drill? सेक्शन बैटल ड्रिल में पुनर्गठन के बाद सेक्शन कमांडर किसके आदेश की प्रतीक्षा करेगा?
A. Commanding Officer (कमांडिंग ऑफिसर)
B. Company Commander (कंपनी कमांडर)
C. Platoon Commander (प्लाटून कमांडर) ✅
D. None of the above (उपरोक्त में से कोई नहीं)
"""
    result = service.process_raw_text(raw)
    assert result.is_ready_for_publish
    assert len(result.questions) == 5
    assert result.questions[0].correct_letter == "A"
    assert result.questions[1].correct_letter == "A"
    assert result.questions[2].correct_letter == "B"
    assert result.questions[3].correct_letter == "A"
    assert result.questions[4].correct_letter == "C"

    # Verify that the poll options do NOT contain the checkmark spoilers
    for q in result.questions:
        for opt in q.options:
            assert "✅" not in opt


@pytest.mark.asyncio
async def test_published_poll_has_question_numbers():
    """Verify that published quiz polls retain their respective question numbers (e.g. Q1, Q2)."""
    parser = BulkQuizParser()
    tg_service = TelegramService()
    settings = QuizSettings()

    raw_text = """
Q1. How many degrees are there in a compass? कम्पास में कुल कितने डिग्री होते हैं?
A. 360 (360) ✅
B. 270 (270)
Answer: A

Q2. Which direction is indicated by the top of a map?
A. North ✅
B. South
Answer: A
"""
    batch = parser.parse(raw_text)
    assert len(batch.questions) == 2

    q1 = batch.questions[0]
    q2 = batch.questions[1]

    # Verify model fields
    assert q1.question_number == 1
    assert q1.raw_prefix == "Q1."
    assert q1.display_question.startswith("Q1. How many degrees are there in a compass?")

    assert q2.question_number == 2
    assert q2.raw_prefix == "Q2."
    assert q2.display_question.startswith("Q2. Which direction is indicated by the top of a map?")

    # Verify actual Telegram API payload sent
    mock_bot = MagicMock()
    mock_bot.send_poll = AsyncMock()

    await tg_service.publish_single_quiz(mock_bot, 12345, q1, settings)
    assert mock_bot.send_poll.call_count == 1
    call_kwargs_1 = mock_bot.send_poll.call_args.kwargs
    assert call_kwargs_1["question"].startswith("Q1. How many degrees are there in a compass?")
    assert "\nकम्पास में कुल कितने डिग्री होते हैं?" in call_kwargs_1["question"]

    await tg_service.publish_single_quiz(mock_bot, 12345, q2, settings)
    assert mock_bot.send_poll.call_count == 2
    call_kwargs_2 = mock_bot.send_poll.call_args.kwargs
    assert call_kwargs_2["question"] == "Q2. Which direction is indicated by the top of a map?"


def test_format_header_banner():
    """Verify header banner formatting with and without custom title and description."""
    settings = QuizSettings(
        title="Modern Indian History #04",
        description="Covers Freedom Struggle 1857-1947.",
        header_banner_enabled=True,
        time_limit=30,
        explanation_enabled=True,
        is_anonymous=True,
    )
    banner = PublishingService.format_header_banner(25, settings)
    assert "Modern Indian History #04" in banner
    assert "Freedom Struggle 1857-1947" in banner
    assert "25" in banner
    assert "30s" in banner
    assert "Enabled" in banner
    assert "Anonymous" in banner

    # Without title and without description
    settings_no_title = QuizSettings(
        title=None,
        description=None,
        header_banner_enabled=True,
        time_limit=None,
        explanation_enabled=False,
        is_anonymous=False,
    )
    banner_no_title = PublishingService.format_header_banner(10, settings_no_title)
    assert "Quiz Session Starting!" in banner_no_title
    assert "10" in banner_no_title
    assert "None" in banner_no_title
    assert "Disabled" in banner_no_title
    assert "Public" in banner_no_title


@pytest.mark.asyncio
async def test_publishing_service_header_banner(sample_questions):
    """Verify header banner is sent when enabled and omitted when disabled."""
    mock_telegram = MagicMock(spec=TelegramService)
    mock_telegram.publish_single_quiz = AsyncMock(return_value=True)

    pub_service = PublishingService(telegram_service=mock_telegram)
    pub_service.settings.DEFAULT_DELAY_BETWEEN_POSTS = 0.001

    mock_bot = MagicMock(spec=Bot)
    mock_bot.send_message = AsyncMock()

    # Enabled case
    settings_enabled = QuizSettings(
        title="Mock Test #5",
        header_banner_enabled=True,
    )
    await pub_service.publish_batch(
        bot=mock_bot,
        target_chat_id="@test_channel",
        questions=sample_questions,
        settings=settings_enabled,
    )
    assert mock_bot.send_message.call_count == 1
    call_args = mock_bot.send_message.call_args.kwargs
    assert call_args["chat_id"] == "@test_channel"
    assert "Mock Test #5" in call_args["text"]

    # Disabled case
    mock_bot.send_message.reset_mock()
    settings_disabled = QuizSettings(
        title="Mock Test #5",
        header_banner_enabled=False,
    )
    await pub_service.publish_batch(
        bot=mock_bot,
        target_chat_id="@test_channel",
        questions=sample_questions,
        settings=settings_disabled,
    )
    assert mock_bot.send_message.call_count == 0


