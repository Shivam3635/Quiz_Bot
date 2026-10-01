"""Tests for in-creation validation error detection and in-place fixing."""

import pytest
from app.bot.keyboards.bulk import get_validation_error_keyboard
from app.bot.keyboards.preview import get_edit_question_keyboard, get_preview_keyboard
from app.parser.models import QuizQuestion, QuizSettings
from app.parser.parser import BulkQuizParser
from app.parser.validator import QuizValidator


def test_validation_error_detection_and_reporting():
    """Verify that options exceeding Telegram's 100-character limit are detected with proper indices."""
    validator = QuizValidator()

    # Generate 5 questions, where Q2 and Q4 have options > 100 chars
    long_opt_1 = "This is a very long option text designed to exceed the Telegram limit of 100 characters. " * 2
    long_opt_2 = "Another long option that is far too long for a Telegram quiz poll option field. " * 2

    questions = [
        QuizQuestion(question="Q1 Valid?", options=["A", "B", "C", "D"], correct_option=0),
        QuizQuestion(question="Q2 Invalid Option?", options=["A", long_opt_1, "C", "D"], correct_option=1),
        QuizQuestion(question="Q3 Valid?", options=["A", "B", "C", "D"], correct_option=2),
        QuizQuestion(question="Q4 Invalid Option?", options=[long_opt_2, "B", "C", "D"], correct_option=0),
        QuizQuestion(question="Q5 Valid?", options=["A", "B", "C", "D"], correct_option=3),
    ]

    val_res = validator.validate_batch(questions)
    assert not val_res.is_valid
    assert val_res.total_questions == 5
    assert val_res.valid_count == 3
    assert val_res.error_count == 2
    assert val_res.failed_question_indices == [2, 4]

    report = val_res.get_formatted_error_report(custom_footer="Fix below:")
    assert "Question 2" in report
    assert "Question 4" in report
    assert "limit: 100" in report


def test_validation_error_keyboard_structure():
    """Verify get_validation_error_keyboard generates correct callbacks for first invalid question and dropping."""
    kb = get_validation_error_keyboard(first_invalid_idx=9, total_valid=23, total_invalid=2)
    flat_buttons = [btn for row in kb.inline_keyboard for btn in row]

    # Check button text and callback data
    fix_btn = next((b for b in flat_buttons if "Fix Question 9" in b.text), None)
    assert fix_btn is not None
    assert fix_btn.callback_data == "fix_invalid_q_8"  # 0-indexed

    drop_btn = next((b for b in flat_buttons if "Drop 2 Invalid" in b.text), None)
    assert drop_btn is not None
    assert drop_btn.callback_data == "drop_invalid_questions"


def test_in_place_edit_resolves_validation_errors():
    """Verify that editing an oversized option in-place resolves the error."""
    validator = QuizValidator()
    long_opt = "X" * 135
    q = QuizQuestion(
        question="What is the capital of France?",
        options=["London", long_opt, "Berlin", "Madrid"],
        correct_option=1,
    )

    # Initial check fails
    errors_before = validator.validate_single(q, index=9)
    assert len(errors_before) == 1
    assert errors_before[0].rule == "OPTION_TOO_LONG"

    # User edits option B to shorter text with cursor
    q.options[1] = "Paris"

    # Check passes
    errors_after = validator.validate_single(q, index=9)
    assert len(errors_after) == 0


def test_drop_invalid_questions():
    """Verify dropping invalid questions yields only valid questions."""
    validator = QuizValidator()
    long_opt = "Y" * 120

    questions = [
        QuizQuestion(question="Q1 Valid?", options=["A", "B"], correct_option=0),
        QuizQuestion(question="Q2 Invalid?", options=[long_opt, "B"], correct_option=1),
        QuizQuestion(question="Q3 Valid?", options=["A", "B"], correct_option=0),
    ]

    val_res = validator.validate_batch(questions)
    assert len(val_res.valid_questions) == 2
    assert val_res.valid_questions[0].question == "Q1 Valid?"
    assert val_res.valid_questions[1].question == "Q3 Valid?"

    # Batch of valid questions passes validation
    second_res = validator.validate_batch(val_res.valid_questions)
    assert second_res.is_valid


def test_preview_keyboard_shows_issue_jump_button():
    """Verify preview keyboard shows jump button when invalid questions exist."""
    kb_with_issues = get_preview_keyboard(current_index=0, total_questions=5, invalid_indices=[1, 3])
    flat = [b.callback_data for row in kb_with_issues.inline_keyboard for b in row]
    assert "jump_next_invalid" in flat

    kb_clean = get_preview_keyboard(current_index=0, total_questions=5, invalid_indices=[])
    flat_clean = [b.callback_data for row in kb_clean.inline_keyboard for b in row]
    assert "jump_next_invalid" not in flat_clean


def test_edit_keyboard_shows_length_warnings():
    """Verify edit keyboard highlights option exceeding 100 chars."""
    q = QuizQuestion(
        question="Test Question",
        options=["Short", "Z" * 135],
        correct_option=0,
    )
    kb = get_edit_question_keyboard(q, current_index=0)
    flat_texts = [b.text for row in kb.inline_keyboard for b in row]

    # Option B should have a warning indicator with character count
    opt_b_btn = next((t for t in flat_texts if "Opt B" in t), "")
    assert "⚠️" in opt_b_btn
    assert "135" in opt_b_btn
