"""Security middleware and perimeter gatekeepers for QuizBotPro."""

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.config.settings import get_settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)

STAGING_ACCESS_DENIED_TEXT = (
    "⛔ <b>Access Restricted</b>\n\n"
    "This is a private staging instance of QuizBotPro. You are not authorized to use this bot."
)

STAGING_ACCESS_DENIED_ALERT = (
    "⛔ Access Restricted: You are not authorized to use this staging bot."
)


async def staging_security_gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Perimeter gatekeeper for the staging environment.

    Executes in priority group -1 (before any command, conversation, callback query,
    or member tracking handler). Enforces strict allowlist authorization against
    STAGING_ALLOWED_USER_IDS.

    In non-staging environments (production, development, testing), updates pass through
    immediately without altering default behaviour.
    """
    settings = get_settings()

    # Pass through immediately for all non-staging environments
    if settings.ENVIRONMENT != "staging":
        return

    # Extract effective user across all Telegram update types
    user = update.effective_user
    user_id = user.id if user is not None else None

    # Fail closed on updates without a verifiable user
    if user is None or user_id is None or user_id <= 0:
        logger.warning(
            "Staging gatekeeper: Rejected update lacking verifiable user (update_id=%s)",
            getattr(update, "update_id", "unknown"),
        )
        raise ApplicationHandlerStop

    # Allow authorized users to proceed to downstream handlers
    if user_id in settings.STAGING_ALLOWED_USER_IDS:
        return

    # Unauthorized access attempt detected
    logger.warning(
        "Staging gatekeeper: Blocked unauthorized user_id=%s (username=%s) on update_id=%s",
        user_id,
        user.username or "None",
        getattr(update, "update_id", "unknown"),
    )

    # Deliver immediate rejection feedback to the unauthorized user
    # - Callback queries receive an alert
    # - Private chat messages receive an access-denied text message
    # - Group and supergroup messages are silently rejected to avoid chat noise
    try:
        if update.callback_query:
            await update.callback_query.answer(
                STAGING_ACCESS_DENIED_ALERT,
                show_alert=True,
            )
        elif (
            update.effective_chat
            and update.effective_chat.type == ChatType.PRIVATE
            and update.effective_message
        ):
            await update.effective_message.reply_text(
                STAGING_ACCESS_DENIED_TEXT,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.debug("Staging gatekeeper: Failed to send rejection feedback: %s", e)

    # CRITICAL: Terminate all further handler propagation for this update
    raise ApplicationHandlerStop

