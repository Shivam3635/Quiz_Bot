"""Unit tests for /help, /guide, and PDF User Guide generator."""

import io
import pytest
from unittest.mock import AsyncMock, MagicMock
from pypdf import PdfReader
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.handlers.help import help_command, guide_command, template_command, HELP_MESSAGE
from app.utils.guide_generator import build_user_guide_pdf_bytes


def test_build_user_guide_pdf_structure_and_pages():
    """Verify generated PDF guide is valid, non-empty, and has multiple structured pages."""
    pdf_bytes = build_user_guide_pdf_bytes()
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 5000
    assert pdf_bytes.startswith(b"%PDF-")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 2

    # Extract text from page 1 and verify key sections are present
    p1_text = reader.pages[0].extract_text()
    assert "QuizBotPro" in p1_text
    assert "What is QuizBotPro" in p1_text


@pytest.mark.asyncio
async def test_help_command_sends_brief_message_and_buttons():
    """Verify /help sends a clean, brief overview with PDF guide download button."""
    mock_message = AsyncMock()
    update = MagicMock(spec=Update)
    update.message = mock_message
    update.callback_query = None

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await help_command(update, context)

    mock_message.reply_text.assert_called_once()
    args, kwargs = mock_message.reply_text.call_args
    sent_text = args[0]
    assert "Quick Help & Guidelines" in sent_text
    assert "/newquiz" in sent_text
    assert "/guide" in sent_text

    # Verify buttons
    reply_markup = kwargs.get("reply_markup")
    assert reply_markup is not None
    button_callbacks = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
    assert "action_download_guide" in button_callbacks
    assert "action_download_template" in button_callbacks
    assert "action_main_menu" in button_callbacks


@pytest.mark.asyncio
async def test_guide_command_sends_document_pdf():
    """Verify /guide command generates and sends QuizBotPro_User_Guide.pdf document."""
    mock_bot = AsyncMock()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = mock_bot

    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(id=12345)
    update.callback_query = None

    await guide_command(update, context)

    mock_bot.send_document.assert_called_once()
    _, kwargs = mock_bot.send_document.call_args
    assert kwargs.get("chat_id") == 12345
    assert kwargs.get("filename") == "QuizBotPro_User_Guide.pdf"
    assert "Official User Guide" in kwargs.get("caption")
    document_data = kwargs.get("document")
    assert isinstance(document_data, bytes)
    assert document_data.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_template_command_sends_excel_template():
    """Verify /template command sends quiz_template.xlsx."""
    mock_bot = AsyncMock()
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = mock_bot

    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(id=54321)
    update.callback_query = None

    await template_command(update, context)

    mock_bot.send_document.assert_called_once()
    _, kwargs = mock_bot.send_document.call_args
    assert kwargs.get("chat_id") == 54321
    assert kwargs.get("filename") == "quiz_template.xlsx"
