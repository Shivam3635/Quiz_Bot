"""Unit tests for QuizValidator."""

import pytest
from app.parser.models import QuizQuestion
from app.parser.validator import QuizValidator, telegram_utf16_length
from app.services.telegram_service import is_extended_option_question


@pytest.fixture
def validator() -> QuizValidator:
    """Fixture returning QuizValidator instance."""
    return QuizValidator()


def test_valid_question(validator: QuizValidator):
    """Test standard valid question passes validation without errors."""
    q = QuizQuestion(
        question="What is the capital of France?",
        options=["London", "Berlin", "Paris", "Madrid"],
        correct_option=2,
        explanation="Paris is the capital and largest city of France.",
    )
    errors = validator.validate_single(q)
    assert len(errors) == 0

    batch_result = validator.validate_batch([q])
    assert batch_result.is_valid
    assert batch_result.valid_count == 1
    assert batch_result.error_count == 0


def test_empty_question(validator: QuizValidator):
    """Test empty question string detection."""
    # Using construct/bypass pydantic min_length to test validator specifically
    q = QuizQuestion.model_construct(
        question="",
        options=["Option A", "Option B"],
        correct_option=0,
    )
    errors = validator.validate_single(q)
    assert any(e.rule == "EMPTY_QUESTION" for e in errors)


def test_duplicate_options(validator: QuizValidator):
    """Test duplicate option detection."""
    q = QuizQuestion(
        question="Which is an even number?",
        options=["2", "3", "2", "5"],
        correct_option=0,
    )
    errors = validator.validate_single(q)
    assert len(errors) == 1
    assert errors[0].rule == "DUPLICATE_OPTION"
    assert "Duplicate option detected" in errors[0].message


def test_invalid_correct_answer(validator: QuizValidator):
    """Test out-of-bounds correct answer index."""
    q = QuizQuestion.model_construct(
        question="What is 1 + 1?",
        options=["1", "2"],
        correct_option=5,  # only 2 options
    )
    errors = validator.validate_single(q)
    assert any(e.rule == "INVALID_CORRECT_ANSWER" for e in errors)


def test_too_many_options(validator: QuizValidator):
    """Test exceeding Telegram 10-option limit."""
    options = [f"Choice {i}" for i in range(12)]
    q = QuizQuestion.model_construct(
        question="Select an option",
        options=options,
        correct_option=0,
    )
    errors = validator.validate_single(q)
    assert any(e.rule == "TOO_MANY_OPTIONS" for e in errors)


def test_question_too_long(validator: QuizValidator):
    """Test question exceeding Telegram limit (300 chars)."""
    long_question = "A" * 305
    q = QuizQuestion.model_construct(
        question=long_question,
        options=["Yes", "No"],
        correct_option=0,
    )
    errors = validator.validate_single(q)
    assert any(e.rule == "QUESTION_TOO_LONG" for e in errors)


def test_option_too_long(validator: QuizValidator):
    """Test option exceeding Telegram limit (100 chars)."""
    long_option = "X" * 105
    q = QuizQuestion(
        question="Question?",
        options=[long_option, "Normal option"],
        correct_option=1,
    )
    errors = validator.validate_single(q)
    assert any(e.rule == "OPTION_TOO_LONG" for e in errors)


def test_explanation_too_long(validator: QuizValidator):
    """Test explanation exceeding Telegram limit (200 chars)."""
    long_expl = "E" * 205
    q = QuizQuestion(
        question="Question?",
        options=["A", "B"],
        correct_option=0,
        explanation=long_expl,
    )
    errors = validator.validate_single(q)
    assert any(e.rule == "EXPLANATION_TOO_LONG" for e in errors)


def test_formatted_error_report(validator: QuizValidator):
    """Test user-friendly error formatting."""
    q1 = QuizQuestion(question="Q1", options=["A", "B"], correct_option=0)
    q2 = QuizQuestion.model_construct(question="", options=["A", "A"], correct_option=0)

    result = validator.validate_batch([q1, q2])
    assert not result.is_valid
    assert result.valid_count == 1
    assert result.error_count > 0

    report = result.get_formatted_error_report()
    assert "Question 2:" in report
    assert "Duplicate option detected" in report


