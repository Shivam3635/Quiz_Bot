"""Tests for Telegram 100-character option length handling and bilingual compression."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.config.settings import TELEGRAM_OPTION_MAX_LENGTH
from app.parser.models import QuizQuestion, QuizSettings
from app.parser.validator import (
    QuizValidator,
    OptionLengthResult,
    validate_option_length,
)
from app.services.ai_service import (
    OptionShortenerService,
    compress_bilingual_option,
    verify_factual_preservation,
    extract_numbers_and_years,
)
from app.services.telegram_service import TelegramService
from app.services.publishing_service import PublishingService


# --- 1. Basic & Boundary Validation Tests ---


def test_valid_option_simple():
    """Verify short valid option like 'India'."""
    res = validate_option_length("India")
    assert res.valid is True
    assert res.length == 5
    assert res.limit == 100
    assert res.excess == 0
    assert res.to_dict() == {
        "valid": True,
        "length": 5,
        "limit": 100,
        "excess": 0,
    }


def test_exactly_100_characters():
    """Verify exactly 100-character option is VALID."""
    text_100 = "A" * 100
    res = validate_option_length(text_100)
    assert res.valid is True
    assert res.length == 100
    assert res.excess == 0


def test_101_characters():
    """Verify 101-character option is INVALID with excess=1."""
    text_101 = "B" * 101
    res = validate_option_length(text_101)
    assert res.valid is False
    assert res.length == 101
    assert res.excess == 1


def test_empty_option():
    """Verify empty or whitespace-only option is invalid."""
    res = validate_option_length("   ")
    assert res.valid is False
    assert res.length == 0


# --- 2. Unicode, Hindi, & Bilingual Options ---


def test_realistic_hindi_option():
    """Verify realistic Hindi options are measured by character count."""
    hindi_text = "भारत का संविधान 26 जनवरी 1950 को लागू हुआ"
    res = validate_option_length(hindi_text)
    assert res.valid is True
    assert res.length == len(hindi_text)
    assert res.length <= 100


def test_bilingual_option():
    """Verify bilingual option under 100 characters is VALID."""
    bilingual = "Slithering down from a helicopter (हेलीकॉप्टर से नीचे उतरना)"
    res = validate_option_length(bilingual)
    assert res.valid is True
    assert res.length == len(bilingual)
    assert res.excess == 0


def test_unicode_and_emojis_character_count():
    """Verify Unicode, Hindi matras, symbols, and emojis are accurately counted."""
    text = "🎯 Option A: Rocket launch (रॉकेट प्रक्षेपण) 🚀"
    res = validate_option_length(text)
    assert res.valid is True
    assert res.length == len(text)


# --- 3. Semantic Bilingual Compression ---


def test_long_bilingual_option_compression():
    """Verify long bilingual option (>100 chars) is compressed <= 100 without hard truncation."""
    # Construct an oversized bilingual option: 105 characters
    long_original = "Slithering down from a helicopter using a heavy rope (हेलीकॉप्टर से भारी रस्सी के माध्यम से नीचे उतरना)"
    assert len(long_original) > 100

    compressed = compress_bilingual_option(long_original)
    assert compressed is not None
    assert len(compressed) <= 100
    # "के माध्यम से" should be replaced by "द्वारा"
    assert "द्वारा" in compressed
    # No trailing ellipses or cutoffs
    assert not compressed.endswith("...")
    assert not compressed.endswith("…")


def test_never_hard_truncate():
    """Verify that arbitrary long non-bilingual string is NOT hard-truncated with [:100]."""
    uncompressible = "X" * 120
    compressed = compress_bilingual_option(uncompressible)
    # Must return None rather than cutting off the string
    assert compressed is None


def test_factual_preservation_numbers_dates():
    """Verify compression never mutates numerical values, years, or dates."""
    orig_numbers = extract_numbers_and_years("256 MB RAM and 512 GB SSD in 2024")
    assert orig_numbers == ["256", "512", "2024"]

    # Changing 256 to 128 must be rejected by verification
    assert verify_factual_preservation(
        "256 MB RAM and 512 GB SSD in 2024",
        "128 MB RAM and 512 GB SSD in 2024",
    ) is False

    # Keeping all numbers is verified
    assert verify_factual_preservation(
        "256 MB RAM and 512 GB SSD in 2024",
        "256 MB RAM, 512 GB SSD (2024)",
    ) is True


# --- 4. AI Shortening Service & Graceful Fallbacks ---


@pytest.mark.asyncio
async def test_ai_shortener_service_mocked_success():
    """Verify AI shortening returns validated result when AI succeeds."""
    mock_settings = MagicMock()
    mock_settings.AI_SHORTENER_ENABLED = True
    mock_settings.GEMINI_API_KEY = "mock_key"
    mock_settings.OPENAI_API_KEY = None

    service = OptionShortenerService(settings=mock_settings)

    long_opt = "Extremely detailed full description of photosynthesis (पौधों द्वारा प्रकाश संश्लेषण की विस्तृत प्रक्रिया)"
    assert len(long_opt) > 100

    shortened_mock = "Photosynthesis in plants (पौधों में प्रकाश संश्लेषण)"
    assert len(shortened_mock) <= 100

    with patch.object(service, "_call_gemini", AsyncMock(return_value=shortened_mock)):
        res = await service.shorten_option(long_opt, question_idx=1, option_letter="A")
        assert res.success is True
        assert res.shortened_text == shortened_mock
        assert res.shortened_length <= 100
        assert res.method == "gemini"


@pytest.mark.asyncio
async def test_ai_shortener_api_failure_falls_back_to_heuristic():
    """Verify that when AI call raises an exception, service falls back to heuristic compression."""
    mock_settings = MagicMock()
    mock_settings.AI_SHORTENER_ENABLED = True
    mock_settings.GEMINI_API_KEY = "mock_key"
    mock_settings.OPENAI_API_KEY = None

    service = OptionShortenerService(settings=mock_settings)

    long_opt = "Slithering down from a rescue helicopter using a heavy rope (हेलीकॉप्टर से रस्सी के माध्यम से नीचे उतरना)"
    assert len(long_opt) > 100

    with patch.object(service, "_call_gemini", AsyncMock(side_effect=Exception("API quota exceeded"))):
        res = await service.shorten_option(long_opt, question_idx=1, option_letter="B")
        assert res.success is True
        assert res.method == "heuristic"
        assert res.shortened_length <= 100
        assert "द्वारा" in res.shortened_text


@pytest.mark.asyncio
async def test_ai_returns_over_100_characters_is_rejected():
    """Verify AI output > 100 characters is rejected and not trusted blindly."""
    mock_settings = MagicMock()
    mock_settings.AI_SHORTENER_ENABLED = True
    mock_settings.GEMINI_API_KEY = "mock_key"
    mock_settings.OPENAI_API_KEY = None

    service = OptionShortenerService(settings=mock_settings)

    # 115 chars
    long_opt = "Z" * 115

    # AI returns 105 chars (still over limit)
    ai_too_long = "Y" * 105

    with patch.object(service, "_call_gemini", AsyncMock(return_value=ai_too_long)):
        res = await service.shorten_option(long_opt, question_idx=2, option_letter="C")
        assert res.success is False
        assert res.method == "failed"
        assert "Unable to automatically shorten" in res.error_message


# --- 5. Batch & Bulk Quiz Tests ---


@pytest.mark.asyncio
async def test_bulk_quiz_multiple_oversized_options():
    """Test entire quiz containing multiple oversized options across different questions."""
    validator = QuizValidator()
    service = OptionShortenerService()

    q1 = QuizQuestion(
        question="What is the capital of India?",
        options=["New Delhi", "Mumbai", "Kolkata", "Chennai"],
        correct_option=0,
    )
    # Q2 Option B is 105 characters
    q2_opt_b = "Slithering down from a rescue helicopter using a heavy rope (हेलीकॉप्टर से रस्सी के माध्यम से नीचे उतरना)"
    q2 = QuizQuestion(
        question="Explain helicopter slithering.",
        options=["Fast rope", q2_opt_b, "Parachute", "Ladder"],
        correct_option=1,
    )
    # Q3 Option A is 108 characters
    q3_opt_a = "Slithering down from a helicopter using a heavy long rope (हेलीकॉप्टर से भारी रस्सी के माध्यम से नीचे उतरना)"
    q3 = QuizQuestion(
        question="Tactical insertion method?",
        options=[q3_opt_a, "Boat", "Vehicle", "Foot"],
        correct_option=0,
    )

    questions = [q1, q2, q3]

    # Validate batch
    val_res = validator.validate_batch(questions)
    assert val_res.is_valid is False
    assert val_res.has_oversized_options is True
    assert len(val_res.oversized_option_errors) == 2

    # Auto-shorten all oversized options
    batch_res = await service.shorten_all_oversized_options(questions)
    assert batch_res.oversized_count == 2
    assert batch_res.successful_count == 2
    assert batch_res.failed_count == 0

    # Apply changes
    for item in batch_res.results:
        if item.success:
            q_idx = item.question_index - 1
            questions[q_idx].options[item.option_index] = item.shortened_text

    # Re-validate: all questions and options must now pass!
    val_res_after = validator.validate_batch(questions)
    assert val_res_after.is_valid is True
    assert val_res_after.has_oversized_options is False


# --- 6. Pre-Send & Atomic Validation Tests ---


@pytest.mark.asyncio
async def test_pre_send_validation_layer2_blocks_oversized_option():
    """Verify Layer 2 pre-send check raises ValueError and blocks send_poll if an option is > 100."""
    telegram_service = TelegramService()
    mock_bot = MagicMock()
    mock_bot.send_poll = AsyncMock()

    oversized_opt = "A" * 105
    q = QuizQuestion(
        question="Valid question?",
        options=["Valid Opt 1", oversized_opt, "Valid Opt 3"],
        correct_option=0,
    )
    settings = QuizSettings()

    with pytest.raises(ValueError) as exc_info:
        await telegram_service.publish_single_quiz(
            bot=mock_bot,
            chat_id="123456",
            question=q,
            settings=settings,
        )

    assert "Pre-send validation failed" in str(exc_info.value)
    assert "105 characters" in str(exc_info.value)
    # Crucial: verify bot.send_poll was NEVER called
    mock_bot.send_poll.assert_not_called()


@pytest.mark.asyncio
async def test_atomic_pre_publish_validation_blocks_batch_before_q1():
    """Verify Layer 1 atomic pre-publish check blocks publishing before Question 1 is sent."""
    publishing_service = PublishingService()
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_poll = AsyncMock()

    q1 = QuizQuestion(
        question="Question 1?",
        options=["Opt 1", "Opt 2", "Opt 3"],
        correct_option=0,
    )
    # Question 2 has an oversized option
    q2 = QuizQuestion(
        question="Question 2?",
        options=["Opt A", "X" * 105, "Opt C"],
        correct_option=0,
    )

    with pytest.raises(ValueError) as exc_info:
        await publishing_service.publish_batch(
            bot=mock_bot,
            target_chat_id="123456",
            questions=[q1, q2],
            settings=QuizSettings(),
        )

    assert "Atomic validation failed before publishing" in str(exc_info.value)
    # Crucial: Question 1 was NEVER published
    mock_bot.send_poll.assert_not_called()
