"""Unit tests for text-only quiz creation soft-gate in WAITING_FOR_BULK_INPUT state."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Document, PhotoSize, Update
from telegram.ext import ContextTypes

from app.bot.handlers.bulk import (
    receive_quiz_document_message,
    receive_quiz_part_message,
    session_manager,
)
from app.bot.states import QuizCreationState


@pytest.fixture(autouse=True)
def cleanup_session():
    """Ensure clean test session state before and after each test."""
    session_manager.discard_session(user_id=12345, chat_id=12345)
    yield
    session_manager.discard_session(user_id=12345, chat_id=12345)


@pytest.mark.asyncio
async def test_document_upload_returns_soft_gate_warning():
    """Verify uploading a document returns the polite disabled warning and keeps state."""
    mock_message = AsyncMock()
    mock_message.document = MagicMock(spec=Document, file_name="sample_quiz.xlsx", file_size=1024)
    mock_message.photo = None
    mock_message.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.message = mock_message
    update.effective_user.id = 12345
    update.effective_chat.id = 12345

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    state = await receive_quiz_document_message(update, context)

    assert state == QuizCreationState.WAITING_FOR_BULK_INPUT
    mock_message.reply_text.assert_called_once()
    sent_text = mock_message.reply_text.call_args[0][0]
    assert "Document & Photo uploads are temporarily disabled" in sent_text
    assert "only direct <b>text messages</b> are supported" in sent_text


@pytest.mark.asyncio
async def test_photo_upload_returns_soft_gate_warning():
    """Verify uploading a photo returns the polite disabled warning and keeps state."""
    mock_message = AsyncMock()
    mock_message.document = None
    photo_item = MagicMock(spec=PhotoSize, file_unique_id="photo123", file_size=2048)
    mock_message.photo = [photo_item]
    mock_message.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.message = mock_message
    update.effective_user.id = 12345
    update.effective_chat.id = 12345

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    state = await receive_quiz_document_message(update, context)

    assert state == QuizCreationState.WAITING_FOR_BULK_INPUT
    mock_message.reply_text.assert_called_once()
    sent_text = mock_message.reply_text.call_args[0][0]
    assert "Document & Photo uploads are temporarily disabled" in sent_text
    assert "only direct <b>text messages</b> are supported" in sent_text


@pytest.mark.asyncio
async def test_empty_update_returns_waiting_state():
    """Verify empty or non-document message without doc/photo returns WAITING_FOR_BULK_INPUT."""
    mock_message = AsyncMock()
    mock_message.document = None
    mock_message.photo = None
    mock_message.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.message = mock_message
    update.effective_user.id = 12345
    update.effective_chat.id = 12345

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    state = await receive_quiz_document_message(update, context)

    assert state == QuizCreationState.WAITING_FOR_BULK_INPUT
    mock_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_direct_text_question_input_still_works():
    """Verify that direct text questions continue to be ingested and parsed properly."""
    valid_quiz_text = (
        "1. What is the capital of France?\n"
        "A) London\n"
        "B) Paris\n"
        "C) Berlin\n"
        "D) Madrid\n"
        "Answer: B\n"
    )

    mock_message = AsyncMock()
    mock_message.text = valid_quiz_text
    mock_message.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.message = mock_message
    update.effective_user.id = 12345
    update.effective_chat.id = 12345

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    state = await receive_quiz_part_message(update, context)

    assert state == QuizCreationState.WAITING_FOR_BULK_INPUT
    session = session_manager.get_active_session(12345, 12345)
    assert session is not None
    assert session.total_parts == 1
    assert session.total_questions_detected >= 1
