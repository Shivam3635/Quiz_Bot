"""Start command handler."""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot.handlers.common import ADMIN_REQUIRED_MESSAGE, require_group_admin
from app.bot.keyboards.main import get_main_menu_keyboard
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

WELCOME_MESSAGE = (
    "👋 <b>Welcome to QuizBotPro!</b>\n\n"
    "<i>Create hundreds of Telegram quizzes in seconds.</i>\n\n"
    "Tired of typing quiz questions and choices one by one? "
    "With QuizBotPro, you simply paste your questions, configure settings once, and publish them all.\n\n"
    "Choose an action below to get started:"
)


@require_group_admin
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command and display the welcome menu."""
    user = update.effective_user
    chat = update.effective_chat
    logger.info("User %s (%s) triggered /start", user.id if user else "Unknown", user.username if user else "")

    # Check for deep link payload (e.g. /start quiz_5 or /startgroup quiz_5)
    args = context.args or []
    if args and args[0].startswith("quiz_"):
        quiz_id_str = args[0].replace("quiz_", "")
        if quiz_id_str.isdigit():
            quiz_id = int(quiz_id_str)
            if chat and chat.type in ("group", "supergroup"):
                try:
                    member = await chat.get_member(user.id)
                    if member.status not in ("creator", "administrator") and getattr(member.user, "username", "") != "GroupAnonymousBot":
                        if update.message:
                            await update.message.reply_text(ADMIN_REQUIRED_MESSAGE)
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

    # In groups/supergroups without a deep link payload: display group command guidance
    if chat and chat.type in ("group", "supergroup"):
        bot_username = context.bot.username or "my_bulk_quiz_bot"
        if update.message:
            await update.message.reply_text(
                "👋 <b>QuizBotPro Group Mode</b>\n\n"
                "To launch a quiz battle in this group, run:\n"
                "<code>/startquiz &lt;quiz_id&gt;</code>\n\n"
                f"To create quizzes or manage quiz sets, open a private chat: @{bot_username}",
                parse_mode=ParseMode.HTML,
            )
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

