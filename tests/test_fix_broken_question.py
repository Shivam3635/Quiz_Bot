"""Tests for in-line fixing of broken questions during bulk quiz creation."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, User, Chat, Message, CallbackQuery
from telegram.ext import ContextTypes

from app.bot.handlers.bulk import (
    multipart_done_callback,
    receive_quiz_part_message,
    drop_broken_question_callback,
    clear_creation_session,
)
from app.bot.states import QuizCreationState
from app.parser.models import QuizSettings
from app.services.session_service import session_manager


@pytest.fixture(autouse=True)
def clean_sessions():
    """Ensure session manager is cleaned up around each test."""
    session_manager._sessions.clear()
    yield
    session_manager._sessions.clear()


def make_mock_update(user_id=123, chat_id=456, text=None, callback_data=None):
    update = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = "testuser"
    chat = MagicMock(spec=Chat)
    chat.id = chat_id

    update.effective_user = user
    update.effective_chat = chat

    if text is not None:
        message = MagicMock(spec=Message)
        message.text = text
        message.message_id = 999
        message.reply_text = AsyncMock()
        update.message = message
        update.callback_query = None
    elif callback_data is not None:
        query = MagicMock(spec=CallbackQuery)
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        message = MagicMock(spec=Message)
        message.message_id = 888
        message.edit_text = AsyncMock()
        query.message = message
        update.callback_query = query
        update.message = None
    return update


def make_mock_context(user_data=None):
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = user_data if user_data is not None else {}
    context.bot = MagicMock()
    context.bot.delete_message = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_broken_question_prompts_for_input():
    """When a quiz has an error in Question 2, multipart_done prompts specifically for Question 2."""
    user_id = 101
    chat_id = 202

    session = session_manager.create_session(user_id, chat_id)
    # Part with Q1 valid and Q2 broken (no options)
    part_text = (
        "Q1. Capital of France?\n"
        "A) Paris ✅\n"
        "B) London\n"
        "C) Rome\n"
        "D) Berlin\n\n"
        "Q2. Capital of Germany?\n"
    )
    session.add_part(part_text, detected_questions=2)

    update = make_mock_update(user_id=user_id, chat_id=chat_id, callback_data="multipart_done")
    context = make_mock_context()

    next_state = await multipart_done_callback(update, context)

    assert next_state == QuizCreationState.WAITING_FOR_BULK_INPUT
    assert context.user_data.get("fixing_question_number") == 2
    assert context.user_data.get("pending_error_q_nums") == [2]
    assert len(context.user_data.get("parsed_valid_questions")) == 1

    # Check edit_text was called with prompt asking for Question 2
    call_args = update.callback_query.message.edit_text.call_args
    prompt_text = call_args[0][0]
    assert "Question 2" in prompt_text
    assert "Please send Question 2 again" in prompt_text


@pytest.mark.asyncio
async def test_send_fixed_question_resolves_error():
    """Sending the corrected question resolves the issue and advances to settings."""
    user_id = 102
    chat_id = 203

    session = session_manager.create_session(user_id, chat_id)
    part_text = (
        "Q1. Capital of France?\n"
        "A) Paris ✅\n"
        "B) London\n"
        "C) Rome\n"
        "D) Berlin\n\n"
        "Q2. Capital of Germany?\n"
    )
    session.add_part(part_text, detected_questions=2)

    update_done = make_mock_update(user_id=user_id, chat_id=chat_id, callback_data="multipart_done")
    context = make_mock_context()
    await multipart_done_callback(update_done, context)

    assert context.user_data.get("fixing_question_number") == 2

    # Now user sends the complete Question 2
    fixed_q_text = (
        "Q2. Capital of Germany?\n"
        "A) Munich\n"
        "B) Berlin ✅\n"
        "C) Frankfurt\n"
        "D) Hamburg"
    )
    update_fix = make_mock_update(user_id=user_id, chat_id=chat_id, text=fixed_q_text)
    next_state = await receive_quiz_part_message(update_fix, context)

    assert next_state == QuizCreationState.CONFIGURING_SETTINGS
    assert "fixing_question_number" not in context.user_data
    assert len(context.user_data.get("bulk_questions")) == 2

    reply_args = update_fix.message.reply_text.call_args
    reply_text = reply_args[0][0]
    assert "Question 2 fixed" in reply_text


@pytest.mark.asyncio
async def test_drop_broken_question_retains_valid():
    """Clicking drop broken question retains valid questions and moves forward."""
    user_id = 103
    chat_id = 204

    session = session_manager.create_session(user_id, chat_id)
    part_text = (
        "Q1. Capital of France?\n"
        "A) Paris ✅\n"
        "B) London\n"
        "C) Rome\n"
        "D) Berlin\n\n"
        "Q2. Capital of Germany?\n"
    )
    session.add_part(part_text, detected_questions=2)

    update_done = make_mock_update(user_id=user_id, chat_id=chat_id, callback_data="multipart_done")
    context = make_mock_context()
    await multipart_done_callback(update_done, context)

    # Click Drop Question 2
    update_drop = make_mock_update(user_id=user_id, chat_id=chat_id, callback_data="drop_broken_q_2")
    next_state = await drop_broken_question_callback(update_drop, context)

    assert next_state == QuizCreationState.CONFIGURING_SETTINGS
    assert len(context.user_data.get("bulk_questions")) == 1
    assert context.user_data.get("bulk_questions")[0].question == "Capital of France?"


@pytest.mark.asyncio
async def test_hindi_north_question_not_mistaken_for_answer():
    """Questions starting with 'उत्तर' (Hindi for 'North') should not be mistaken for answer lines."""
    from app.parser.parser import QuizBotProParser

    raw_text = (
        "Q12. How many types of North are there?\n"
        "उत्तर कितने प्रकार के होते हैं?\n"
        "A. 3 ✅\n"
        "B. 5\n"
        "C. 8\n"
        "D. 10"
    )
    parser = QuizBotProParser()
    result = parser.parse(raw_text)

    assert not result.has_errors
    assert len(result.questions) == 1
    q = result.questions[0]
    assert "How many types of North are there?" in q.question
    assert "उत्तर कितने प्रकार के होते हैं?" in q.question
    assert q.options == ["3", "5", "8", "10"]
    assert q.correct_option == 0