def test_telegram_utf16_length():
    """Verify UTF-16 code units length calculation for various Unicode scripts."""
    # None and empty string
    assert telegram_utf16_length(None) == 0
    assert telegram_utf16_length("") == 0

    # Basic Latin ASCII (1 code point == 1 UTF-16 unit)
    assert telegram_utf16_length("Hello") == 5

    # Hindi / Devanagari (BMP characters: U+0900 to U+097F, 1 code point == 1 UTF-16 unit)
    assert telegram_utf16_length("भारत") == 4
    assert telegram_utf16_length("नमस्ते") == 6
    assert telegram_utf16_length("फ़िल्म") == 6

    # Supplementary characters / Emojis (SMP > U+FFFF, represented as surrogate pairs = 2 UTF-16 units)
    assert len("🎯") == 1
    assert telegram_utf16_length("🎯") == 2
    assert telegram_utf16_length("Hello 🎯") == 8  # 6 + 2 = 8

    # Multiple emojis
    assert telegram_utf16_length("🎯" * 50) == 100
    assert telegram_utf16_length("🎯" * 51) == 102

    # Lone surrogates (ISS-5451-01)
    assert telegram_utf16_length("\ud800") == 1
    assert telegram_utf16_length("\udfff") == 1
    assert telegram_utf16_length("Hello 🎯 \ud800 World") == 16


def test_option_exact_boundaries(validator: QuizValidator):
    """Test exact UTF-16 code unit boundaries (99, 100, 101) for ASCII options."""
    # 99 chars - passes
    q_99 = QuizQuestion(
        question="Question 99?",
        options=["A" * 99, "Option B"],
        correct_option=0,
    )
    assert not any(e.rule == "OPTION_TOO_LONG" for e in validator.validate_single(q_99))

    # 100 chars - passes (boundary)
    q_100 = QuizQuestion(
        question="Question 100?",
        options=["A" * 100, "Option B"],
        correct_option=0,
    )
    assert not any(e.rule == "OPTION_TOO_LONG" for e in validator.validate_single(q_100))

    # 101 chars - fails
    q_101 = QuizQuestion(
        question="Question 101?",
        options=["A" * 101, "Option B"],
        correct_option=0,
    )
    errors = validator.validate_single(q_101)
    assert any(e.rule == "OPTION_TOO_LONG" for e in errors)
    opt_err = next(e for e in errors if e.rule == "OPTION_TOO_LONG")
    assert "Option 1 is 101 characters (limit: 100)" in opt_err.message


def test_emoji_option_boundaries(validator: QuizValidator):
    """
    Test emoji options where Python len() != UTF-16 code units (ISS-5409).
    50 emojis: len() is 50, UTF-16 units is 100 -> MUST PASS.
    51 emojis: len() is 51, UTF-16 units is 102 -> MUST FAIL.
    """
    # 50 emojis = 100 UTF-16 units (allowed)
    opt_50_emojis = "🎯" * 50
    assert len(opt_50_emojis) == 50
    assert telegram_utf16_length(opt_50_emojis) == 100

    q_50 = QuizQuestion(
        question="Emoji question 50?",
        options=[opt_50_emojis, "Normal option"],
        correct_option=0,
    )
    errors_50 = validator.validate_single(q_50)
    assert not any(e.rule == "OPTION_TOO_LONG" for e in errors_50)

    # 51 emojis = 102 UTF-16 units (exceeds Telegram 100 UTF-16 code units limit)
    opt_51_emojis = "🎯" * 51
    assert len(opt_51_emojis) == 51
    assert telegram_utf16_length(opt_51_emojis) == 102

    q_51 = QuizQuestion(
        question="Emoji question 51?",
        options=[opt_51_emojis, "Normal option"],
        correct_option=0,
    )
    errors_51 = validator.validate_single(q_51)
    assert any(e.rule == "OPTION_TOO_LONG" for e in errors_51)
    opt_err = next(e for e in errors_51 if e.rule == "OPTION_TOO_LONG")
    assert "Option 1 is 102 characters (limit: 100)" in opt_err.message


