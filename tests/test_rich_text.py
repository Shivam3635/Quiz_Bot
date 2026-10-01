"""Unit tests for Priority 3: Rich text formatting and Telegram HTML parse mode."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Bot
from telegram.constants import ParseMode, PollType
from telegram.error import TelegramError

from app.parser.models import QuizQuestion, QuizSettings
from app.services.telegram_service import TelegramService
from app.utils.helpers import (
    convert_markdown_to_telegram_html,
    format_rich_text_for_telegram,
    sanitize_telegram_html,
    strip_html_tags,
    truncate_rich_text,
)


def test_markdown_to_telegram_html():
    """Verify conversion of common Markdown syntax into Telegram HTML tags."""
    text = "What is **H2O**? Is it `water` or *gas*? See __formula__ and ~~salt~~ or ||secret||."
    html_out = convert_markdown_to_telegram_html(text)
    assert "<b>H2O</b>" in html_out
    assert "<code>water</code>" in html_out
    assert "<i>gas</i>" in html_out
    assert "<u>formula</u>" in html_out
    assert "<s>salt</s>" in html_out
    assert "<tg-spoiler>secret</tg-spoiler>" in html_out


def test_sanitize_telegram_html_allowed_and_stripped_tags():
    """Verify allowed tags are preserved and disallowed tags are stripped while keeping content."""
    text = "<p><b>Bold</b> and <i>Italic</i> and <div><span>content inside div</span></div></p>"
    sanitized = sanitize_telegram_html(text)
    assert "<b>Bold</b>" in sanitized
    assert "<i>Italic</i>" in sanitized
    assert "content inside div" in sanitized
    assert "<p>" not in sanitized
    assert "<div>" not in sanitized
    assert "<span>" not in sanitized


def test_sanitize_telegram_html_escapes_math_and_ampersand():
    """Verify rogue '<', '>', '&' are safely escaped to prevent Telegram parse errors."""
    text = "Find x where x < 5 & y > 10 in <b>equation</b>."
    sanitized = sanitize_telegram_html(text)
    assert "x &lt; 5 &amp; y &gt; 10" in sanitized
    assert "<b>equation</b>" in sanitized


def test_sanitize_telegram_html_balances_unclosed_tags():
    """Verify unclosed HTML tags are automatically balanced so Telegram parser doesn't crash."""
    text = "<b>Unclosed bold text with <code>code snippet"
    sanitized = sanitize_telegram_html(text)
    assert sanitized.endswith("</code></b>") or sanitized.endswith("</b></code>")


def test_truncate_rich_text_safe_closing():
    """Verify truncation closes open tags properly without leaving dangling tags."""
    long_text = "<b>" + "A" * 150 + "</b> and <i>" + "B" * 150 + "</i>"
    truncated = truncate_rich_text(long_text, max_chars=100)
    assert len(strip_html_tags(truncated)) <= 100
    assert truncated.endswith("</b>") or truncated.endswith("</i>") or truncated.endswith("...")
    # Verify open and close counts match for tags present
    assert truncated.count("<b>") == truncated.count("</b>")


def test_strip_html_tags():
    """Verify all tags are removed and HTML entities unescaped."""
    text = "<b>Q1.</b> What is <code>H&lt;2&gt;O</code> &amp; CO2?"
    plain = strip_html_tags(text)
    assert plain == "Q1. What is H<2>O & CO2?"


def test_format_rich_text_for_telegram_pipeline():
    """Verify full rich text formatting pipeline end-to-end."""
    raw = "What is the result of **2 < 5** in `Python` & *C++*?"
    formatted = format_rich_text_for_telegram(raw, max_plain_length=300)
    assert "<b>2 &lt; 5</b>" in formatted
    assert "<code>Python</code>" in formatted
    assert "<i>C++</i>" in formatted
    assert "&amp;" in formatted


@pytest.mark.asyncio
async def test_telegram_service_sends_html_parse_mode():
    """Verify send_poll is called with ParseMode.HTML for both question and explanation."""
    tg_service = TelegramService()
    mock_bot = MagicMock(spec=Bot)
    mock_bot.send_poll = AsyncMock()

    question = QuizQuestion(
        question="What is **photosynthesis**?",
        options=["Plant food", "Animal food", "Both"],
        correct_option=0,
        explanation="Occurs in `chloroplasts` & requires *light*.",
        question_number=1,
    )
    settings = QuizSettings(
        explanation_enabled=True,
        time_limit=30,
        is_anonymous=True,
    )

    result = await tg_service.publish_single_quiz(
        bot=mock_bot,
        chat_id=123456,
        question=question,
        settings=settings,
    )

    assert result is True
    assert mock_bot.send_poll.call_count == 1
    call_kwargs = mock_bot.send_poll.call_args.kwargs

    assert call_kwargs["question_parse_mode"] == ParseMode.HTML
    assert call_kwargs["explanation_parse_mode"] == ParseMode.HTML
    assert "<b>photosynthesis</b>" in call_kwargs["question"]
    assert "<code>chloroplasts</code>" in call_kwargs["explanation"]
    assert "<i>light</i>" in call_kwargs["explanation"]


@pytest.mark.asyncio
async def test_telegram_service_parse_error_fallback():
    """Verify fallback to plain text if Telegram API raises an entity parsing error."""
    tg_service = TelegramService()
    mock_bot = MagicMock(spec=Bot)

    # First call raises BadRequest regarding entity parsing, second call succeeds
    mock_bot.send_poll = AsyncMock(
        side_effect=[
            TelegramError("Can't parse entities: can't find end tag for <b>"),
            True,
        ]
    )

    question = QuizQuestion(
        question="Q1. What is **test**?",
        options=["Option 1", "Option 2"],
        correct_option=0,
        explanation="`Explanation` note",
        question_number=1,
    )
    settings = QuizSettings(explanation_enabled=True)

    result = await tg_service.publish_single_quiz(
        bot=mock_bot,
        chat_id=123456,
        question=question,
        settings=settings,
    )

    assert result is True
    # Verify send_poll was called twice (once with HTML, then retry with plain text)
    assert mock_bot.send_poll.call_count == 2

    # Second call must have question_parse_mode=None and clean plain text
    fallback_kwargs = mock_bot.send_poll.call_args_list[1].kwargs
    assert fallback_kwargs["question_parse_mode"] is None
    assert fallback_kwargs["explanation_parse_mode"] is None
    assert "<b>" not in fallback_kwargs["question"]
    assert "<code>" not in fallback_kwargs["explanation"]
    assert "test" in fallback_kwargs["question"]
