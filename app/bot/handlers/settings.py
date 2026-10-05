import html
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.keyboards.settings import get_quiz_settings_config_keyboard, get_settings_keyboard
from app.bot.states import QuizCreationState
from app.database.database import SessionLocal
from app.database.repositories import (
    get_or_create_user,
    get_quiz_set_by_id,
    reconstruct_quiz_data,
    save_quiz_batch,
    update_quiz_set_settings,
)
from app.parser.models import QuizSettings
from app.services.session_service import session_manager
from app.services.telegram_service import TelegramService
from app.utils.logger import setup_logger

logger = setup_logger(__name__)
telegram_service = TelegramService()

TIMER_CYCLES = [None, 15, 30, 45, 60]
CONFIG_TIMER_CYCLES = [None, 15, 30, 45, 60, 120]



def format_settings_text(settings: QuizSettings) -> str:
    """Format settings review overview text."""
    title = f"<b>{settings.title}</b>" if settings.title else "<i>None (Untitled)</i>"
    desc = f"<b>{settings.description}</b>" if settings.description else "<i>None (No description)</i>"
    banner = "<b>ON</b> (Sends intro banner before polls)" if settings.header_banner_enabled else "<b>OFF</b>"
    anon = "<b>ON</b> (Voter identities hidden)" if settings.is_anonymous else "<b>OFF</b> (Public voters)"
    shuffle = "<b>ON</b>" if settings.shuffle_options else "<b>OFF</b>"
    expl = "<b>ON</b>" if settings.explanation_enabled else "<b>OFF</b>"
    timer = f"<b>{settings.time_limit} seconds</b>" if settings.time_limit else "<b>No limit</b>"
    dest = f"<b>{settings.channel_id}</b>" if settings.channel_id else "<b>This chat</b>"

    return (
        "⚙️ <b>Bulk Quiz Settings</b>\n\n"
        "Configure settings once — they will be applied to every question in your batch:\n\n"
        f"• 🏷️ Title: {title}\n"
        f"• 📝 Description: {desc}\n"
        f"• 📌 Channel Banner: {banner}\n"
        f"• 🕶️ Anonymous Polls: {anon}\n"
        f"• 🔀 Shuffle Options: {shuffle}\n"
        f"• 💡 Explanations: {expl}\n"
        f"• ⏱️ Quiz Timer: {timer}\n"
        f"• 📢 Destination: {dest}\n\n"
        "<i>Tap any button below to toggle or change:</i>"
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the settings configuration screen."""
    query = update.callback_query
    if query:
        await query.answer()

    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    context.user_data["quiz_settings"] = settings

    text = format_settings_text(settings)
    keyboard = get_settings_keyboard(settings)

    if query and query.message:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    return QuizCreationState.CONFIGURING_SETTINGS


async def toggle_anonymous_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle anonymity setting."""
    query = update.callback_query
    if query:
        await query.answer("Anonymity toggled.")
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.is_anonymous = not settings.is_anonymous
    context.user_data["quiz_settings"] = settings

    if query and query.message:
        await query.edit_message_text(
            format_settings_text(settings),
            reply_markup=get_settings_keyboard(settings),
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.CONFIGURING_SETTINGS


async def toggle_shuffle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle option shuffling."""
    query = update.callback_query
    if query:
        await query.answer("Shuffle toggled.")
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.shuffle_options = not settings.shuffle_options
    context.user_data["quiz_settings"] = settings

    if query and query.message:
        await query.edit_message_text(
            format_settings_text(settings),
            reply_markup=get_settings_keyboard(settings),
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.CONFIGURING_SETTINGS


async def toggle_explanation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle explanation visibility."""
    query = update.callback_query
    if query:
        await query.answer("Explanation toggled.")
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.explanation_enabled = not settings.explanation_enabled
    context.user_data["quiz_settings"] = settings

    if query and query.message:
        await query.edit_message_text(
            format_settings_text(settings),
            reply_markup=get_settings_keyboard(settings),
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.CONFIGURING_SETTINGS


async def cycle_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cycle between timer presets (None -> 15s -> 30s -> 45s -> 60s -> None)."""
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()

    current_val = settings.time_limit
    try:
        curr_idx = TIMER_CYCLES.index(current_val)
        next_idx = (curr_idx + 1) % len(TIMER_CYCLES)
    except ValueError:
        next_idx = 0

    settings.time_limit = TIMER_CYCLES[next_idx]
    context.user_data["quiz_settings"] = settings

    query = update.callback_query
    if query:
        await query.answer(f"Timer set to {settings.time_limit or 'Off'}s")
        if query.message:
            await query.edit_message_text(
                format_settings_text(settings),
                reply_markup=get_settings_keyboard(settings),
                parse_mode=ParseMode.HTML,
            )
    return QuizCreationState.CONFIGURING_SETTINGS


async def toggle_header_banner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle channel header banner on/off."""
    query = update.callback_query
    if query:
        await query.answer("Header banner toggled.")
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    settings.header_banner_enabled = not settings.header_banner_enabled
    context.user_data["quiz_settings"] = settings

    if query and query.message:
        await query.edit_message_text(
            format_settings_text(settings),
            reply_markup=get_settings_keyboard(settings),
            parse_mode=ParseMode.HTML,
        )
    return QuizCreationState.CONFIGURING_SETTINGS


async def prompt_set_title_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type a quiz title."""
    query = update.callback_query
    if query:
        await query.answer()
        settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back to Settings", callback_data="goto_settings")]]
        )
        curr_title = f"<code>{settings.title}</code>" if settings.title else "<i>None (Untitled)</i>"
        await query.edit_message_text(
            "🏷️ <b>Configure Quiz Title</b>\n\n"
            f"Current title: {curr_title}\n\n"
            "Send your desired quiz title below (e.g. <code>General Science Mock Test #12</code>).\n"
            "<i>(Send <code>none</code> to remove the title)</i>",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )
    context.user_data["waiting_for_title_text"] = True
    context.user_data.pop("waiting_for_desc_text", None)
    context.user_data.pop("waiting_for_channel_text", None)
    return QuizCreationState.CONFIGURING_SETTINGS


async def prompt_set_desc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type a quiz description."""
    query = update.callback_query
    if query:
        await query.answer()
        settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back to Settings", callback_data="goto_settings")]]
        )
        curr_desc = f"<code>{settings.description}</code>" if settings.description else "<i>None (No description)</i>"
        await query.edit_message_text(
            "📝 <b>Configure Quiz Description</b>\n\n"
            f"Current description: {curr_desc}\n\n"
            "Send your desired quiz description below (e.g. <code>Covers NCERT Class 10 Physics Chapter 1-3.</code>).\n"
            "<i>(Send <code>none</code> to remove the description)</i>",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )
    context.user_data["waiting_for_desc_text"] = True
    context.user_data.pop("waiting_for_title_text", None)
    context.user_data.pop("waiting_for_channel_text", None)
    return QuizCreationState.CONFIGURING_SETTINGS


async def prompt_channel_dest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type channel username or ID."""
    query = update.callback_query
    if query:
        await query.answer()
        cancel_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back to Settings", callback_data="goto_settings")]]
        )
        await query.edit_message_text(
            "📢 <b>Configure Target Channel or Group</b>\n\n"
            "Send the <b>@username</b> of your target channel or group (e.g. <code>@myquizchannel</code>).\n\n"
            "⚠️ <b>Important:</b> Ensure QuizBotPro is added as an <b>Administrator</b> with 'Post Messages' permissions in that channel.",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )
    context.user_data["waiting_for_channel_text"] = True
    context.user_data.pop("waiting_for_title_text", None)
    context.user_data.pop("waiting_for_desc_text", None)
    return QuizCreationState.CONFIGURING_SETTINGS


async def receive_channel_dest_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate and save user-entered channel destination or quiz title."""
    if not update.message or not update.message.text:
        return QuizCreationState.CONFIGURING_SETTINGS

    text = update.message.text.strip()
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()

    # Case A: Setting Quiz Title
    if context.user_data.get("waiting_for_title_text"):
        context.user_data.pop("waiting_for_title_text", None)
        if text.lower() in ("none", "clear", "remove", "-"):
            settings.title = None
            msg = "🏷️ <b>Quiz title removed.</b>"
        else:
            settings.title = text[:150]
            msg = f"🏷️ <b>Quiz Title Set:</b>\n\n<i>\"{settings.title}\"</i>"

        context.user_data["quiz_settings"] = settings
        back_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Continue in Settings", callback_data="goto_settings")]]
        )
        await update.message.reply_text(msg, reply_markup=back_kb, parse_mode=ParseMode.HTML)
        return QuizCreationState.CONFIGURING_SETTINGS

    # Case B: Setting Quiz Description
    if context.user_data.get("waiting_for_desc_text"):
        context.user_data.pop("waiting_for_desc_text", None)
        if text.lower() in ("none", "clear", "remove", "-"):
            settings.description = None
            msg = "📝 <b>Quiz description removed.</b>"
        else:
            settings.description = text[:500]
            msg = f"📝 <b>Quiz Description Set:</b>\n\n<i>\"{settings.description}\"</i>"

        context.user_data["quiz_settings"] = settings
        back_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Continue in Settings", callback_data="goto_settings")]]
        )
        await update.message.reply_text(msg, reply_markup=back_kb, parse_mode=ParseMode.HTML)
        return QuizCreationState.CONFIGURING_SETTINGS

    # Case B: Setting Channel Destination
    bot = context.bot
    valid, msg, chat_id = await telegram_service.validate_channel_access(bot, text)

    if valid:
        settings.channel_id = text
        context.user_data["quiz_settings"] = settings
        context.user_data.pop("waiting_for_channel_text", None)
        quiz_set_id = context.user_data.get("saved_quiz_set_id")

        if quiz_set_id:
            try:
                with SessionLocal() as db:
                    update_quiz_set_settings(db, quiz_set_id, settings)
            except Exception as e:
                logger.warning("Could not sync channel dest to DB: %s", e)

        continue_cb = f"edit_quiz_settings_{quiz_set_id}" if quiz_set_id else "goto_settings"
        back_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Continue in Settings", callback_data=continue_cb)]]
        )
        await update.message.reply_text(
            f"🎉 <b>Target Channel Set!</b>\n\n{msg}\nAll quizzes will be published to <b>{text}</b>.",
            reply_markup=back_kb,
            parse_mode=ParseMode.HTML,
        )
    else:
        retry_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔙 Back to Settings", callback_data="goto_settings")],
            ]
        )
        await update.message.reply_text(
            f"{msg}\n\nPlease verify that the bot is an admin in that channel, or type another @channelusername:",
            reply_markup=retry_kb,
            parse_mode=ParseMode.HTML,
        )

    return QuizCreationState.CONFIGURING_SETTINGS


