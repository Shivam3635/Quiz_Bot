"""Unit tests for QuizValidator."""

import pytest
from app.parser.models import QuizQuestion
from app.parser.validator import QuizValidator


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
