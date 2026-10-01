"""Test module imports, configuration, and app initialization."""

import pytest
from app.config.settings import Settings, get_settings
from app.main import create_bot_app
from app.bot.states import QuizCreationState
from app.bot.keyboards.main import get_main_menu_keyboard, get_cancel_keyboard


def test_settings_load():
    """Verify that settings can load and have expected defaults."""
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.DATABASE_URL.startswith("sqlite") or settings.DATABASE_URL.startswith("postgresql")
    assert settings.MIN_OPTIONS_PER_QUESTION == 2
    assert settings.MAX_OPTIONS_PER_QUESTION == 10


def test_keyboards():
    """Verify keyboard structures."""
    main_menu = get_main_menu_keyboard()
    assert len(main_menu.inline_keyboard) == 3
    # Check that Create Quiz button is in the first row
    assert main_menu.inline_keyboard[0][0].text == "✍️ Create Quiz"
    assert main_menu.inline_keyboard[0][0].callback_data == "action_bulk_create"

    cancel = get_cancel_keyboard()
    assert len(cancel.inline_keyboard) == 1
    assert cancel.inline_keyboard[0][0].text == "❌ Cancel"


def test_states():
    """Verify conversation states are defined."""
    assert QuizCreationState.WAITING_FOR_BULK_INPUT is not None
    assert QuizCreationState.CONFIGURING_SETTINGS is not None
    assert QuizCreationState.PREVIEWING is not None
    assert QuizCreationState.PUBLISHING is not None


def test_app_factory():
    """Verify Telegram Application can be built with configured handlers."""
    app = create_bot_app()
    assert app is not None
    # Verify handlers are attached
    assert len(app.handlers) > 0
    # Level 0 handlers contain CommandHandler and CallbackQueryHandler
    handlers_group_0 = app.handlers.get(0, [])
    assert len(handlers_group_0) >= 4
