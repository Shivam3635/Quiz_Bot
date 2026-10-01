"""Unit tests for /myquizzes dashboard keyboards and handlers."""

import pytest
from unittest.mock import MagicMock
from app.database.models import QuizSet, Question
from app.bot.keyboards.myquizzes import (
    get_delete_confirm_keyboard,
    get_my_quizzes_keyboard,
    get_preview_saved_quiz_keyboard,
    get_quiz_details_keyboard,
    get_republish_confirm_keyboard,
)


def test_my_quizzes_keyboard_generation():
    """Verify /myquizzes dashboard listing keyboard generation and pagination."""
    qs1 = QuizSet(id=1, title="Quiz One", questions=[Question(), Question()])
    qs2 = QuizSet(id=2, title="Quiz Two", questions=[Question()])

    kb = get_my_quizzes_keyboard([qs1, qs2], page=0, total_count=2, per_page=5)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]

    assert "view_quiz_1" in callbacks
    assert "view_quiz_2" in callbacks
    assert any("Quiz One (2 Qs)" in t for t in texts)
    assert any("Quiz Two (1 Qs)" in t for t in texts)
    assert "action_bulk_create" in callbacks
    assert "action_main_menu" in callbacks


def test_my_quizzes_pagination_buttons():
    """Verify pagination buttons appear when total_count exceeds per_page."""
    qs_list = [QuizSet(id=i, title=f"Quiz {i}", questions=[]) for i in range(1, 6)]

    # Page 0 of 2 pages (total_count=8, per_page=5)
    kb = get_my_quizzes_keyboard(qs_list, page=0, total_count=8, per_page=5)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "my_quizzes_list_1" in callbacks

    # Page 1 of 2 pages
    kb_p1 = get_my_quizzes_keyboard(qs_list, page=1, total_count=8, per_page=5)
    callbacks_p1 = [btn.callback_data for row in kb_p1.inline_keyboard for btn in row]
    assert "my_quizzes_list_0" in callbacks_p1


def test_quiz_details_and_republish_keyboards():
    """Verify controls for quiz details and 1-click re-publishing."""
    details_kb = get_quiz_details_keyboard(42)
    details_cbs = [btn.callback_data for row in details_kb.inline_keyboard for btn in row]
    assert "execute_republish_42" in details_cbs
    assert "edit_quiz_settings_42" in details_cbs
    assert "republish_change_dest_42" in details_cbs
    assert "preview_saved_42_0" in details_cbs
    assert "delete_saved_prompt_42" in details_cbs
    assert "my_quizzes_list_0" in details_cbs

    confirm_kb = get_republish_confirm_keyboard(42)
    confirm_cbs = [btn.callback_data for row in confirm_kb.inline_keyboard for btn in row]
    assert "execute_republish_42" in confirm_cbs
    assert "republish_change_dest_42" in confirm_cbs
    assert "view_quiz_42" in confirm_cbs

    preview_kb = get_preview_saved_quiz_keyboard(42, current_idx=1, total_questions=5)
    preview_cbs = [btn.callback_data for row in preview_kb.inline_keyboard for btn in row]
    assert "preview_saved_42_0" in preview_cbs
    assert "preview_saved_42_2" in preview_cbs
    assert "edit_saved_q_42_1" in preview_cbs
    assert "del_saved_q_42_1" in preview_cbs
    assert "execute_republish_42" in preview_cbs

    del_kb = get_delete_confirm_keyboard(42)
    del_cbs = [btn.callback_data for row in del_kb.inline_keyboard for btn in row]
    assert "confirm_delete_quiz_42" in del_cbs
    assert "view_quiz_42" in del_cbs
