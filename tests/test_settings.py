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

