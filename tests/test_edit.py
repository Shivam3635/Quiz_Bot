"""Unit tests for preview question editing and deletion."""

import pytest
from app.bot.keyboards.preview import (
    get_edit_question_keyboard,
    get_delete_confirm_keyboard,
    get_preview_keyboard,
)
from app.parser.models import QuizQuestion


@pytest.fixture
def sample_question() -> QuizQuestion:
    return QuizQuestion(
        question="Which planet is known as the Red Planet?",
        options=["Venus", "Mars", "Jupiter", "Saturn"],
        correct_option=1,
        explanation="Due to iron oxide on its surface.",
    )


def test_preview_keyboard_has_edit_button():
    """Verify preview keyboard offers Edit and Delete options."""
    kb = get_preview_keyboard(current_index=0, total_questions=3)
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    button_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]

    assert any("Edit" in t and "Question" in t for t in button_texts)
    assert any("Delete" in t for t in button_texts)
    assert "edit_current_question" in button_data
    assert "delete_current_question" in button_data


def test_edit_question_keyboard(sample_question):
    """Verify edit menu offers 1-click correct answer switch and edit options."""
    kb = get_edit_question_keyboard(sample_question, current_index=0)
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    button_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]

    # Quick answer options for 4 choices (A, B, C, D)
    assert "set_correct_ans_0" in button_data
    assert "set_correct_ans_1" in button_data
    assert "set_correct_ans_2" in button_data
    assert "set_correct_ans_3" in button_data

    # B is the current correct answer, so it should have a checkmark
    assert any("✅ B" in t for t in button_texts)

    # Edit actions
    assert "edit_q_text" in button_data
    assert "edit_opt_0" in button_data
    assert "edit_opt_1" in button_data
    assert "edit_q_expl" in button_data
    assert "show_copyable_q" in button_data
    assert "goto_preview" in button_data


def test_delete_confirm_keyboard():
    """Verify delete confirmation keyboard buttons."""
    kb = get_delete_confirm_keyboard(current_index=2)
    button_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "confirm_delete_2" in button_data
    assert "goto_preview" in button_data


def test_in_place_edit_operations(sample_question):
    """Verify question mutation operations."""
    # 1. Change correct option
    sample_question.correct_option = 0
    assert sample_question.correct_letter == "A"

    # 2. Modify question text
    sample_question.question = "Updated question?"
    assert sample_question.question == "Updated question?"

    # 3. Update explanation
    sample_question.explanation = "New explanation text"
    assert sample_question.explanation == "New explanation text"

    sample_question.explanation = None
    assert sample_question.explanation is None
