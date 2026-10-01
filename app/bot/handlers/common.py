"""Common handlers and placeholders."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


async def coming_soon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle buttons for features scheduled for upcoming phases."""
    query = update.callback_query
    if query:
        await query.answer("🚧 Coming soon in an upcoming phase!")
        back_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="action_main_menu")]]
        )
        await query.edit_message_text(
            "🚧 <b>Feature Coming Soon</b>\n\n"
            "This feature is scheduled for an upcoming phase.\n"
            "Currently, you can use <b>📦 Bulk Create</b> to publish your quizzes!",
            reply_markup=back_keyboard,
            parse_mode=ParseMode.HTML,
        )
