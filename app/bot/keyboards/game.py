"""Keyboards for Live Group Quiz Mode and Lobby."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.database.models import QuizSet


def get_game_lobby_keyboard(quiz_set_id: int) -> InlineKeyboardMarkup:
    """Generate lobby button for participants to vote ready."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🙋 I am ready!", callback_data=f"game_join_{quiz_set_id}"),
        ],
    ])


def get_game_finished_keyboard(quiz_set_id: int) -> InlineKeyboardMarkup:
    """Generate end-of-game options (Play Again removed per admin control requirements)."""
    return InlineKeyboardMarkup([])


def get_select_quiz_for_game_keyboard(quiz_sets: list[QuizSet]) -> InlineKeyboardMarkup:
    """List available quiz sets to start a battle in group."""
    keyboard: list[list[InlineKeyboardButton]] = []
    for qs in quiz_sets:
        q_count = len(qs.questions)
        title_snippet = qs.title[:24] + "..." if len(qs.title) > 24 else qs.title
        keyboard.append([
            InlineKeyboardButton(
                f"🎯 {title_snippet} ({q_count} Qs)",
                callback_data=f"game_launch_{qs.id}",
            )
        ])
    keyboard.append([
        InlineKeyboardButton("❌ Cancel", callback_data="game_cancel_selection")
    ])
    return InlineKeyboardMarkup(keyboard)