def format_quiz_settings_text(settings: QuizSettings, q_count: int = 0) -> str:
    """Format comprehensive settings overview text."""
    title = f"<b>{html.escape(settings.title)}</b>" if settings.title else "<i>None (Untitled)</i>"
    desc = f"<b>{html.escape(settings.description)}</b>" if settings.description else "<i>None (No description)</i>"
    banner = "<b>ON ✅</b>" if settings.header_banner_enabled else "<b>OFF ❌</b>"
    anon = "<b>ON (Anonymous) 🕶️</b>" if settings.is_anonymous else "<b>OFF (Public) 👤</b>"
    shuffle = "<b>ON ✅</b>" if settings.shuffle_options else "<b>OFF ❌</b>"
    expl = "<b>ON ✅</b>" if settings.explanation_enabled else "<b>OFF ❌</b>"
    timer = f"<b>{settings.time_limit} seconds</b>" if settings.time_limit else "<b>No limit (Off)</b>"
    dest = f"<b>{settings.channel_id}</b>" if settings.channel_id else "<b>This chat (Default)</b>"

    q_line = f"• 📊 Questions: <code>{q_count}</code>\n" if q_count > 0 else ""

    return (
        "⚙️ <b>Quiz Settings</b>\n\n"
        f"• 🏷️ Title: {title}\n"
        f"• 📝 Description: {desc}\n"
        f"{q_line}"
        f"• ⏱️ Quiz Timer: {timer}\n"
        f"• 🔀 Shuffle Options: {shuffle}\n"
        f"• 🕶️ Mode: {anon}\n"
        f"• 💡 Explanations: {expl}\n"
        f"• 📌 Channel Banner: {banner}\n"
        f"• 📢 Destination: {dest}\n\n"
        "<i>Tap any button below to toggle or change:</i>"
    )