def test_hindi_devanagari_unicode_options(validator: QuizValidator):
    """
    Test Hindi / Devanagari text options.
    Devanagari characters reside in the BMP (U+0900-U+097F), so 1 code point == 1 UTF-16 unit.
    """
    # 100 Devanagari characters -> 100 UTF-16 units -> PASS
    hindi_100 = "क" * 100
    assert telegram_utf16_length(hindi_100) == 100
    q_hindi_100 = QuizQuestion(
        question="हिन्दी प्रश्न?",
        options=[hindi_100, "विकल्प ख"],
        correct_option=0,
    )
    assert not any(e.rule == "OPTION_TOO_LONG" for e in validator.validate_single(q_hindi_100))

    # 101 Devanagari characters -> 101 UTF-16 units -> FAIL
    hindi_101 = "क" * 101
    assert telegram_utf16_length(hindi_101) == 101
    q_hindi_101 = QuizQuestion(
        question="हिन्दी प्रश्न?",
        options=[hindi_101, "विकल्प ख"],
        correct_option=0,
    )
    errors_hindi_101 = validator.validate_single(q_hindi_101)
    assert any(e.rule == "OPTION_TOO_LONG" for e in errors_hindi_101)


def test_bilingual_options_utf16(validator: QuizValidator):
    """Test bilingual English-Hindi options with mixed scripts and emojis."""
    mixed_opt_90 = ("English " * 5) + ("हिन्दी " * 2) + ("⭐" * 10)
    utf16_len_90 = telegram_utf16_length(mixed_opt_90)
    assert utf16_len_90 <= 100
    q_mixed_90 = QuizQuestion(
        question="Bilingual Question?",
        options=[mixed_opt_90, "Short option"],
        correct_option=0,
    )
    assert not any(e.rule == "OPTION_TOO_LONG" for e in validator.validate_single(q_mixed_90))

    # Construct option with exactly 101 UTF-16 units: 99 ASCII chars + 1 emoji (2 units) = 101
    mixed_opt_101 = ("A" * 99) + "🚀"
    assert len(mixed_opt_101) == 100
    assert telegram_utf16_length(mixed_opt_101) == 101

    q_mixed_101 = QuizQuestion(
        question="Bilingual Question 101?",
        options=[mixed_opt_101, "Short option"],
        correct_option=0,
    )
    errors_101 = validator.validate_single(q_mixed_101)
    assert any(e.rule == "OPTION_TOO_LONG" for e in errors_101)
    opt_err = next(e for e in errors_101 if e.rule == "OPTION_TOO_LONG")
    assert "Option 1 is 101 characters (limit: 100)" in opt_err.message


def test_extended_options_utf16_consistency():
    """Verify consistency between validator and is_extended_option_question for UTF-16 options."""
    opt_51_emojis = "🎯" * 51  # 51 code points, 102 UTF-16 units

    q = QuizQuestion(
        question="Extended emoji question?",
        options=[opt_51_emojis, "Option B"],
        correct_option=0,
    )

    # 1. is_extended_option_question must detect this as extended option (> 100 UTF-16 units)
    assert is_extended_option_question(q) is True

    # 2. Strict validator rejects it
    strict_val = QuizValidator(allow_extended_options=False)
    strict_errs = strict_val.validate_single(q)
    assert any(e.rule == "OPTION_TOO_LONG" for e in strict_errs)

    # 3. Extended validator accepts it (<= 1000 UTF-16 units)
    ext_val = QuizValidator(allow_extended_options=True)
    ext_errs = ext_val.validate_single(q)
    assert not any(e.rule == "OPTION_TOO_LONG" for e in ext_errs)


def test_malformed_unicode_surrogates(validator: QuizValidator):
    """Test detection and handling of unencodable lone surrogate code points (ISS-5451-01)."""
    # 1. Lone surrogate in question text
    q_bad_text = QuizQuestion.model_construct(
        question="Invalid surrogate \ud800 in question text?",
        options=["Option A", "Option B"],
        correct_option=0,
    )
    errors_q = validator.validate_single(q_bad_text)
    assert any(e.rule == "MALFORMED_UNICODE" for e in errors_q)
    assert any("Question text contains invalid Unicode" in e.message for e in errors_q)

    # 2. Lone surrogate in option text
    q_bad_opt = QuizQuestion.model_construct(
        question="Normal question?",
        options=["Option A", "Invalid option \ud800 text"],
        correct_option=0,
    )
    errors_opt = validator.validate_single(q_bad_opt)
    assert any(e.rule == "MALFORMED_UNICODE" for e in errors_opt)
    assert any("Option 2 contains invalid Unicode" in e.message for e in errors_opt)

    # 3. Lone surrogate in explanation
    q_bad_expl = QuizQuestion.model_construct(
        question="Normal question?",
        options=["Option A", "Option B"],
        correct_option=0,
        explanation="Invalid explanation \ud800",
    )
    errors_expl = validator.validate_single(q_bad_expl)
    assert any(e.rule == "MALFORMED_UNICODE" for e in errors_expl)
    assert any("Explanation contains invalid Unicode" in e.message for e in errors_expl)


