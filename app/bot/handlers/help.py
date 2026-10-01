"""Help command and callback handler."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.utils.logger import setup_logger

logger = setup_logger(__name__)

HELP_MESSAGE = (
    "❓ <b>BulkQuiz Help & Usage Guide</b>\n\n"
    "BulkQuiz lets you bulk-create native Telegram quiz polls quickly.\n\n"
    "<b>Supported Question Format:</b>\n"
    "<code>"
    "Q1. What is the capital of India?\n"
    "A) Mumbai\n"
    "B) New Delhi\n"
    "C) Kolkata\n"
    "D) Chennai\n"
    "Answer: B\n\n"
    "Q2. Which language is used for web styling?\n"
    "A) Python\n"
    "B) Java\n"
    "C) CSS\n"
    "D) C++\n"
    "Answer: C"
    "</code>\n\n"
    "✨ <b>Rich Text Formatting Supported:</b>\n"
    "You can style question text and explanations using:\n"
    "• <b>Bold:</b> <code>**word**</code> or <code>&lt;b&gt;word&lt;/b&gt;</code>\n"
    "• <i>Italic:</i> <code>*word*</code> or <code>&lt;i&gt;word&lt;/i&gt;</code>\n"
    "• <code>Code:</code> <code>`code`</code> or <code>&lt;code&gt;code&lt;/code&gt;</code>\n"
    "• <u>Underline:</u> <code>__word__</code> or <code>&lt;u&gt;word&lt;/u&gt;</code>\n"
    "• <s>Strikethrough:</s> <code>~~word~~</code> or <code>&lt;s&gt;word&lt;/s&gt;</code>\n\n"
    "<b>Steps:</b>\n"
    "1️⃣ Run /newquiz or tap <b>📦 Bulk Create</b>\n"
    "2️⃣ Set Title and Description (optional)\n"
    "3️⃣ Send multiple questions across messages, then press <b>✅ Done</b>\n"
    "4️⃣ Configure settings (timer, anonymous, channel destination)\n"
    "5️⃣ Preview, edit, and publish automatically!\n\n"
    "<b>Commands:</b>\n"
    "• /newquiz - Create a new bulk quiz\n"
    "• /myquizzes - Dashboard to view past quizzes & 1-click re-publish\n"
    "• /startquiz - Launch a live group quiz battle\n"
    "• /stopquiz - Stop an active group quiz battle\n"
    "• /cancel - Cancel current session\n"
    "• /help - Show this guide\n\n"
    "💡 <i>Tip: Ensure this bot is an Admin with 'Post Messages' rights in your target channel.</i>"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command or help callback button."""
    back_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="action_main_menu")]]
    )

    if update.message:
        await update.message.reply_text(
            HELP_MESSAGE,
            reply_markup=back_keyboard,
            parse_mode=ParseMode.HTML,
        )
    elif update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                HELP_MESSAGE,
                reply_markup=back_keyboard,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.debug("Minor edit text exception: %s", e)
