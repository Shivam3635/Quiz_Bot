"""Help command and callback handler."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.utils.logger import setup_logger

logger = setup_logger(__name__)

HELP_MESSAGE = (
    "💡 <b>QuizBotPro Quick Help & Guidelines</b>\n\n"
    "Create, manage, and host hundreds of native Telegram quiz polls in seconds!\n\n"
    "<b>⚡ 3-Step Quickstart:</b>\n"
    "1️⃣ Run /newquiz or tap <b>📦 Bulk Create</b>\n"
    "2️⃣ Paste questions or upload files (Excel, Word, CSV, PDF)\n"
    "3️⃣ Tap <b>✅ Done</b>, configure your timer/channel, and tap <b>Publish</b>!\n\n"
    "<b>📄 Supported Input Formats:</b>\n"
    "• <b>Direct Text:</b> Q1, options (A, B, C, D), and answers (marked with ✅ or <code>Answer: B</code>)\n"
    "• <b>Spreadsheets:</b> Excel (<code>.xlsx</code>) and CSV (<code>.csv</code>)\n"
    "• <b>Documents:</b> Word (<code>.docx</code>) and PDF (<code>.pdf</code>)\n"
    "• <b>Bilingual:</b> English + Hindi questions automatically formatted cleanly\n\n"
    "<b>🚀 Essential Commands:</b>\n"
    "• /newquiz — Create a new bulk quiz set\n"
    "• /myquizzes — View saved quizzes & 1-click re-publish\n"
    "• /startquiz — Launch a live group quiz battle tournament\n"
    "• /template — Download sample Excel spreadsheet template\n"
    "• /guide — <b>Download Complete PDF User Guide & Manual</b>\n\n"
    "<i>📖 For comprehensive guidelines, full format examples, comparison with official @QuizBot, and pro tips, download the PDF guide below!</i>"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command or help callback button with clear, brief guidance."""
    help_keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📖 Download Complete PDF Guide", callback_data="action_download_guide")],
            [InlineKeyboardButton("📥 Sample Excel Template", callback_data="action_download_template")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="action_main_menu")],
        ]
    )

    if update.message:
        await update.message.reply_text(
            HELP_MESSAGE,
            reply_markup=help_keyboard,
            parse_mode=ParseMode.HTML,
        )
    elif update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                HELP_MESSAGE,
                reply_markup=help_keyboard,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.debug("Minor edit text exception: %s", e)


async def guide_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send comprehensive official QuizBotPro PDF User Guide."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    if update.callback_query:
        await update.callback_query.answer("Generating PDF guide...")

    from app.utils.guide_generator import build_user_guide_pdf_bytes

    pdf_bytes = build_user_guide_pdf_bytes()
    caption = (
        "📘 <b>QuizBotPro Official User Guide & Manual</b>\n\n"
        "Here is your complete guide containing:\n"
        "• What is QuizBotPro & Core Capabilities\n"
        "• Step-by-Step Creation Walkthrough\n"
        "• Supported Input Formats with Real Examples\n"
        "• In-Depth Comparison: QuizBotPro vs Official @QuizBot\n"
        "• Live Group Battle Tournaments & Admin Tips\n\n"
        "<i>💡 Tip: Keep this PDF handy for reference whenever creating quizzes!</i>"
    )

    await context.bot.send_document(
        chat_id=chat_id,
        document=pdf_bytes,
        filename="QuizBotPro_User_Guide.pdf",
        caption=caption,
        parse_mode=ParseMode.HTML,
    )


async def template_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send ready-to-use sample Excel spreadsheet template (.xlsx)."""
    if not update.effective_chat:
        return

    if update.callback_query:
        await update.callback_query.answer()

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

