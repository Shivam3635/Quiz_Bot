"""Help command and callback handler."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.utils.logger import setup_logger

logger = setup_logger(__name__)

HELP_MESSAGE = (
    "❓ <b>QuizBotPro Help & Usage Guide</b>\n\n"
    "QuizBotPro lets you bulk-create native Telegram quiz polls quickly.\n\n"
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
    "• /template - Download sample Excel spreadsheet template\n"
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


async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send ready-to-use sample Excel spreadsheet template (.xlsx)."""
    if not update.effective_chat:
        return

    from app.extractors.sheet_extractor import generate_quiz_template_bytes

    buf_bytes = generate_quiz_template_bytes()
    caption = (
        "📥 <b>QuizBotPro Spreadsheet Template</b>\n\n"
        "Fill your quiz questions into this Excel sheet and upload it directly to QuizBotPro!\n\n"
        "<b>Supported Columns:</b>\n"
        "• <b>Question:</b> Question text (supports bilingual Hindi/English)\n"
        "• <b>Option A - D:</b> Answer choices\n"
        "• <b>Answer:</b> Correct option letter (e.g. <code>A</code>, <code>B</code>, <code>C</code>, <code>D</code>)\n"
        "• <b>Explanation:</b> Optional explanation\n\n"
        "<i>💡 Tip: You can also upload CSV files (.csv) formatted the same way!</i>"
    )

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=buf_bytes,
        filename="quiz_template.xlsx",
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
