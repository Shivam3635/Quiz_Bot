"""Unit tests for QuizSettings models and keyboard toggles."""

import pytest
from app.parser.models import QuizSettings
from app.bot.keyboards.settings import get_settings_keyboard, get_timer_select_keyboard


def test_default_quiz_settings():
    """Verify default quiz settings."""
    settings = QuizSettings()
    assert settings.title is None
    assert settings.description is None
    assert settings.header_banner_enabled is True
    assert settings.is_anonymous is False
    assert settings.shuffle_options is False
    assert settings.explanation_enabled is True
    assert settings.time_limit == 15
    assert settings.channel_id is None


def test_custom_quiz_settings():
    """Verify custom settings values."""
    settings = QuizSettings(
        title="Science Test #1",
        description="Covers Chapter 1 to 5",
        header_banner_enabled=False,
        is_anonymous=False,
        shuffle_options=True,
        explanation_enabled=False,
        time_limit=45,
        channel_id="@mychannel",
    )
    assert settings.title == "Science Test #1"
    assert settings.description == "Covers Chapter 1 to 5"
    assert settings.header_banner_enabled is False
    assert settings.is_anonymous is False
    assert settings.shuffle_options is True
    assert settings.explanation_enabled is False
    assert settings.time_limit == 45
    assert settings.channel_id == "@mychannel"


def test_settings_keyboard_generation():
    """Verify settings keyboard reflects active settings."""
    settings = QuizSettings(
        title="General Science",
        description="Covers Class 10 Biology",
        header_banner_enabled=True,
        is_anonymous=True,
        time_limit=30,
        channel_id="@test_channel",
    )
    kb = get_settings_keyboard(settings)

    # Flatten all button texts
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]

    assert any("General Science" in t for t in button_texts)
    assert any("Description:" in t for t in button_texts)
    assert any("Channel Header Banner: ON" in t for t in button_texts)
    assert any("Anonymous: ON" in t for t in button_texts)
    assert any("30s" in t for t in button_texts)
    assert any("@test_channel" in t for t in button_texts)


def test_title_and_description_prompt_keyboards():
    """Verify title and description input step keyboards."""
    from app.bot.keyboards.bulk import get_title_input_keyboard, get_description_input_keyboard
    
    title_kb = get_title_input_keyboard()
    title_callbacks = [btn.callback_data for row in title_kb.inline_keyboard for btn in row]
    assert "skip_quiz_title" in title_callbacks
    assert "action_cancel" in title_callbacks

    desc_kb = get_description_input_keyboard()
    desc_callbacks = [btn.callback_data for row in desc_kb.inline_keyboard for btn in row]
    assert "skip_quiz_desc" in desc_callbacks
    assert "action_cancel" in desc_callbacks


def test_timer_select_keyboard():
    """Verify timer selector keyboard."""
    kb = get_timer_select_keyboard()
    button_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "set_timer_15" in button_data
    assert "set_timer_30" in button_data
    assert "set_timer_60" in button_data


def test_quiz_settings_config_keyboard_title_and_description():
    """Verify get_quiz_settings_config_keyboard includes title and description buttons."""
    from app.bot.keyboards.settings import get_quiz_settings_config_keyboard

    settings = QuizSettings(title="My Special Quiz", description="A great quiz")
    kb = get_quiz_settings_config_keyboard(settings, quiz_set_id=42)

    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]

    assert "cfg_title_42" in callbacks
    assert "cfg_desc_42" in callbacks
    assert any("Title: My Special Quiz" in t for t in texts)
    assert any("Description: A great quiz" in t for t in texts)
    assert any("Done / Back to Quiz" in t for t in texts)


@pytest.mark.asyncio
async def test_config_title_and_desc_callbacks():
    """Verify config_title_callback and config_desc_callback prompt the user."""
    from unittest.mock import AsyncMock, MagicMock
    from app.bot.handlers.settings import config_title_callback, config_desc_callback

    update = MagicMock()
    update.effective_user.id = 12345
    update.callback_query = AsyncMock()
    update.callback_query.data = "cfg_title_10"
    update.callback_query.message = AsyncMock()
    context = MagicMock()
    context.user_data = {"quiz_settings": QuizSettings(title="Old Title", description="Old Desc")}

    await config_title_callback(update, context)
    assert context.user_data.get("waiting_for_title_text") is True
    assert context.user_data.get("saved_quiz_set_id") == 10
    update.callback_query.edit_message_text.assert_called_once()

    update.callback_query.data = "cfg_desc_10"
    update.callback_query.edit_message_text.reset_mock()
    await config_desc_callback(update, context)
    assert context.user_data.get("waiting_for_desc_text") is True
    assert context.user_data.get("waiting_for_title_text") is None
    update.callback_query.edit_message_text.assert_called_once()


@pytest.mark.asyncio
async def test_receive_republish_dest_message_title_and_desc():
    """Verify receive_republish_dest_message updates title and description outside creation flow."""
    from unittest.mock import AsyncMock, MagicMock
    from app.bot.handlers.myquizzes import receive_republish_dest_message

    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "New Quiz Title"
    context = MagicMock()
    context.user_data = {
        "waiting_for_title_text": True,
        "saved_quiz_set_id": 999,
        "quiz_settings": QuizSettings(title="Old Title"),
    }

    handled = await receive_republish_dest_message(update, context)
    assert handled is True
    assert context.user_data["quiz_settings"].title == "New Quiz Title"
    assert "waiting_for_title_text" not in context.user_data
    update.message.reply_text.assert_called_once()