async def edit_quiz_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open settings configurator for an existing saved quiz."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    quiz_set_id = int(query.data.split("_")[-1])
    user_id = update.effective_user.id if update.effective_user else 0

    with SessionLocal() as db:
        quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
        if not quiz_set:
            await query.edit_message_text("⚠️ Quiz set not found.")
            return
        questions, settings = reconstruct_quiz_data(quiz_set)

    context.user_data["quiz_settings"] = settings
    context.user_data["saved_quiz_set_id"] = quiz_set_id

    text = format_quiz_settings_text(settings, q_count=len(questions))
    keyboard = get_quiz_settings_config_keyboard(settings, quiz_set_id=quiz_set_id)

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def config_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle settings toggle (timer, shuffle, anon, expl, banner)."""
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split("_")
    setting_type = parts[1]
    id_tag = "_".join(parts[2:])

    quiz_set_id = int(id_tag) if id_tag.isdigit() else context.user_data.get("saved_quiz_set_id")
    user_id = update.effective_user.id if update.effective_user else 0

    settings: Optional[QuizSettings] = context.user_data.get("quiz_settings")
    q_count = len(context.user_data.get("bulk_questions", []))

    if not settings and quiz_set_id:
        with SessionLocal() as db:
            quiz_set = get_quiz_set_by_id(db, quiz_set_id=quiz_set_id, telegram_id=user_id)
            if quiz_set:
                questions, settings = reconstruct_quiz_data(quiz_set)
                q_count = len(questions)

    if not settings:
        settings = QuizSettings()

    if setting_type == "timer":
        current_timer = settings.time_limit
        idx = CONFIG_TIMER_CYCLES.index(current_timer) if current_timer in CONFIG_TIMER_CYCLES else -1
        next_timer = CONFIG_TIMER_CYCLES[(idx + 1) % len(CONFIG_TIMER_CYCLES)]
        settings.time_limit = next_timer
        await query.answer(f"Timer: {next_timer}s" if next_timer else "Timer: Off")
    elif setting_type == "shuffle":
        settings.shuffle_options = not settings.shuffle_options
        await query.answer("Shuffle toggled")
    elif setting_type == "anon":
        settings.is_anonymous = not settings.is_anonymous
        await query.answer("Mode toggled")
    elif setting_type == "expl":
        settings.explanation_enabled = not settings.explanation_enabled
        await query.answer("Explanation toggled")
    elif setting_type == "banner":
        settings.header_banner_enabled = not settings.header_banner_enabled
        await query.answer("Header banner toggled")

    context.user_data["quiz_settings"] = settings

    if quiz_set_id:
        try:
            with SessionLocal() as db:
                update_quiz_set_settings(db, quiz_set_id, settings)
        except Exception as e:
            logger.warning("Could not sync settings to DB: %s", e)

    text = format_quiz_settings_text(settings, q_count=q_count)
    keyboard = get_quiz_settings_config_keyboard(settings, quiz_set_id=quiz_set_id)

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def config_dest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle destination toggle or prompt for settings config."""
    query = update.callback_query
    if query:
        await query.answer()

    parts = query.data.split("_")
    id_tag = parts[-1]
    quiz_set_id = int(id_tag) if id_tag.isdigit() else context.user_data.get("saved_quiz_set_id")

    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    if settings.channel_id:
        settings.channel_id = None
        context.user_data["quiz_settings"] = settings
        if quiz_set_id:
            with SessionLocal() as db:
                update_quiz_set_settings(db, quiz_set_id, settings)
        await query.answer("Destination reset to Default (This chat)")
        text = format_quiz_settings_text(settings)
        keyboard = get_quiz_settings_config_keyboard(settings, quiz_set_id=quiz_set_id)
        if query and query.message:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return QuizCreationState.CONFIGURING_SETTINGS

    cancel_cb = f"edit_quiz_settings_{quiz_set_id}" if quiz_set_id else "goto_settings"
    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back to Settings", callback_data=cancel_cb)]]
    )
    if query and query.message:
        await query.edit_message_text(
            "📢 <b>Configure Target Channel or Group</b>\n\n"
            "Send the <b>@username</b> of your target channel or group (e.g. <code>@myquizchannel</code>).\n\n"
            "💡 <i>Tip: Tap the button below to keep it as Default (This Chat).</i>",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )
    context.user_data["waiting_for_channel_text"] = True
    context.user_data["saved_quiz_set_id"] = quiz_set_id
    return QuizCreationState.CONFIGURING_SETTINGS


