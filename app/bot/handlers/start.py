"""Start command handler."""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot.keyboards.main import get_main_menu_keyboard
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

WELCOME_MESSAGE = (
    "👋 <b>Welcome to BulkQuiz!</b>\n\n"
    "<i>Create hundreds of Telegram quizzes in seconds.</i>\n\n"
    "Tired of typing quiz questions and choices one by one? "
    "With BulkQuiz, you simply paste your questions, configure settings once, and publish them all.\n\n"
    "Choose an action below to get started:"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command and display the welcome menu."""
    user = update.effective_user
    logger.info("User %s (%s) triggered /start", user.id if user else "Unknown", user.username if user else "")

    # Check for deep link payload (e.g. /start quiz_5 or /startgroup quiz_5)
    args = context.args or []
    if args and args[0].startswith("quiz_"):
        quiz_id_str = args[0].replace("quiz_", "")
        if quiz_id_str.isdigit():
            quiz_id = int(quiz_id_str)
            chat = update.effective_chat
            if chat and chat.type in ("group", "supergroup"):
                try:
                    member = await chat.get_member(user.id)
                    if member.status not in ("creator", "administrator"):
                        if update.message:
                            await update.message.reply_text("⚠️ Only group administrators can launch a quiz battle.")
                        return
                except Exception:
                    pass

                from app.bot.handlers.game import launch_game_lobby
                user_name = (user.username or user.first_name) if user else "Host"
                await launch_game_lobby(
                    chat_id=chat.id,
                    chat_title=chat.title,
                    quiz_set_id=quiz_id,
                    initiator_id=user.id if user else 0,
                    initiator_name=user_name,
                    bot=context.bot,
                    target_message=update.message,
                )
                return
            else:
                from app.bot.handlers.game import startquiz_command
                context.args = [str(quiz_id)]
                await startquiz_command(update, context)
                return

    if update.message:
        await update.message.reply_text(
            WELCOME_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            WELCOME_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