def test_question_exact_boundaries(validator: QuizValidator):
    """Test exact UTF-16 code unit boundaries (299, 300, 301) for question text (ISS-5451-02)."""
    # 299 UTF-16 units (passes)
    q_299 = QuizQuestion.model_construct(
        question="Q" * 299,
        options=["Option A", "Option B"],
        correct_option=0,
    )
    assert not any(e.rule == "QUESTION_TOO_LONG" for e in validator.validate_single(q_299))

    # 300 UTF-16 units (passes - exact limit)
    q_300 = QuizQuestion.model_construct(
        question="Q" * 300,
        options=["Option A", "Option B"],
        correct_option=0,
    )
    assert not any(e.rule == "QUESTION_TOO_LONG" for e in validator.validate_single(q_300))

    # 301 UTF-16 units via ASCII (fails)
    q_301 = QuizQuestion.model_construct(
        question="Q" * 301,
        options=["Option A", "Option B"],
        correct_option=0,
    )
    errs_301 = validator.validate_single(q_301)
    assert any(e.rule == "QUESTION_TOO_LONG" for e in errs_301)
    q_err = next(e for e in errs_301 if e.rule == "QUESTION_TOO_LONG")
    assert "Question text is 301 characters (Telegram limit is 300 characters)" in q_err.message

    # 301 UTF-16 units via emoji: 299 ASCII chars + 1 emoji (len == 300, UTF-16 units == 301)
    emoji_q_301 = ("Q" * 299) + "🎯"
    assert len(emoji_q_301) == 300
    assert telegram_utf16_length(emoji_q_301) == 301

    q_emoji_301 = QuizQuestion.model_construct(
        question=emoji_q_301,
        options=["Option A", "Option B"],
        correct_option=0,
    )
    errs_emoji = validator.validate_single(q_emoji_301)
    assert any(e.rule == "QUESTION_TOO_LONG" for e in errs_emoji)
    q_err_emoji = next(e for e in errs_emoji if e.rule == "QUESTION_TOO_LONG")
    assert "Question text is 301 characters (Telegram limit is 300 characters)" in q_err_emoji.message


def test_explanation_exact_boundaries(validator: QuizValidator):
    """Test exact UTF-16 code unit boundaries (199, 200, 201) for explanation text (ISS-5451-02)."""
    # 199 UTF-16 units (passes)
    q_199 = QuizQuestion(
        question="Normal Question?",
        options=["Option A", "Option B"],
        correct_option=0,
        explanation="E" * 199,
    )
    assert not any(e.rule == "EXPLANATION_TOO_LONG" for e in validator.validate_single(q_199))

    # 200 UTF-16 units (passes - exact limit)
    q_200 = QuizQuestion(
        question="Normal Question?",
        options=["Option A", "Option B"],
        correct_option=0,
        explanation="E" * 200,
    )
    assert not any(e.rule == "EXPLANATION_TOO_LONG" for e in validator.validate_single(q_200))

    # 201 UTF-16 units via ASCII (fails)
    q_201 = QuizQuestion(
        question="Normal Question?",
        options=["Option A", "Option B"],
        correct_option=0,
        explanation="E" * 201,
    )
    errs_201 = validator.validate_single(q_201)
    assert any(e.rule == "EXPLANATION_TOO_LONG" for e in errs_201)
    expl_err = next(e for e in errs_201 if e.rule == "EXPLANATION_TOO_LONG")
    assert "Explanation is 201 characters (Telegram limit is 200)" in expl_err.message

    # 201 UTF-16 units via emoji: 199 ASCII chars + 1 emoji (len == 200, UTF-16 units == 201)
    emoji_expl_201 = ("E" * 199) + "💡"
    assert len(emoji_expl_201) == 200
    assert telegram_utf16_length(emoji_expl_201) == 201

    q_emoji_201 = QuizQuestion(
        question="Normal Question?",
        options=["Option A", "Option B"],
        correct_option=0,
        explanation=emoji_expl_201,
    )
    errs_emoji = validator.validate_single(q_emoji_201)
    assert any(e.rule == "EXPLANATION_TOO_LONG" for e in errs_emoji)
    expl_err_emoji = next(e for e in errs_emoji if e.rule == "EXPLANATION_TOO_LONG")
    assert "Explanation is 201 characters (Telegram limit is 200)" in expl_err_emoji.message
