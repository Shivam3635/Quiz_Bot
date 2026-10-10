"""Common handlers, helpers, and group admin permissions."""

from functools import wraps
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from app.utils.logger import setup_logger

logger = setup_logger(__name__)

ADMIN_REQUIRED_MESSAGE = "⚠️ You should be an admin to run this command!"


async def is_user_group_admin(bot, chat, user_id: int) -> bool:
    """Return True if in private chat or if user is group admin/creator."""
    if not chat:
        return True

    chat_type = getattr(chat, "type", None)
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        return True

    # Telegram GroupAnonymousBot (ID: 1087968824)
    if user_id == 1087968824:
        return True

    try:
        member = await chat.get_member(user_id)
        if member.status in ("creator", "administrator"):
            return True
        if getattr(member.user, "username", "") == "GroupAnonymousBot":
            return True
    except Exception as e:
        logger.warning(
            "Could not check admin status for user %s in chat %s: %s",
            user_id,
            getattr(chat, "id", "unknown"),
            e,
        )
    return False


async def check_group_admin_permission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Verify that if a command is triggered in a group, the sender is a group admin.

    Returns:
        True: Allowed to execute (private chat or group admin).
        False: Blocked (normal member in group); replies with admin-required notice.
    """
    chat = update.effective_chat
    user = update.effective_user

    if not chat:
        return True

    chat_type = getattr(chat, "type", None)
    # Only enforce admin restrictions inside group and supergroup chats
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP, "group", "supergroup"):
        return True

    user_id = user.id if user else None
    if not user_id:
        return False


    is_admin = await is_user_group_admin(context.bot, chat, user_id)
    if is_admin:
        return True

    # Normal member in group attempted to execute command
    if update.message:
        try:
            await update.message.reply_text(ADMIN_REQUIRED_MESSAGE)
        except Exception as e:
            logger.warning("Failed to send admin notice to chat %s: %s", chat.id, e)
    elif update.callback_query:
        try:
            await update.callback_query.answer(
                "You should be an admin to run this command!",
                show_alert=True,
            )
        except Exception:
            pass

    return False


def require_group_admin(func: Callable) -> Callable:
    """Decorator to enforce that bot commands in group chats can only be run by group admins."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        if not await check_group_admin_permission(update, context):
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


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

