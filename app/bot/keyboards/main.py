"""Main menu keyboards for QuizBotPro."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Return clean inline keyboard markup for the main menu."""
    keyboard = [
        [
            InlineKeyboardButton("✍️ Create Quiz", callback_data="action_bulk_create"),
        ],
        [
            InlineKeyboardButton("📚 My Quizzes", callback_data="action_saved_sets"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="action_help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Return inline keyboard markup with a single Cancel button."""
    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="action_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)