async def finish_creation_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Finish creation flow, persist any pending settings, clean up session, and display Quiz Card."""
    query = update.callback_query
    if query:
        await query.answer("Settings saved!")

    quiz_set_id = context.user_data.get("saved_quiz_set_id")
    settings: QuizSettings = context.user_data.get("quiz_settings") or QuizSettings()
    questions = context.user_data.get("bulk_questions") or []
    user = update.effective_user
    user_id = user.id if user else 0
    chat = update.effective_chat
    chat_id = chat.id if chat else 0

    if not quiz_set_id and questions:
        with SessionLocal() as db:
            user_rec = get_or_create_user(db, telegram_id=user_id, username=user.username if user else None)
            quiz_set = save_quiz_batch(
                db=db,
                user_id=user_rec.id,
                title=settings.title,
                description=settings.description,
                questions=questions,
                settings=settings,
            )
            quiz_set_id = quiz_set.id
    elif quiz_set_id:
        with SessionLocal() as db:
            update_quiz_set_settings(db, quiz_set_id, settings)

    # Discard creation session from session_manager and clear user_data
    from app.bot.handlers.bulk import clear_creation_session
    clear_creation_session(context, user_id, chat_id)

    # Show Quiz Details Card
    from app.bot.handlers.myquizzes import view_quiz_callback
    if quiz_set_id and query:
        query.data = f"view_quiz_{quiz_set_id}"
        await view_quiz_callback(update, context)

    return ConversationHandler.END

