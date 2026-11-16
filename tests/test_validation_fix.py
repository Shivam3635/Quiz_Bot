"""Tests for in-creation validation error detection and in-place fixing."""

import pytest

from app.bot.keyboards.bulk import get_validation_error_keyboard
from app.bot.keyboards.preview import get_edit_question_keyboard, get_preview_keyboard
from app.parser.models import QuizQuestion, QuizSettings
from app.parser.validator import QuizValidator


def test_validation_error_detection_and_reporting():
    """Verify that options exceeding Telegram's 100-character limit are detected with proper indices."""
    validator = QuizValidator()

    # Generate 5 questions, where Q2 and Q4 have options > 100 chars
    long_opt_1 = (
        "This is a very long option text designed to exceed the Telegram limit of 100 characters. "
        * 2
    )
    long_opt_2 = (
        "Another long option that is far too long for a Telegram quiz poll option field. " * 2
    )

    questions = [
        QuizQuestion(question="Q1 Valid?", options=["A", "B", "C", "D"], correct_option=0),
        QuizQuestion(
            question="Q2 Invalid Option?", options=["A", long_opt_1, "C", "D"], correct_option=1
        ),
        QuizQuestion(question="Q3 Valid?", options=["A", "B", "C", "D"], correct_option=2),
        QuizQuestion(
            question="Q4 Invalid Option?", options=[long_opt_2, "B", "C", "D"], correct_option=0
        ),
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
    kb_with_issues = get_preview_keyboard(
        current_index=0, total_questions=5, invalid_indices=[1, 3]
    )
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


def test_preview_keyboard_shows_done_save_button_when_valid():
    """Verify that draft preview keyboard displays 'Done / Back to Quiz' at bottom when all questions are valid."""
    kb_clean = get_preview_keyboard(
        current_index=0, total_questions=5, invalid_indices=[], saved_quiz_set_id=None
    )
    flat_clean = [b.callback_data for row in kb_clean.inline_keyboard for b in row]
    assert "finish_creation_settings" in flat_clean
    assert kb_clean.inline_keyboard[-1][0].text == "✅ Done / Back to Quiz"

    kb_invalid = get_preview_keyboard(
        current_index=0, total_questions=5, invalid_indices=[1], saved_quiz_set_id=None
    )
    flat_invalid = [b.callback_data for row in kb_invalid.inline_keyboard for b in row]
    assert "finish_creation_settings" not in flat_invalid
    assert kb_invalid.inline_keyboard[-1][0].text == "❌ Cancel"


def test_settings_keyboard_has_done_save_button():
    """Verify settings keyboard includes 'Done / Back to Quiz' option at bottom when valid."""
    from app.bot.keyboards.settings import get_settings_keyboard

    settings = QuizSettings(title="Test Quiz")
    kb = get_settings_keyboard(settings)
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "finish_creation_settings" in flat
    assert kb.inline_keyboard[-1][0].text == "✅ Done / Back to Quiz"


@pytest.mark.asyncio
async def test_finish_creation_settings_via_done_command():
    """Verify finishing quiz creation via /done command persists quiz and ends conversation."""
    from unittest.mock import AsyncMock, MagicMock

    from telegram import Message, Update, User
    from telegram.ext import ConversationHandler

    from app.bot.handlers.settings import finish_creation_settings_callback
    from app.database.database import Base, engine

    Base.metadata.create_all(bind=engine)

    user_id = 777
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = "testcreator"
    update.effective_user = user
    update.effective_chat = MagicMock(id=888)
    msg = MagicMock(spec=Message)
    msg.reply_text = AsyncMock()
    update.message = msg
    update.callback_query = None

    context = MagicMock()
    valid_q = QuizQuestion(
        question="What is 2 + 2?",
        options=["3", "4", "5", "6"],
        correct_option=1,
    )
    context.user_data = {
        "bulk_questions": [valid_q],
        "quiz_settings": QuizSettings(title="Math Quiz"),
    }

    result = await finish_creation_settings_callback(update, context)
    assert result == ConversationHandler.END
    assert msg.reply_text.called
    assert "Math Quiz" in msg.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_finish_creation_settings_rejects_invalid_questions():
    """Verify finishing quiz creation warns and returns to preview when invalid questions remain."""
    from unittest.mock import AsyncMock, MagicMock

    from telegram import Message, Update, User

    from app.bot.handlers.settings import finish_creation_settings_callback
    from app.bot.states import QuizCreationState
    from app.database.database import Base, engine

    Base.metadata.create_all(bind=engine)

    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = 778
    user.username = "testcreator2"
    update.effective_user = user
    update.effective_chat = MagicMock(id=889)
    msg = MagicMock(spec=Message)
    msg.reply_text = AsyncMock()
    update.message = msg
    update.callback_query = None

    context = MagicMock()
    invalid_q = QuizQuestion(
        question="What is 2 + 2?",
        options=["3", "X" * 150, "5", "6"],  # Over 100 limit
        correct_option=1,
    )
    context.user_data = {
        "bulk_questions": [invalid_q],
        "quiz_settings": QuizSettings(title="Broken Math Quiz"),
    }

    result = await finish_creation_settings_callback(update, context)
    # Should return to previewing instead of ending
    assert result == QuizCreationState.PREVIEWING
    assert msg.reply_text.called
    first_call_text = msg.reply_text.call_args_list[0][0][0]
    assert "still need attention" in first_call_text


@pytest.mark.asyncio
async def test_e2e_edit_question_reveals_done_button_and_saves_quiz():
    """Simulate full lifecycle: upload with over-limit option -> done -> fix option -> done button visible -> save."""
    from unittest.mock import AsyncMock, MagicMock

    from telegram import CallbackQuery, Message, Update, User
    from telegram.ext import ConversationHandler

    from app.bot.handlers.bulk import multipart_done_callback
    from app.bot.handlers.preview import (
        fix_invalid_question_callback,
        receive_question_edit_message,
    )
    from app.bot.handlers.settings import finish_creation_settings_callback
    from app.bot.states import QuizCreationState
    from app.database.database import Base, engine
    from app.services.session_service import session_manager

    Base.metadata.create_all(bind=engine)

    user_id = 901
    chat_id = 902
    session = session_manager.create_session(user_id, chat_id)

    # Question 1 has question text > 300 characters (invalid in Telegram)
    long_q = (
        "What is the capital of Japan? "
        + "Extra explanatory question text that exceeds the 300 character limit! " * 5
    )
    raw_text = f"Q1. {long_q}\n" "A. Kyoto\n" "B. Tokyo ✅\n" "C. Osaka\n" "D. Nagoya\n"
    session.add_part(raw_text, detected_questions=1)

    # Step 1: User clicks Done on multipart session
    update_done = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = "japantester"
    update_done.effective_user = user
    update_done.effective_chat = MagicMock(id=chat_id)
    query_done = MagicMock(spec=CallbackQuery)
    query_done.data = "multipart_done"
    query_done.answer = AsyncMock()
    query_done.message = MagicMock(spec=Message)
    query_done.message.edit_text = AsyncMock()
    update_done.callback_query = query_done
    update_done.message = None

    context = MagicMock()
    context.user_data = {}

    state = await multipart_done_callback(update_done, context)
    assert state == QuizCreationState.PREVIEWING

    # Step 2: User clicks Fix Question 1
    update_fix = MagicMock(spec=Update)
    update_fix.effective_user = user
    update_fix.effective_chat = MagicMock(id=chat_id)
    query_fix = MagicMock(spec=CallbackQuery)
    query_fix.data = "fix_invalid_q_0"
    query_fix.answer = AsyncMock()
    query_fix.message = MagicMock(spec=Message)
    query_fix.message.edit_text = AsyncMock()
    update_fix.callback_query = query_fix
    update_fix.message = None

    state = await fix_invalid_question_callback(update_fix, context)
    assert state == QuizCreationState.EDITING_QUESTION

    # Step 3: User provides alternative question text that fits the limit
    context.user_data["edit_mode"] = "question_text"
    update_edit_msg = MagicMock(spec=Update)
    update_edit_msg.effective_user = user
    update_edit_msg.effective_chat = MagicMock(id=chat_id)
    msg_edit = MagicMock(spec=Message)
    msg_edit.text = "What is the capital of Japan?"
    msg_edit.reply_text = AsyncMock()
    update_edit_msg.message = msg_edit
    update_edit_msg.callback_query = None

    state = await receive_question_edit_message(update_edit_msg, context)
    assert state == QuizCreationState.PREVIEWING

    # Feedback message tells user all questions are valid and points to Done button
    assert msg_edit.reply_text.called
    feedback_text = msg_edit.reply_text.call_args_list[0][0][0]
    assert "All questions are now valid" in feedback_text
    assert "Done / Back to Quiz" in feedback_text

    # Check that keyboard in preview message contains Done / Back to Quiz at the bottom
    preview_call = msg_edit.reply_text.call_args_list[1]
    preview_kb = preview_call[1]["reply_markup"]
    saved_id = context.user_data.get("saved_quiz_set_id")
    expected_cb = f"view_quiz_{saved_id}" if saved_id else "finish_creation_settings"
    flat_callbacks = [b.callback_data for row in preview_kb.inline_keyboard for b in row]
    assert expected_cb in flat_callbacks
    assert preview_kb.inline_keyboard[-1][0].text == "✅ Done / Back to Quiz"
    assert preview_kb.inline_keyboard[-1][0].callback_data == expected_cb

    # Step 4: User clicks Done / Back to Quiz (or sends /done)
    update_finish = MagicMock(spec=Update)
    update_finish.effective_user = user
    update_finish.effective_chat = MagicMock(id=chat_id)
    query_finish = MagicMock(spec=CallbackQuery)
    query_finish.data = expected_cb
    query_finish.answer = AsyncMock()
    query_finish.message = MagicMock(spec=Message)
    query_finish.message.edit_message_text = AsyncMock()
    update_finish.callback_query = query_finish
    update_finish.message = None

    final_state = await finish_creation_settings_callback(update_finish, context)
    assert final_state == ConversationHandler.END
